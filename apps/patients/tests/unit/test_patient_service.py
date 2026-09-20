"""Unit tests for PatientService.

These exercise the service directly, with no HTTP anywhere - which is only
possible because the layering actually holds. If a test here needs a request
object, the boundary has been broken (AUDIT 7.1).
"""

from __future__ import annotations

import pytest

from apps.consent.models import ConsentArtefact
from apps.patients.models import Patient
from apps.patients.services.patient_service import PatientService
from utils.exceptions.domain import NotFoundError, ValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture
def service() -> PatientService:
    return PatientService()


@pytest.fixture
def valid() -> dict:
    return {
        "name": "Service Person",
        "contact": "9876543210",
        "age": 41,
        "height_cm": 175,
        "weight_kg": 72,
        "consent_given": True,
    }


class TestRegister:
    def test_creates_patient_and_consent_together(self, service, valid):
        patient = service.register(**valid)
        assert patient.pk
        artefact = ConsentArtefact.objects.get()
        assert artefact.patient_id == patient.pk
        assert artefact.patient_ref == patient.pk

    def test_refuses_without_consent(self, service, valid):
        valid["consent_given"] = False
        with pytest.raises(ValidationError):
            service.register(**valid)

    def test_writes_nothing_when_consent_is_refused(self, service, valid):
        """Consent is checked before the row is written."""
        valid["consent_given"] = False
        with pytest.raises(ValidationError):
            service.register(**valid)
        assert Patient.objects.count() == 0

    def test_refuses_a_blank_name(self, service, valid):
        valid["name"] = "   "
        with pytest.raises(ValidationError):
            service.register(**valid)

    def test_normalises_the_contact_number(self, service, valid):
        valid["contact"] = "+91 98765 43210"
        assert service.register(**valid).contact == "9876543210"

    def test_refuses_a_non_mobile_number(self, service, valid):
        valid["contact"] = "1234567890"
        with pytest.raises(ValidationError):
            service.register(**valid)

    def test_records_the_notice_version(self, service, valid):
        service.register(**valid, notice_version="2026-01-01.9")
        assert ConsentArtefact.objects.get().notice_version == "2026-01-01.9"


class TestLookup:
    def test_get_raises_for_a_missing_id(self, service):
        with pytest.raises(NotFoundError):
            service.get(999999)

    def test_find_tolerates_a_missing_id(self, service):
        assert service.find(999999) is None

    def test_find_tolerates_none(self, service):
        assert service.find(None) is None


class TestForget:
    def test_erases_the_patient(self, service, valid):
        patient = service.register(**valid)
        service.forget(patient)
        assert Patient.objects.count() == 0

    def test_keeps_the_artefact_as_evidence(self, service, valid):
        """The artefact outlives the patient row on purpose (AUDIT 4.1)."""
        patient = service.register(**valid)
        service.forget(patient)

        artefact = ConsentArtefact.objects.get()
        assert artefact.is_withdrawn
        assert artefact.patient_id is None
        assert artefact.patient_ref


class TestPatientModel:
    def test_bmi_is_computed(self, service, valid):
        patient = service.register(**valid)
        # 72kg at 1.75m
        assert patient.bmi == pytest.approx(23.5, abs=0.1)

    def test_bmi_is_none_when_height_is_unusable(self):
        assert Patient(height_cm=0, weight_kg=70).bmi is None

    def test_str_masks_the_contact_number(self, service, valid):
        patient = service.register(**valid)
        assert "9876543210" not in str(patient)
