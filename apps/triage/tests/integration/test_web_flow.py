"""End-to-end tests for the triage request flow.

Every test in ``TestCrashPaths`` corresponds to an exception the audit
reproduced against the running application.  Intake and consent tests live in
``apps/patients/tests/integration/``.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.triage.services import predictor

pytestmark = pytest.mark.django_db


def text_of(response) -> str:
    """Rendered page as a single lowercase line.

    Template indentation wraps prose across lines, so asserting on a phrase
    against the raw body fails for reasons that have nothing to do with the
    page being wrong.
    """
    return " ".join(response.content.decode().split()).lower()


@pytest.fixture
def known_symptoms() -> list[str]:
    return ["itching", "skin_rash", "nodal_skin_eruptions", "dischromic _patches"]


class TestCrashPaths:
    def test_get_predict_redirects_instead_of_returning_none(self, client):
        """``predict()`` had no return on GET, so Django raised
        "didn't return an HttpResponse object" - a 500 on any refresh or
        shared link (AUDIT 2.6).
        """
        response = client.get(reverse("predict"))
        assert response.status_code == 302
        assert response.url == reverse("symptoms")

    def test_post_with_no_symptoms_is_rejected_not_a_zero_division(self, client):
        """AUDIT 2.7: ZeroDivisionError from ``severity_sum / len(symptoms)``."""
        response = client.post(reverse("predict"), {"days": 3})
        assert response.status_code == 400
        assert b"Select at least one symptom" in response.content

    def test_post_with_an_unknown_symptom_is_rejected_not_a_key_error(self, client):
        """AUDIT 2.7: KeyError from indexing the symptom dict directly."""
        response = client.post(
            reverse("predict"), {"symptoms": ["fake_symptom"], "days": 3}
        )
        assert response.status_code == 400

    def test_post_without_days_is_rejected_not_a_type_error(self, client, known_symptoms):
        """AUDIT 2.7: TypeError from ``int(None)``."""
        response = client.post(reverse("predict"), {"symptoms": known_symptoms})
        assert response.status_code == 400
        assert b"how many days" in response.content.lower()

    def test_non_numeric_days_is_rejected(self, client, known_symptoms):
        response = client.post(
            reverse("predict"), {"symptoms": known_symptoms, "days": "abc"}
        )
        assert response.status_code == 400

    def test_negative_days_is_rejected(self, client, known_symptoms):
        response = client.post(
            reverse("predict"), {"symptoms": known_symptoms, "days": -4}
        )
        assert response.status_code == 400


class TestPredictFlow:
    def test_a_valid_check_renders_a_ranked_result(self, client, known_symptoms):
        response = client.post(
            reverse("predict"), {"symptoms": known_symptoms, "days": 3}
        )
        assert response.status_code == 200
        assert b"Possible conditions" in response.content

    def test_the_result_never_presents_itself_as_a_diagnosis(self, client, known_symptoms):
        """AUDIT 4.3: a definite diagnostic output pushes this toward being
        regulated medical device software.
        """
        response = client.post(
            reverse("predict"), {"symptoms": known_symptoms, "days": 3}
        )
        body = text_of(response)
        assert "not a diagnosis" in body
        assert "disease:" not in body

    def test_remedies_render_as_whole_items_not_single_letters(self, client):
        """AUDIT 2.9: the string-iteration bug produced one bullet per letter.

        Hyperthyroidism is the one disease with no remedies entry, so it is
        the case that used to break.
        """
        symptoms = [
            "fatigue", "mood_swings", "weight_loss", "restlessness", "sweating",
            "diarrhoea", "fast_heart_rate", "excessive_hunger", "muscle_weakness",
            "irritability", "abnormal_menstruation",
        ]
        response = client.post(reverse("predict"), {"symptoms": symptoms, "days": 5})
        body = response.content.decode()
        assert "<li>R</li>" not in body
        assert "<li>e</li>" not in body
        assert "no home remedies are recorded" in text_of(response)

    def test_a_perfect_pattern_match_is_not_worded_as_certainty(
        self, client, known_symptoms
    ):
        """A textbook presentation returns a unanimous forest vote on this
        synthetic dataset. Rendering that as "100% confident" would put back
        the false certainty the audit set out to remove, so the page leads
        with a qualitative band and labels the number a symptom match.
        """
        body = text_of(client.post(
            reverse("predict"), {"symptoms": known_symptoms, "days": 3}
        ))
        assert "strong match" in body
        assert "symptom match" in body
        for phrase in ("100% confident", "100% confidence", "100% accurate"):
            assert phrase not in body

    def test_a_result_page_is_safe_to_reload(self, client, known_symptoms):
        client.post(reverse("predict"), {"symptoms": known_symptoms, "days": 3})
        assert client.get(reverse("predict")).status_code == 302


class TestDisclosures:
    @pytest.mark.parametrize("route", ["home", "register", "symptoms", "privacy"])
    def test_every_page_carries_the_medical_disclaimer(self, client, route):
        """A search for disclaimer text across all templates previously
        returned zero matches (AUDIT 2.3).
        """
        body = text_of(client.get(reverse(route)))
        assert "not a substitute for professional medical advice" in body

    def test_the_intake_form_states_what_is_collected_and_why(self, client):
        body = text_of(client.get(reverse("register")))
        for phrase in ("what we collect", "why", "how long we keep it", "withdraw"):
            assert phrase in body

    def test_no_page_offers_medicines_to_a_patient(self, client, known_symptoms):
        """AUDIT 4.3: TPG 2020 prohibits an AI platform from prescribing."""
        response = client.post(
            reverse("predict"), {"symptoms": known_symptoms, "days": 3}
        )
        assert "does not recommend or prescribe medicines" in text_of(response)

    def test_no_accuracy_claim_is_displayed(self, client, known_symptoms):
        """The dataset cannot support an accuracy claim in either direction;
        see ml/artifacts/MODEL_CARD.md.
        """
        for route in ("home", "symptoms", "privacy"):
            body = text_of(client.get(reverse(route)))
            assert "100% accurate" not in body
            assert "accuracy" not in body


class TestSecurity:
    def test_admin_is_not_served_from_the_default_path(self, client):
        """AUDIT 8.10: the admin URL comes from the environment."""
        assert client.get("/admin/").status_code == 404

    def test_admin_is_reachable_at_the_configured_path(self, client, settings):
        """AUDIT 2.16: there was no admin route at all."""
        response = client.get(f"/{settings.ADMIN_URL}")
        assert response.status_code in (200, 302)

    def test_post_without_csrf_token_is_rejected(self, known_symptoms):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        response = strict.post(
            reverse("predict"), {"symptoms": known_symptoms, "days": 3}
        )
        assert response.status_code == 403


class TestModelUnavailable:
    def test_a_missing_artefact_renders_503_not_a_stack_trace(
        self, client, known_symptoms, monkeypatch
    ):
        def boom(_symptoms):
            raise predictor.ModelUnavailable("no artefact")

        monkeypatch.setattr(predictor, "predict", boom)
        response = client.post(
            reverse("predict"), {"symptoms": known_symptoms, "days": 3}
        )
        assert response.status_code == 503
        assert b"temporarily unavailable" in response.content.lower()
