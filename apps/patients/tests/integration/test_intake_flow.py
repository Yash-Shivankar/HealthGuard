"""End-to-end tests for intake and consent withdrawal.

These moved here from the triage app in Phase 2, when ``UserInfo`` was split
into ``patients.Patient`` plus ``consent.ConsentArtefact``.  The assertions
about consent now check the artefact rather than columns on the patient row.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.consent.models import ConsentArtefact
from apps.patients.models import Patient

pytestmark = pytest.mark.django_db


def text_of(response) -> str:
    """Rendered page as a single lowercase line."""
    return " ".join(response.content.decode().split()).lower()


@pytest.fixture
def valid_intake() -> dict:
    return {
        "name": "Test Person",
        "contact": "9876543210",
        "age": 30,
        "height_cm": 170,
        "weight_kg": 65,
        "consent": "on",
    }


class TestIntake:
    def test_a_valid_submission_is_stored_in_the_database(self, client, valid_intake):
        """Not appended to data.csv, which is the root cause of AUDIT 2.1."""
        response = client.post(reverse("register"), valid_intake)
        assert response.status_code == 302
        patient = Patient.objects.get()
        assert patient.name == "Test Person"
        assert patient.contact == "9876543210"

    def test_consent_is_recorded_as_an_artefact(self, client, valid_intake):
        """A versioned, timestamped artefact - not a boolean (AUDIT 4.1)."""
        client.post(reverse("register"), valid_intake)
        artefact = ConsentArtefact.objects.get()
        assert artefact.is_active
        assert artefact.notice_version
        assert artefact.granted_at is not None
        assert artefact.patient_ref == Patient.objects.get().pk

    def test_submission_without_consent_is_rejected(self, client, valid_intake):
        del valid_intake["consent"]
        response = client.post(reverse("register"), valid_intake)
        assert response.status_code == 200
        assert Patient.objects.count() == 0
        assert ConsentArtefact.objects.count() == 0

    def test_server_side_validation_cannot_be_bypassed(self, client, valid_intake):
        """The old page validated in JavaScript only, which curl ignores
        entirely (AUDIT 2.7).
        """
        valid_intake |= {"age": 999, "contact": "12", "name": ""}
        response = client.post(reverse("register"), valid_intake)
        assert response.status_code == 200
        assert Patient.objects.count() == 0

    @pytest.mark.parametrize(
        "typed",
        ["+91 98765 43210", "098765 43210", "98765-43210", "9876543210"],
    )
    def test_contact_numbers_are_normalised(self, client, valid_intake, typed):
        valid_intake["contact"] = typed
        client.post(reverse("register"), valid_intake)
        assert Patient.objects.get().contact == "9876543210"

    def test_a_number_outside_the_indian_mobile_series_is_rejected(
        self, client, valid_intake
    ):
        valid_intake["contact"] = "1234567890"
        response = client.post(reverse("register"), valid_intake)
        assert response.status_code == 200
        assert Patient.objects.count() == 0

    def test_no_data_csv_is_written(self, client, valid_intake, settings):
        csv_path = settings.BASE_DIR / "data.csv"
        before = csv_path.stat().st_mtime if csv_path.exists() else None
        client.post(reverse("register"), valid_intake)
        after = csv_path.stat().st_mtime if csv_path.exists() else None
        assert before == after

    def test_contact_is_masked_in_string_representation(self, client, valid_intake):
        client.post(reverse("register"), valid_intake)
        patient = Patient.objects.get()
        assert "9876543210" not in str(patient)
        assert "3210" in str(patient)


class TestConsentWithdrawal:
    def test_consent_can_be_withdrawn_and_the_patient_erased(
        self, client, valid_intake
    ):
        """Withdrawal must be as easy as giving consent (AUDIT 4.1)."""
        client.post(reverse("register"), valid_intake)
        response = client.post(reverse("withdraw_consent"))
        assert response.status_code == 200
        assert Patient.objects.count() == 0

    def test_the_artefact_survives_erasure_as_evidence(self, client, valid_intake):
        """The artefact outlives the patient row on purpose: it is what shows
        the Data Protection Board that the erasure was requested and lawful.
        """
        client.post(reverse("register"), valid_intake)
        client.post(reverse("withdraw_consent"))

        artefact = ConsentArtefact.objects.get()
        assert artefact.is_withdrawn
        assert artefact.patient_id is None, "FK should be nulled, not cascaded"
        assert artefact.patient_ref, "the original row id must be retained"

    def test_withdrawal_page_handles_no_session_record(self, client):
        assert client.get(reverse("withdraw_consent")).status_code == 200

    def test_withdrawing_twice_is_harmless(self, client, valid_intake):
        client.post(reverse("register"), valid_intake)
        client.post(reverse("withdraw_consent"))
        assert client.post(reverse("withdraw_consent")).status_code == 200
        assert ConsentArtefact.objects.count() == 1
