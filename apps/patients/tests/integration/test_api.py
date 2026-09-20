"""Tests for the patient intake API."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.consent.models import ConsentArtefact
from apps.patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def payload() -> dict:
    return {
        "name": "API Person",
        "contact": "+91 98765 43210",
        "age": 34,
        "height_cm": 168,
        "weight_kg": 61,
        "consent": True,
    }


class TestIntakeEndpoint:
    def test_creates_patient_and_consent(self, client, payload):
        response = client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        assert response.status_code == 201
        assert Patient.objects.get().contact == "9876543210"
        assert ConsentArtefact.objects.get().is_active

    def test_never_returns_the_full_contact_number(self, client, payload):
        """An API that hands back complete mobile numbers is a bulk-extraction
        endpoint waiting to happen (AUDIT 8.8).
        """
        body = client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        ).json()
        assert "9876543210" not in str(body)
        assert body["contact"].endswith("3210")

    def test_rejects_missing_consent(self, client, payload):
        payload["consent"] = False
        response = client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        assert response.status_code == 400
        assert Patient.objects.count() == 0

    @pytest.mark.parametrize(
        "field,value",
        [
            ("name", "   "),
            ("contact", "12345"),
            ("contact", "1234567890"),
            ("age", 999),
            ("age", -1),
            ("height_cm", 5),
            ("weight_kg", 0),
        ],
    )
    def test_rejects_invalid_fields(self, client, payload, field, value):
        payload[field] = value
        response = client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        assert response.status_code == 400
        assert Patient.objects.count() == 0

    def test_no_row_is_written_when_consent_is_refused(self, client, payload):
        """Consent is checked before the row is written: a failure between the
        two would leave identified health data with no lawful basis.
        """
        payload["consent"] = False
        client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        assert Patient.objects.count() == 0
        assert ConsentArtefact.objects.count() == 0


class TestCurrentPatientEndpoint:
    def test_returns_null_without_a_session_record(self, client):
        body = client.get(reverse("patients:me")).json()
        assert body["patient"] is None

    def test_returns_the_session_record_with_its_consent(self, client, payload):
        client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        body = client.get(reverse("patients:me")).json()
        assert body["patient"]["name"] == "API Person"
        assert len(body["consent"]) == 1
        assert body["consent"][0]["is_active"] is True

    def test_delete_withdraws_consent_and_erases_the_patient(self, client, payload):
        client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        assert client.delete(reverse("patients:me")).status_code == 204
        assert Patient.objects.count() == 0
        assert ConsentArtefact.objects.get().is_withdrawn

    def test_delete_without_a_record_is_still_successful(self, client):
        """Idempotent: a client retrying an erasure must not get an error."""
        assert client.delete(reverse("patients:me")).status_code == 204

    def test_one_session_cannot_read_another_persons_record(self, client, payload):
        """The endpoint is session-scoped and takes no id, so there is no
        parameter to tamper with. Until the accounts app brings real
        authentication in Phase 4, that is what stands in for authorisation.
        """
        from django.test import Client

        client.post(
            reverse("patients:intake"), payload, content_type="application/json"
        )
        assert Patient.objects.count() == 1

        other = Client()
        assert other.get(reverse("patients:me")).json()["patient"] is None
