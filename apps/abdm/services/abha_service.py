"""Linking a patient to an ABHA (AUDIT 4.2 M1).

The gateway call is the small part. What this service is actually for is the
four things around it that are easy to skip and expensive to retrofit:

**Consent, for this purpose specifically.** Consent to a symptom check is not
consent to link a national health identifier and share records through a
national network. The DPDP Rules require itemised notice per purpose, so a link
without ``ConsentPurpose.ABDM_LINKAGE`` is refused - not warned about.

**Idempotency.** ABHA creation is not a safe operation to repeat: a retry after
a timeout must not create a second identity for the same person. A patient has
at most one ``AbhaLink``, and a link that already exists is returned rather than
re-created.

**Audit.** Every call is recorded before the result is known, so a call that
times out still leaves a trace. A gap in the record is worst exactly when the
integration is least stable.

**Failing without losing the record.** A gateway failure leaves the link in
``FAILED`` with the reason, rather than rolling it away - so the front desk can
see that it was attempted and why it did not work, instead of wondering whether
they clicked the button.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.abdm.models import AbhaLink, RegistrationState
from apps.abdm.repositories.abdm_repository import AbhaRepository
from apps.abdm.services import endpoints
from apps.abdm.services.gateway import GatewayError, GatewayRequest, Transport, build_transport
from apps.abdm.services.recorder import GatewayRecorder
from apps.audit.models import AuditAction
from apps.audit.services.audit_service import AuditService
from apps.consent.models import ConsentPurpose
from apps.consent.services.consent_service import ConsentService
from utils.exceptions.domain import ConsentRequiredError, DomainError, ValidationError

logger = logging.getLogger(__name__)

ABHA_DIGITS = 14


class AbhaService:
    def __init__(
        self,
        transport: Transport | None = None,
        repository: AbhaRepository | None = None,
        consent: ConsentService | None = None,
        audit: AuditService | None = None,
        recorder: GatewayRecorder | None = None,
    ):
        self.transport = transport or build_transport()
        self.repository = repository or AbhaRepository()
        self.consent = consent or ConsentService()
        self.audit = audit or AuditService()
        self.recorder = recorder or GatewayRecorder()

    # --- Reads --------------------------------------------------------------
    def for_patient(self, patient) -> AbhaLink | None:
        return self.repository.for_patient(patient)

    # --- Create -------------------------------------------------------------
    def create(self, *, patient, actor=None, request=None) -> AbhaLink:
        """Create a new ABHA for someone who does not have one."""
        self._require_linkage_consent(patient)

        existing = self.repository.for_patient(patient)
        if existing is not None and existing.is_verified:
            # Idempotent: a retry after a timeout must not mint a second
            # national identity for the same person.
            return existing

        link = existing or self.repository.create(
            patient=patient,
            clinic=patient.clinic,
            linked_by=actor if _is_saved_user(actor) else None,
            consent_artefact=self._artefact_for(patient),
            state=RegistrationState.PENDING,
        )

        payload = endpoints.abha_create_payload(
            name=patient.name,
            mobile=patient.contact,
            year_of_birth=_year_of_birth(patient.age),
        )

        try:
            response = self._call(endpoints.ABHA_CREATE, payload, clinic=patient.clinic)
        except GatewayError as exc:
            self._mark_failed(link, exc.message)
            raise DomainError(
                "Could not create an ABHA right now. The attempt is recorded and "
                "can be retried.",
                code="abdm_unavailable",
            ) from exc

        link.abha_number = _digits(str(response.data.get("abhaNumber", "")))
        link.abha_address = str(response.data.get("abhaAddress", ""))
        link.mark_verified()
        self.repository.save(
            link, ["abha_number", "abha_address", "state", "verified_at", "last_error"]
        )

        self._record_audit(link, actor, request, "ABHA created")
        return link

    # --- Verify -------------------------------------------------------------
    def verify(self, *, patient, abha_number: str, actor=None, request=None) -> AbhaLink:
        """Link an ABHA the person already has."""
        self._require_linkage_consent(patient)

        digits = _digits(abha_number)
        if len(digits) != ABHA_DIGITS:
            raise ValidationError(f"An ABHA number has {ABHA_DIGITS} digits.")

        # A second patient record claiming the same ABHA is a data-quality
        # problem worth stopping at: two records for one national identity
        # cannot both be right, and merging them later is far harder.
        clash = self.repository.find_by_number(digits)
        if clash is not None and clash.patient_id != patient.pk:
            raise DomainError(
                "That ABHA is already linked to a different patient record.",
                code="abha_already_linked",
            )

        link = self.repository.for_patient(patient) or self.repository.create(
            patient=patient,
            clinic=patient.clinic,
            linked_by=actor if _is_saved_user(actor) else None,
            consent_artefact=self._artefact_for(patient),
            state=RegistrationState.PENDING,
        )

        try:
            response = self._call(
                endpoints.ABHA_VERIFY,
                endpoints.abha_verify_payload(abha_number=digits),
                clinic=patient.clinic,
            )
        except GatewayError as exc:
            self._mark_failed(link, exc.message)
            raise DomainError(
                "Could not verify that ABHA right now. The attempt is recorded "
                "and can be retried.",
                code="abdm_unavailable",
            ) from exc

        if not response.data.get("verified"):
            self._mark_failed(link, "The gateway did not recognise that ABHA number.")
            raise ValidationError("That ABHA number could not be verified.")

        link.abha_number = digits
        link.abha_address = str(response.data.get("abhaAddress", "")) or link.abha_address
        link.mark_verified()
        self.repository.save(
            link, ["abha_number", "abha_address", "state", "verified_at", "last_error"]
        )

        self._record_audit(link, actor, request, "ABHA verified and linked")
        return link

    # --- Unlink -------------------------------------------------------------
    def unlink(self, *, patient, actor=None, request=None) -> None:
        """Remove the local link.

        This deletes **our** record of the association. It does not delete the
        person's ABHA, which is theirs and lives in the national registry - and
        implying otherwise in the UI would be a lie worth avoiding.
        """
        link = self.repository.for_patient(patient)
        if link is None:
            return

        self.audit.record(
            action=AuditAction.DELETE,
            actor=actor,
            object_type="abdm.AbhaLink",
            object_id=str(link.pk),
            request=request,
            reason="ABHA unlinked",
        )
        link.delete()
        logger.info("ABHA link removed", extra={"event": "abha_unlinked", "patient_id": patient.pk})

    # --- Internals ----------------------------------------------------------
    def _require_linkage_consent(self, patient) -> None:
        if not self.consent.has_active(patient=patient, purpose=ConsentPurpose.ABDM_LINKAGE):
            raise ConsentRequiredError(
                "Linking an ABHA needs separate consent. Agreeing to a symptom "
                "check is not agreement to link a national health identifier.",
                code="abdm_consent_required",
            )

    def _artefact_for(self, patient):
        return self.consent.active_artefact(
            patient=patient, purpose=ConsentPurpose.ABDM_LINKAGE
        )

    def _call(self, endpoint, payload: dict, *, clinic):
        request = GatewayRequest(
            operation=endpoint.operation,
            method=endpoint.method,
            path=endpoint.path,
            payload=payload,
        )
        return self.recorder.send(self.transport, request, clinic=clinic)

    def _mark_failed(self, link: AbhaLink, reason: str) -> None:
        link.state = RegistrationState.FAILED
        link.last_error = reason[:500]
        self.repository.save(link, ["state", "last_error"])

    def _record_audit(self, link: AbhaLink, actor, request, reason: str) -> None:
        # By reference, never by value: the ABHA number itself must not enter
        # the access log, which outlives the patient record.
        self.audit.record(
            action=AuditAction.UPDATE,
            actor=actor,
            obj=link,
            request=request,
            reason=reason,
        )
        logger.info(
            "ABHA link updated",
            extra={
                "event": "abha_linked",
                "patient_id": link.patient_id,
                "state": link.state,
                "simulated": self.transport.simulated,
            },
        )


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _year_of_birth(age: int | None) -> int | None:
    if age is None:
        return None
    return timezone.now().year - int(age)


def _is_saved_user(actor) -> bool:
    return bool(actor and getattr(actor, "pk", None) and actor.is_authenticated)
