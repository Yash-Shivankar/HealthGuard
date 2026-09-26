"""Tests for the AI API (AUDIT 6, 7.1, 8.7).

The isolation checks are repeated here rather than trusted from
``apps/accounts``: the mixin is shared, but a view that forgets to use it is
exactly the bug those tests cannot catch. An intake draft holds a patient's own
description of their illness, which makes an unscoped list of them one of the
worse leaks available in this project.
"""

from __future__ import annotations

import io

import pytest
from django.urls import reverse

from apps.ai.models import DraftState, IntakeDraft
from apps.ai.services import providers
from apps.ai.services.extraction_service import ExtractionService
from apps.accounts.models import Clinic, Role
from apps.accounts.services.membership_service import MembershipService
from apps.patients.models import Patient
from apps.triage.models import ReviewState

pytestmark = pytest.mark.django_db

PASSWORD = "ai-api-pw"


@pytest.fixture(autouse=True)
def roles():
    from django.core.management import call_command

    call_command("seed_roles", verbosity=0)


@pytest.fixture
def clinics():
    return {
        "north": Clinic.objects.create(name="North Clinic"),
        "south": Clinic.objects.create(name="South Clinic"),
    }


@pytest.fixture
def patients(clinics):
    return {
        key: Patient.objects.create(
            clinic=clinic, name=f"{key.title()} Person",
            contact=f"98765000{index:02d}", age=30 + index,
            height_cm=165, weight_kg=60,
        )
        for index, (key, clinic) in enumerate(clinics.items(), start=10)
    }


@pytest.fixture
def doctors(clinics, django_user_model):
    made = {}
    for key, clinic in clinics.items():
        user = django_user_model.objects.create_user(
            username=f"doc-{key}", password=PASSWORD
        )
        MembershipService().grant(user=user, clinic=clinic, role=Role.DOCTOR)
        made[key] = user
    return made


@pytest.fixture
def drafts(clinics, patients):
    """One real (confirmable) draft per clinic."""
    service = ExtractionService(extractor=providers.LexiconExtractor())
    return {
        key: service.extract(
            text="I have itching and a skin rash for four days",
            language="en",
            patient=patients[key],
            clinic=clinics[key],
        )
        for key in clinics
    }


def sign_in(client, user):
    # force_login rather than login: django-axes wraps the authentication
    # backend and needs a request object, which the test client's login() does
    # not supply. Same approach as the ABDM API tests.
    client.force_login(user)
    return client


class TestDescribe:
    URL = reverse("ai:describe")

    def test_a_description_returns_proposed_symptoms(self, client):
        response = client.post(
            self.URL,
            {"text": "I have itching and a skin rash", "language": "en"},
            content_type="application/json",
        )
        assert response.status_code == 201
        names = [entry["symptom"] for entry in response.json()["proposed_symptoms"]]
        assert set(names) == {"itching", "skin_rash"}

    def test_the_evidence_travels_to_the_client(self, client):
        """A reviewer needs to see which words were read, or the review is a
        rubber stamp.
        """
        response = client.post(
            self.URL, {"text": "mujhe khujli hai", "language": "hi"},
            content_type="application/json",
        )
        entry = response.json()["proposed_symptoms"][0]
        assert entry["evidence"] == "khujli"
        assert entry["label"] == "Itching"

    def test_it_does_not_run_the_symptom_checker(self, client):
        """Extraction normalises language. Classification happens after a human
        confirms the list.
        """
        from apps.triage.models import TriageAssessment

        client.post(
            self.URL, {"text": "I have itching"}, content_type="application/json"
        )
        assert TriageAssessment.objects.count() == 0

    def test_the_response_names_no_condition(self, client):
        response = client.post(
            self.URL, {"text": "I have itching and a skin rash"},
            content_type="application/json",
        )
        body = response.json()
        assert "differential" not in body
        assert "disease" not in str(body).lower()

    def test_the_duration_is_offered_so_it_is_not_asked_twice(self, client):
        response = client.post(
            self.URL, {"text": "itching for four days"},
            content_type="application/json",
        )
        assert response.json()["suggested_days"] == 4

    def test_an_unrecognised_description_says_so(self, client):
        response = client.post(
            self.URL, {"text": "my tongue feels furry and metallic"},
            content_type="application/json",
        )
        body = response.json()
        assert body["understood_nothing"] is True
        assert body["unmapped_phrases"]

    def test_empty_text_is_a_400(self, client):
        response = client.post(self.URL, {"text": ""}, content_type="application/json")
        assert response.status_code == 400

    def test_a_bad_language_is_a_400(self, client):
        response = client.post(
            self.URL, {"text": "itching", "language": "klingon"},
            content_type="application/json",
        )
        assert response.status_code == 400


