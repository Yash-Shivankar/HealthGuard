"""Business rules for consent.

Everything that grants, checks or withdraws consent goes through here, so
there is exactly one place to audit when someone asks how the DPDP obligations
are met (AUDIT 4.1).
"""

from __future__ import annotations

import logging

from apps.consent.models import CURRENT_NOTICE_VERSION, ConsentArtefact, ConsentPurpose
from apps.consent.repositories.consent_repository import ConsentRepository
from utils.exceptions.domain import ConsentRequiredError

logger = logging.getLogger(__name__)


class ConsentService:
    def __init__(self, repository: ConsentRepository | None = None):
        self.repository = repository or ConsentRepository()

    def grant(
        self,
        *,
        patient,
        purpose: str = ConsentPurpose.SYMPTOM_CHECK,
        notice_version: str | None = None,
    ) -> ConsentArtefact:
        artefact = self.repository.create(
            patient=patient,
            purpose=purpose,
            notice_version=notice_version or CURRENT_NOTICE_VERSION,
        )
        logger.info(
            "Consent granted",
            extra={
                "patient_id": patient.pk,
                "purpose": purpose,
                "notice_version": artefact.notice_version,
            },
        )
        return artefact

    def has_active(
        self, *, patient, purpose: str = ConsentPurpose.SYMPTOM_CHECK
    ) -> bool:
        if patient is None:
            return False
        return self.repository.active_for(patient, purpose) is not None

    def require_active(
        self, *, patient, purpose: str = ConsentPurpose.SYMPTOM_CHECK
    ) -> None:
        """Raise unless this patient has live consent for the purpose.

        A distinct exception type, not a generic validation error: withdrawn
        consent is the case that has to be visible in the audit trail.
        """
        if not self.has_active(patient=patient, purpose=purpose):
            raise ConsentRequiredError(
                "We do not have your consent for this. Please give consent "
                "again, or use the symptom checker without saving your details."
            )

    def withdraw_all(self, *, patient) -> int:
        count = self.repository.withdraw_active_for(patient)
        logger.info(
            "Consent withdrawn", extra={"patient_id": patient.pk, "artefacts": count}
        )
        return count

    def artefacts_for(self, *, patient):
        return self.repository.all_for(patient)
