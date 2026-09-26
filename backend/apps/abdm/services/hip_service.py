"""Sharing records under an ABDM consent (AUDIT 4.2 M2).

The Health Information Provider side: a HIU asks for records, a consent
artefact says whether that is allowed, and if it is we assemble a FHIR
document, encrypt it for the requester, and push it.

## The consent check is the point of this module

Everything else here is plumbing. The check is four separate questions, and
failing any one of them means no disclosure:

1. Is the consent **usable** - granted, not revoked, not expired? Expiry is
   evaluated live rather than trusted from the stored state, because a consent
   that lapsed five minutes ago still reads ``granted`` until some job notices.
2. Does it cover **this category** of information?
3. Does it cover **this record's date**?
4. Is the record **about the patient the consent belongs to**?

Question 4 is the one that looks redundant and is not. A consent authorises
disclosure of *one person's* records; a bug that assembled someone else's
document under a valid consent would be a cross-patient disclosure that every
other check waves through.

## What is verified here and what is not

The consent logic, the FHIR assembly and the encryption are tested. The gateway
envelope - how the encrypted payload is wrapped and which endpoint it goes to -
is the same unverified layer as the rest of the ABDM integration (AUDIT 11.3).
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.abdm.models import (
    HealthInformationTransfer,
    HieConsentArtefact,
    HiType,
    TransferState,
)
from apps.abdm.repositories.abdm_repository import TransferRepository
from apps.abdm.services import crypto
from apps.abdm.services.fhir_service import FhirService, bundle_sha256, canonical_json
from apps.abdm.services.gateway import GatewayError, GatewayRequest, Transport, build_transport
from apps.abdm.services.recorder import GatewayRecorder
from apps.audit.models import AuditAction
from apps.audit.services.audit_service import AuditService
from utils.exceptions.domain import ConsentRequiredError, DomainError

logger = logging.getLogger(__name__)

#: A triage summary is an outpatient consultation record.
#: ABDM-SPECIFIC: verify this is the category ABDM expects for it.
TRIAGE_HI_TYPE = HiType.OP_CONSULTATION


class HipService:
    def __init__(
        self,
        transport: Transport | None = None,
        fhir: FhirService | None = None,
        recorder: GatewayRecorder | None = None,
        audit: AuditService | None = None,
        transfers: TransferRepository | None = None,
    ):
        self.transport = transport or build_transport()
        self.fhir = fhir or FhirService()
        self.recorder = recorder or GatewayRecorder()
        self.audit = audit or AuditService()
        self.transfers = transfers or TransferRepository()

    # --- The gate -----------------------------------------------------------
    def check_consent(
        self, *, consent: HieConsentArtefact, assessment, hi_type: str = TRIAGE_HI_TYPE
    ) -> None:
        """Raise unless this consent permits disclosing this record.

        Separate from :meth:`share` so it can be called on its own - a UI that
        wants to grey out a button needs the answer without the side effects.
        """
        if not consent.is_usable:
            raise ConsentRequiredError(
                "That ABDM consent is not currently valid: it has been revoked, "
                "has expired, or was never granted.",
                code="abdm_consent_not_usable",
            )

        if not consent.covers(hi_type):
            raise ConsentRequiredError(
                f"That consent does not cover {hi_type}.",
                code="abdm_consent_scope",
            )

        if not consent.covers_date(assessment.created_at):
            raise ConsentRequiredError(
                "That consent does not cover records from this date.",
                code="abdm_consent_date_range",
            )

        # Looks redundant next to the checks above and is not: a consent
        # authorises disclosure of one person's records, and assembling someone
        # else's document under it would be a cross-patient disclosure that
        # every other check waves through.
        if assessment.patient_id != consent.patient_id:
            raise DomainError(
                "That consent belongs to a different patient.",
                code="abdm_consent_subject_mismatch",
            )

    # --- The transfer -------------------------------------------------------
    def share(
        self,
        *,
        consent: HieConsentArtefact,
        assessment,
        requester_public_key_b64: str,
        requester_nonce_b64: str,
        actor=None,
        request=None,
    ) -> HealthInformationTransfer:
        """Assemble, encrypt and push one assessment under a consent."""
        self.check_consent(consent=consent, assessment=assessment)

        abha_link = getattr(assessment.patient, "abha_link", None)
        bundle = self.fhir.build_assessment_document(
            assessment=assessment,
            abha_link=abha_link,
            practitioner=actor if _is_saved_user(actor) else None,
        )

        transfer = self.transfers.create(
            consent=consent,
            clinic=consent.clinic,
            entry_count=len(bundle["entry"]),
            # The hash, not the bundle. Storing the document would be a second
            # copy of clinical data outside the erasure path.
            bundle_sha256=bundle_sha256(bundle),
            state=TransferState.PENDING,
        )

        sender = crypto.generate_key_material()
        try:
            payload = crypto.encrypt(
                plaintext=canonical_json(bundle).encode("utf-8"),
                sender=sender,
                receiver_public_key_b64=requester_public_key_b64,
                receiver_nonce_b64=requester_nonce_b64,
            )
        except crypto.DecryptionError as exc:
            # Raised by the key-derivation step on a malformed peer key.
            return self._fail(transfer, f"Requester key rejected: {exc}")

        from apps.abdm.services import endpoints

        try:
            response = self.recorder.send(
                self.transport,
                GatewayRequest(
                    operation=endpoints.HI_TRANSFER.operation,
                    method=endpoints.HI_TRANSFER.method,
                    path=endpoints.HI_TRANSFER.path,
                    payload=endpoints.hi_transfer_payload(
                        consent_id=consent.consent_id,
                        transaction_id=str(transfer.pk),
                        encrypted=payload.as_dict(),
                    ),
                ),
                clinic=consent.clinic,
            )
        except GatewayError as exc:
            return self._fail(transfer, exc.message)

        transfer.transaction_id = str(response.data.get("transactionId", transfer.pk))
        transfer.state = TransferState.SENT
        transfer.sent_at = timezone.now()
        self.transfers.save(transfer, ["transaction_id", "state", "sent_at"])

        # A disclosure is exactly the event the access log exists for. Recorded
        # by reference: the consent id and the assessment id, never content.
        self.audit.record(
            action=AuditAction.EXPORT,
            actor=actor,
            obj=assessment,
            request=request,
            reason=f"shared under ABDM consent {consent.consent_id or consent.pk}",
        )
        logger.info(
            "Health information shared",
            extra={
                "event": "hi_transfer_sent",
                "consent_id": consent.pk,
                "assessment_id": assessment.pk,
                "entries": transfer.entry_count,
                "simulated": self.transport.simulated,
            },
        )
        return transfer

    def _fail(self, transfer: HealthInformationTransfer, reason: str):
        transfer.state = TransferState.FAILED
        transfer.last_error = reason[:500]
        self.transfers.save(transfer, ["state", "last_error"])
        raise DomainError(
            "Could not share the record right now. The attempt is recorded and "
            "can be retried.",
            code="abdm_transfer_failed",
        )


def _is_saved_user(actor) -> bool:
    return bool(actor and getattr(actor, "pk", None) and actor.is_authenticated)