class TestVoice:
    URL = reverse("ai:voice")

    def test_a_recording_returns_a_draft(self, client):
        audio = io.BytesIO(b"pretend-this-is-audio")
        audio.name = "clip.webm"

        response = client.post(self.URL, {"audio": audio, "language": "hi"})
        assert response.status_code == 201
        assert response.json()["from_audio"] is True

    def test_the_draft_is_marked_simulated_with_no_vendor(self, client):
        """Which is the honest state of this endpoint today, and the client needs
        to know so it does not offer a confirm button that will fail.
        """
        audio = io.BytesIO(b"pretend-this-is-audio")
        audio.name = "clip.webm"

        body = client.post(self.URL, {"audio": audio}).json()
        assert body["simulated"] is True
        assert body["can_be_confirmed"] is False

    def test_a_missing_file_is_a_400(self, client):
        assert client.post(self.URL, {}).status_code == 400


class TestTheReviewQueueIsScoped:
    URL = reverse("ai:drafts")

    def test_a_doctor_sees_only_their_clinics_drafts(self, client, doctors, drafts):
        sign_in(client, doctors["north"])
        response = client.get(self.URL)

        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["results"]}
        assert ids == {drafts["north"].pk}

    def test_another_clinics_draft_is_not_listed(self, client, doctors, drafts):
        sign_in(client, doctors["north"])
        ids = {row["id"] for row in client.get(self.URL).json()["results"]}
        assert drafts["south"].pk not in ids

    def test_the_patients_words_do_not_leak_across_clinics(self, client, doctors, drafts):
        sign_in(client, doctors["north"])
        body = client.get(self.URL).content.decode()
        assert "South Person" not in body

    def test_an_anonymous_caller_is_refused(self, client, drafts):
        assert client.get(self.URL).status_code in (401, 403)

    def test_a_user_with_no_clinic_is_told_why(self, client, django_user_model, drafts):
        """An empty list would read as "no work to do" when it means "you are not
        attached to a clinic".
        """
        stray = django_user_model.objects.create_user(username="stray", password=PASSWORD)
        stray.is_staff = True
        stray.save()
        sign_in(client, stray)

        assert client.get(self.URL).status_code == 403


