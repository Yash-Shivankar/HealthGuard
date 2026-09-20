"""Unit tests for ConsentService and the artefact model."""

from __future__ import annotations

import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.consent.models import CURRENT_NOTICE_VERSION, ConsentArtefact, ConsentPurpose
from apps.consent.services.consent_service import ConsentService
from apps.patients.models import Patient
from utils.exceptions.domain import ConsentRequiredError

pytestmark = pytest.mark.django_db


@pytest.fixture
def patient() -> Patient:
    return Patient.objects.create(
        name="Consent Person", contact="9876543210", age=30, height_cm=170, weight_kg=65
    )


@pytest.fixture
def service() -> ConsentService:
    return ConsentService()


class TestGrant:
    def test_records_the_current_notice_version_by_default(self, service, patient):
        artefact = service.grant(patient=patient)
        assert artefact.notice_version == CURRENT_NOTICE_VERSION
        assert artefact.is_active

    def test_an_explicit_version_is_preserved(self, service, patient):
        """Existing artefacts must keep pointing at the text their owner saw,
        which is the whole reason the version is stored (AUDIT 4.1).
        """
        artefact = service.grant(patient=patient, notice_version="2025-11-01.1")
        assert artefact.notice_version == "2025-11-01.1"

    def test_patient_ref_is_captured_at_grant_time(self, service, patient):
        assert service.grant(patient=patient).patient_ref == patient.pk


class TestHasActive:
    def test_true_after_granting(self, service, patient):
        service.grant(patient=patient)
        assert service.has_active(patient=patient)

    def test_false_before_granting(self, service, patient):
        assert not service.has_active(patient=patient)

    def test_false_after_withdrawal(self, service, patient):
        service.grant(patient=patient)
        service.withdraw_all(patient=patient)
        assert not service.has_active(patient=patient)

    def test_false_for_a_null_patient(self, service):
        assert not service.has_active(patient=None)

    def test_a_different_purpose_does_not_count(self, service, patient):
        """Purposes are itemised, never bundled: consent for one is not
        consent for another.
        """
        service.grant(patient=patient, purpose=ConsentPurpose.SYMPTOM_CHECK)
        assert not service.has_active(patient=patient, purpose="some_other_purpose")


class TestRequireActive:
    def test_passes_with_live_consent(self, service, patient):
        service.grant(patient=patient)
        service.require_active(patient=patient)

    def test_raises_a_distinct_error_without_it(self, service, patient):
        """Not a generic validation error: withdrawn consent is the case that
        has to be visible in the audit trail.
        """
        with pytest.raises(ConsentRequiredError) as exc:
            service.require_active(patient=patient)
        assert exc.value.status_code == 403
        assert exc.value.code == "consent_required"


class TestWithdraw:
    def test_withdraws_every_active_artefact(self, service, patient):
        service.grant(patient=patient)
        service.grant(patient=patient)
        assert service.withdraw_all(patient=patient) == 2
        assert ConsentArtefact.objects.filter(withdrawn_at__isnull=True).count() == 0

    def test_withdrawing_twice_changes_nothing(self, service, patient):
        service.grant(patient=patient)
        service.withdraw_all(patient=patient)
        assert service.withdraw_all(patient=patient) == 0

    def test_withdrawal_timestamp_is_not_overwritten(self, service, patient):
        service.grant(patient=patient)
        service.withdraw_all(patient=patient)
        first = ConsentArtefact.objects.get().withdrawn_at
        service.withdraw_all(patient=patient)
        assert ConsentArtefact.objects.get().withdrawn_at == first


class TestConstraints:
    def test_cannot_be_withdrawn_before_it_was_granted(self, patient):
        """A database-level check, not just application logic: this is
        evidence, and evidence should not be able to become incoherent.
        """
        now = timezone.now()
        with pytest.raises(IntegrityError):
            ConsentArtefact.objects.create(
                patient=patient,
                patient_ref=patient.pk,
                purpose=ConsentPurpose.SYMPTOM_CHECK,
                notice_version="x",
                granted_at=now,
                withdrawn_at=now - timezone.timedelta(days=1),
            )
