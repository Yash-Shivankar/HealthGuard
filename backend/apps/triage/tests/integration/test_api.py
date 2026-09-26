"""Tests for the JSON triage API.

The API and the HTML page go through the same ``AssessmentService``, so these
tests are about the contract - status codes, envelope shape, and the promises
the response makes about what it is and is not.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.triage.models import ReviewState, TriageAssessment
from apps.triage.services import predictor

pytestmark = pytest.mark.django_db


@pytest.fixture
def known_symptoms() -> list[str]:
    return ["itching", "skin_rash", "nodal_skin_eruptions", "dischromic _patches"]


@pytest.fixture
def check_url() -> str:
    return reverse("triage:check")


class TestSymptomList:
    def test_lists_the_model_vocabulary(self, client):
        response = client.get(reverse("triage:symptoms"))
        assert response.status_code == 200
        data = response.json()
        assert len(data) == len(predictor.available_symptoms())
        assert {"value", "label"} <= data[0].keys()

    def test_labels_are_human_readable(self, client):
        data = client.get(reverse("triage:symptoms")).json()
        labels = {row["value"]: row["label"] for row in data}
        assert labels["foul_smell_of urine"] == "Foul smell of urine"


class TestTriageCheck:
    def test_a_valid_check_returns_a_ranked_differential(
        self, client, check_url, known_symptoms
    ):
        response = client.post(
            check_url,
            {"symptoms": known_symptoms, "days": 3},
            content_type="application/json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["abstained"] is False
        assert len(body["candidates"]) >= 1
        confidences = [c["confidence"] for c in body["candidates"]]
        assert confidences == sorted(confidences, reverse=True)

    def test_the_response_carries_a_disclaimer(self, client, check_url, known_symptoms):
        """A client rendering this data has the same obligation we do
        (AUDIT 4.3).
        """
        body = client.post(
            check_url,
            {"symptoms": known_symptoms, "days": 3},
            content_type="application/json",
        ).json()
        assert "not a diagnosis" in body["disclaimer"].lower()
        assert "does not recommend medicines" in body["disclaimer"].lower()

    def test_candidates_carry_a_match_band_not_bare_certainty(
        self, client, check_url, known_symptoms
    ):
        body = client.post(
            check_url,
            {"symptoms": known_symptoms, "days": 3},
            content_type="application/json",
        ).json()
        assert body["candidates"][0]["match_band"] in {
            "Strong match",
            "Possible match",
            "Weak match",
        }

    def test_every_result_is_recorded_as_unreviewed(
        self, client, check_url, known_symptoms
    ):
        """TPG 2020 requires clinician sign-off before an AI output enters a
        record (AUDIT 4.3, 8.3).
        """
        body = client.post(
            check_url,
            {"symptoms": known_symptoms, "days": 3},
            content_type="application/json",
        ).json()
        assert body["review_state"] == ReviewState.UNREVIEWED
        assert TriageAssessment.objects.get().needs_review

    def test_the_result_records_its_model_provenance(
        self, client, check_url, known_symptoms
    ):
        body = client.post(
            check_url,
            {"symptoms": known_symptoms, "days": 3},
            content_type="application/json",
        ).json()
        assert body["model_fingerprint"] == predictor.artefact().data_fingerprint

    def test_a_vague_symptom_abstains_rather_than_guessing(self, client, check_url):
        body = client.post(
            check_url,
            {"symptoms": ["fatigue"], "days": 2},
            content_type="application/json",
        ).json()
        assert body["abstained"] is True
        assert body["candidates"] == []
        assert "doctor" in body["urgency"]["advice"].lower()


class TestTriageCheckValidation:
    """The three crash paths from AUDIT 2.7, at the API boundary."""

    def test_empty_symptoms_is_a_400_not_a_zero_division(self, client, check_url):
        response = client.post(
            check_url, {"symptoms": [], "days": 3}, content_type="application/json"
        )
        assert response.status_code == 400

    def test_unknown_symptom_is_a_400_not_a_key_error(self, client, check_url):
        response = client.post(
            check_url,
            {"symptoms": ["not_a_symptom"], "days": 3},
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "not_a_symptom" in response.content.decode()

    def test_missing_days_is_a_400_not_a_type_error(
        self, client, check_url, known_symptoms
    ):
        response = client.post(
            check_url, {"symptoms": known_symptoms}, content_type="application/json"
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("days", [0, -1, 100000, "abc"])
    def test_out_of_range_days_is_rejected(
        self, client, check_url, known_symptoms, days
    ):
        response = client.post(
            check_url,
            {"symptoms": known_symptoms, "days": days},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_errors_use_the_standard_envelope(self, client, check_url):
        body = client.post(
            check_url, {"symptoms": [], "days": 3}, content_type="application/json"
        ).json()
        assert "error" in body
        assert {"code", "message"} <= body["error"].keys()

    def test_a_missing_model_returns_503_not_a_stack_trace(
        self, client, check_url, known_symptoms, monkeypatch
    ):
        def boom(_symptoms):
            raise predictor.ModelUnavailable("no artefact")

        monkeypatch.setattr(predictor, "predict", boom)
        response = client.post(
            check_url,
            {"symptoms": known_symptoms, "days": 3},
            content_type="application/json",
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "service_unavailable"


class TestObservability:
    def test_every_response_carries_a_request_id(self, client):
        response = client.get(reverse("triage:symptoms"))
        assert response["X-Request-ID"]

    def test_an_upstream_request_id_is_honoured(self, client):
        response = client.get(
            reverse("triage:symptoms"), HTTP_X_REQUEST_ID="trace-abc-123"
        )
        assert response["X-Request-ID"] == "trace-abc-123"

    def test_a_hostile_request_id_is_sanitised(self, client):
        """It ends up in log lines, so it must not carry newlines or control
        characters that would let a caller forge entries.
        """
        response = client.get(
            reverse("triage:symptoms"), HTTP_X_REQUEST_ID="bad\nINFO forged"
        )
        assert "\n" not in response["X-Request-ID"]
        assert " " not in response["X-Request-ID"]