class TestConfirm:
    def url(self, draft):
        return reverse("ai:draft-confirm", args=[draft.pk])

    def test_a_doctor_can_confirm_their_clinics_draft(self, client, doctors, drafts):
        sign_in(client, doctors["north"])
        response = client.post(self.url(drafts["north"]), {}, content_type="application/json")

        assert response.status_code == 201
        drafts["north"].refresh_from_db()
        assert drafts["north"].state == DraftState.CONFIRMED

    def test_it_returns_the_assessment_still_unreviewed(self, client, doctors, drafts):
        """Two sign-offs for two claims: confirming the symptoms is not agreeing
        with the differential.
        """
        sign_in(client, doctors["north"])
        body = client.post(
            self.url(drafts["north"]), {}, content_type="application/json"
        ).json()
        assert body["review_state"] == ReviewState.UNREVIEWED

    def test_a_correction_is_honoured(self, client, doctors, drafts):
        sign_in(client, doctors["north"])
        response = client.post(
            self.url(drafts["north"]),
            {"symptoms": ["itching"], "days": 6},
            content_type="application/json",
        )
        assert response.status_code == 201
        drafts["north"].refresh_from_db()
        assert drafts["north"].confirmed_symptoms == ["itching"]

    def test_another_clinics_draft_is_a_404(self, client, doctors, drafts):
        """404 rather than 403: a 403 confirms the record exists."""
        sign_in(client, doctors["north"])
        response = client.post(
            self.url(drafts["south"]), {}, content_type="application/json"
        )
        assert response.status_code == 404

        drafts["south"].refresh_from_db()
        assert drafts["south"].state == DraftState.DRAFT

    def test_a_simulated_draft_is_refused_over_http(self, client, doctors, drafts):
        draft = drafts["north"]
        draft.simulated = True
        draft.save(update_fields=["simulated"])

        sign_in(client, doctors["north"])
        response = client.post(self.url(draft), {}, content_type="application/json")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "ai_simulated_cannot_be_confirmed"

    def test_an_unknown_symptom_is_a_400(self, client, doctors, drafts):
        sign_in(client, doctors["north"])
        response = client.post(
            self.url(drafts["north"]),
            {"symptoms": ["not_a_real_symptom"]},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_an_anonymous_caller_cannot_confirm(self, client, drafts):
        response = client.post(
            self.url(drafts["north"]), {}, content_type="application/json"
        )
        assert response.status_code in (401, 403)
        drafts["north"].refresh_from_db()
        assert drafts["north"].state == DraftState.DRAFT


class TestDiscard:
    def test_a_doctor_can_discard(self, client, doctors, drafts):
        sign_in(client, doctors["north"])
        url = reverse("ai:draft-discard", args=[drafts["north"].pk])

        response = client.post(
            url, {"reason": "not what the patient said"}, content_type="application/json"
        )
        assert response.status_code == 200
        assert response.json()["state"] == DraftState.DISCARDED

    def test_another_clinics_draft_is_a_404(self, client, doctors, drafts):
        sign_in(client, doctors["north"])
        url = reverse("ai:draft-discard", args=[drafts["south"].pk])
        assert client.post(url, {}, content_type="application/json").status_code == 404


class TestConsultationNotes:
    @pytest.fixture
    def assessment(self, client, doctors, drafts):
        sign_in(client, doctors["north"])
        client.post(
            reverse("ai:draft-confirm", args=[drafts["north"].pk]),
            {}, content_type="application/json",
        )
        drafts["north"].refresh_from_db()
        return drafts["north"].assessment

    def test_a_note_can_be_drafted_for_an_assessment(self, client, doctors, assessment):
        url = reverse("ai:note-draft", args=[assessment.pk])
        response = client.post(url, {}, content_type="application/json")

        assert response.status_code == 201
        assert response.json()["sections"]

    def test_the_sections_arrive_ordered_and_labelled(self, client, doctors, assessment):
        url = reverse("ai:note-draft", args=[assessment.pk])
        sections = client.post(url, {}, content_type="application/json").json()["sections"]

        assert sections[0]["label"] == "Presenting complaint"
        assert [s["label"] for s in sections] == [
            "Presenting complaint", "History", "Assessment", "Suggested next step"
        ]

    def test_the_note_carries_no_medication_advice(self, client, doctors, assessment):
        url = reverse("ai:note-draft", args=[assessment.pk])
        sections = client.post(url, {}, content_type="application/json").json()["sections"]
        text = " ".join(s["text"] for s in sections).lower()

        for word in ("paracetamol", "prescrib", "tablet", "mg "):
            assert word not in text

    def test_it_can_be_signed_off(self, client, doctors, assessment):
        draft_url = reverse("ai:note-draft", args=[assessment.pk])
        note_id = client.post(draft_url, {}, content_type="application/json").json()["id"]

        sign_url = reverse("ai:note-sign-off", args=[note_id])
        response = client.post(
            sign_url, {"clinician_text": "Rash is localised."},
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["state"] == DraftState.CONFIRMED

    def test_a_clinician_cannot_write_a_prescription_into_it(
        self, client, doctors, assessment
    ):
        draft_url = reverse("ai:note-draft", args=[assessment.pk])
        note_id = client.post(draft_url, {}, content_type="application/json").json()["id"]

        sign_url = reverse("ai:note-sign-off", args=[note_id])
        response = client.post(
            sign_url, {"clinician_text": "Prescribed paracetamol 500mg."},
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_another_clinics_assessment_is_a_404(self, client, doctors, drafts):
        """Scoped on the assessment, because the note does not exist yet."""
        from apps.triage.models import TriageAssessment

        theirs = TriageAssessment.objects.create(
            clinic=drafts["south"].clinic, patient=drafts["south"].patient,
            symptoms=["itching"], days=1,
        )
        sign_in(client, doctors["north"])
        url = reverse("ai:note-draft", args=[theirs.pk])
        assert client.post(url, {}, content_type="application/json").status_code == 404


class TestTheSchemaDescribesIt:
    def test_the_ai_endpoints_are_published(self, client):
        response = client.get(reverse("schema"))
        assert response.status_code == 200

        body = response.content.decode()
        assert "/api/v1/ai/intake/describe/" in body
        assert "/api/v1/ai/drafts/" in body

    def test_the_description_states_what_it_does_not_do(self, client):
        body = client.get(reverse("schema")).content.decode()
        assert "never a diagnosis" in body.lower() or "does **not** run" in body


class TestNothingLeavesTheBuildingByDefault:
    def test_no_call_is_recorded_as_external(self, client, patients):
        from apps.ai.models import AiCall

        client.post(
            reverse("ai:describe"),
            {"text": "I have itching"},
            content_type="application/json",
        )
        assert AiCall.objects.filter(external=True).count() == 0

    def test_a_draft_is_not_marked_as_externally_processed(self, client):
        client.post(
            reverse("ai:describe"),
            {"text": "I have itching"},
            content_type="application/json",
        )
        assert not IntakeDraft.objects.filter(externally_processed=True).exists()
