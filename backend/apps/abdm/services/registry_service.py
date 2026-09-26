"""Facility (HFR) and practitioner (HPR) registration (AUDIT 4.2 M1).

Both are the same shape: ask the gateway for an id, store it, record the
attempt. They are kept in one module because splitting two near-identical
twenty-line services across two files would be structure for its own sake.

Like the ABHA service, these are idempotent - a clinic already registered is
returned rather than registered again, because a second facility id for one
clinic is worse than none.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.abdm.models import FacilityRegistration, PractitionerRegistration, RegistrationState
from apps.abdm.repositories.abdm_repository import FacilityRepository, PractitionerRepository
from apps.abdm.services import endpoints
from apps.abdm.services.gateway import GatewayError, GatewayRequest, Transport, build_transport
from apps.abdm.services.recorder import GatewayRecorder
from utils.exceptions.domain import DomainError

logger = logging.getLogger(__name__)


class _RegistryServiceBase:
    def __init__(
        self,
        transport: Transport | None = None,
        recorder: GatewayRecorder | None = None,
    ):
        self.transport = transport or build_transport()
        self.recorder = recorder or GatewayRecorder()

    def _call(self, endpoint, payload: dict, *, clinic):
        return self.recorder.send(
            self.transport,
            GatewayRequest(
                operation=endpoint.operation,
                method=endpoint.method,
                path=endpoint.path,
                payload=payload,
            ),
            clinic=clinic,
        )


class FacilityService(_RegistryServiceBase):
    """Health Facility Registry."""

    def __init__(self, repository: FacilityRepository | None = None, **kwargs):
        super().__init__(**kwargs)
        self.repository = repository or FacilityRepository()

    def registration_for(self, clinic) -> FacilityRegistration | None:
        return self.repository.for_clinic(clinic)

    def register(self, *, clinic) -> FacilityRegistration:
        registration, _ = self.repository.get_or_create(clinic)

        if registration.is_verified:
            # A second facility id for one clinic is worse than none.
            return registration

        registration.state = RegistrationState.PENDING
        registration.requested_at = timezone.now()
        self.repository.save(registration, ["state", "requested_at"])

        payload = endpoints.hfr_register_payload(
            name=clinic.name,
            contact_email=clinic.contact_email,
            contact_phone=clinic.contact_phone,
        )

        try:
            response = self._call(endpoints.HFR_REGISTER, payload, clinic=clinic)
        except GatewayError as exc:
            registration.state = RegistrationState.FAILED
            registration.last_error = exc.message[:500]
            self.repository.save(registration, ["state", "last_error"])
            raise DomainError(
                "Could not register the facility right now. The attempt is "
                "recorded and can be retried.",
                code="abdm_unavailable",
            ) from exc

        registration.hfr_id = str(response.data.get("facilityId", ""))
        registration.state = RegistrationState.VERIFIED
        registration.verified_at = timezone.now()
        registration.last_error = ""
        self.repository.save(registration, ["hfr_id", "state", "verified_at", "last_error"])

        # Clinic.hfr_facility_id is the denormalised current value, so the
        # clinic record reads without a join. This service is the only writer
        # of either, which is what keeps them in step.
        clinic.hfr_facility_id = registration.hfr_id
        clinic.save(update_fields=["hfr_facility_id", "updated_at"])

        logger.info(
            "Facility registered with HFR",
            extra={"event": "hfr_registered", "clinic_id": clinic.pk,
                   "simulated": self.transport.simulated},
        )
        return registration


class PractitionerService(_RegistryServiceBase):
    """Healthcare Professional Registry."""

    def __init__(self, repository: PractitionerRepository | None = None, **kwargs):
        super().__init__(**kwargs)
        self.repository = repository or PractitionerRepository()

    def registration_for(self, user, clinic) -> PractitionerRegistration | None:
        return self.repository.for_user_and_clinic(user, clinic)

    def register(self, *, user, clinic) -> PractitionerRegistration:
        registration, _ = self.repository.get_or_create(user, clinic)

        if registration.is_verified:
            return registration

        registration.state = RegistrationState.PENDING
        self.repository.save(registration, ["state"])

        payload = endpoints.hpr_register_payload(
            name=user.get_full_name() or user.get_username(),
            email=getattr(user, "email", "") or "",
        )

        try:
            response = self._call(endpoints.HPR_REGISTER, payload, clinic=clinic)
        except GatewayError as exc:
            registration.state = RegistrationState.FAILED
            registration.last_error = exc.message[:500]
            self.repository.save(registration, ["state", "last_error"])
            raise DomainError(
                "Could not register the practitioner right now. The attempt is "
                "recorded and can be retried.",
                code="abdm_unavailable",
            ) from exc

        registration.hpr_id = str(response.data.get("hprId", ""))
        registration.state = RegistrationState.VERIFIED
        registration.verified_at = timezone.now()
        registration.last_error = ""
        self.repository.save(registration, ["hpr_id", "state", "verified_at", "last_error"])

        logger.info(
            "Practitioner registered with HPR",
            extra={"event": "hpr_registered", "clinic_id": clinic.pk,
                   "simulated": self.transport.simulated},
        )
        return registration
