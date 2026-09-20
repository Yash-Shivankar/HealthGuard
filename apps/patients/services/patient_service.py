"""Business rules for patient intake.

No ORM here beyond what the repository exposes, and no HTTP at all: this is
callable from a view, a DRF viewset, a management command or a Celery task
without changing (AUDIT 7.1).
"""

from __future__ import annotations

import logging

from apps.consent.services.consent_service import ConsentService
from apps.patients.models import Patient
from apps.patients.repositories.patient_repository import PatientRepository
from utils.exceptions.domain import NotFoundError, ValidationError
from utils.validators.phone import normalise_mobile

logger = logging.getLogger(__name__)


class PatientService:
    """Intake and lookup."""

    def __init__(
        self,
        repository: PatientRepository | None = None,
        consent_service: ConsentService | None = None,
    ):
        # Injected so tests can substitute doubles without patching imports.
        self.repository = repository or PatientRepository()
        self.consent_service = consent_service or ConsentService()

    def register(
        self,
        *,
        name: str,
        contact: str,
        age: int,
        height_cm: float,
        weight_kg: float,
        consent_given: bool,
        notice_version: str | None = None,
    ) -> Patient:
        """Create a patient record and the consent artefact that permits it.

        Consent is checked *before* the row is written, not after.  Storing the
        person first and recording consent second would mean a failure between
        the two leaves identified health data with no lawful basis attached.
        """
        if not consent_given:
            raise ValidationError(
                "We cannot continue without your consent. Please tick the box to agree."
            )

        clean_name = (name or "").strip()
        if not clean_name:
            raise ValidationError("Please enter your name.")

        clean_contact = normalise_mobile(contact)

        patient = self.repository.create(
            name=clean_name,
            contact=clean_contact,
            age=age,
            height_cm=height_cm,
            weight_kg=weight_kg,
        )
        self.consent_service.grant(patient=patient, notice_version=notice_version)

        logger.info("Patient registered", extra={"patient_id": patient.pk})
        return patient

    def get(self, patient_id: int) -> Patient:
        patient = self.repository.get_by_id(patient_id)
        if patient is None:
            raise NotFoundError("No such patient record.")
        return patient

    def find(self, patient_id: int | None) -> Patient | None:
        """Lookup that tolerates a missing or stale id, for session use."""
        if not patient_id:
            return None
        return self.repository.get_by_id(patient_id)

    def forget(self, patient: Patient) -> None:
        """Erase a patient after consent withdrawal.

        Withdrawal must be as easy as giving consent (AUDIT 4.1).  The consent
        artefact is marked withdrawn first and deliberately outlives the
        patient row: it is the evidence that the erasure was lawful and
        requested, and deleting it would destroy the audit trail the Data
        Protection Board would ask for.
        """
        self.consent_service.withdraw_all(patient=patient)
        patient_id = patient.pk
        self.repository.delete(patient)
        logger.info("Patient erased after consent withdrawal", extra={"patient_id": patient_id})
