"""Tests for ambient consultation summarisation (AUDIT 6, Tier 1).

``TestTheMedicationGate`` is the reason this file exists. TPG 2020 forbids this
system prescribing, and a consultation note is the single most dangerous place to
break that rule: it is the artefact a busy clinician is most likely to sign
without reading closely, so a drug name inside one would carry the authority of
their signature.
"""

from __future__ import annotations

import pytest

from apps.ai.models import AiCall, AiCapability, ConsultationNote, DraftState
from apps.ai.services import providers
from apps.ai.services.summary_service import (
    MedicationContentError,
    SummaryService,
    assert_no_medication,
)
from apps.accounts.models import Clinic, Role
from apps.accounts.services.membership_service import MembershipService
from apps.patients.models import Patient
from apps.triage.models import TriageAssessment, UrgencyLevel
from utils.exceptions.domain import DomainError

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic():
    return Clinic.objects.create(name="Note Clinic")


@pytest.fixture
def patient(clinic):
    return Patient.objects.create(
        clinic=clinic, name="Note Person", contact="9876500002",
        age=52, height_cm=170, weight_kg=72,
    )


@pytest.fixture
def assessment(clinic, patient):
    return TriageAssessment.objects.create(
        clinic=clinic, patient=patient,
        symptoms=["itching", "skin_rash"], days=4,
        abstained=False,
        differential=[{"disease": "Fungal infection", "confidence": 0.79}],
        urgency_level=UrgencyLevel.ROUTINE,
    )


@pytest.fixture
def doctor(clinic, django_user_model):
    user = django_user_model.objects.create_user(username="note-doc", password="pw")
    MembershipService().grant(user=user, clinic=clinic, role=Role.DOCTOR)
    return user


@pytest.fixture
def service():
    return SummaryService()


class TestTheMedicationGate:
    """Refused, not flagged.

    A flag relies on somebody noticing it, and the whole premise of ambient
    documentation is that nobody is reading carefully.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "Prescribed paracetamol for the fever.",
            "Give 500mg twice daily.",
            "Start amoxicillin.",
            "Take two tablets after food.",
            "Ibuprofen as needed.",
            "Dose: 5 ml at night.",
            "Advised azithromycin 500 mg od",
            "T. Crocin sos",
            "10 units of insulin",
            "Antibiotics for a week.",
        ],
    )
    def test_medication_content_is_rejected(self, text):
        with pytest.raises(MedicationContentError):
            assert_no_medication(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Reports itching and a skin rash for four days.",
            "Triage disposition: book a consultation.",
            "Clinician to confirm findings and decide management.",
            "Symptom match 79%, not a probability of disease.",
            "The patient has had a fever of 102 for 4 days.",
            "",
        ],
    )
    def test_ordinary_clinical_text_passes(self, text):
        assert_no_medication(text)

    def test_the_error_has_its_own_type(self):
        """So a caller cannot mistake it for an ordinary validation failure and
        retry with the same input.
        """
        assert issubclass(MedicationContentError, DomainError)

    def test_even_a_negative_mention_is_refused(self):
        """"aspirin-free" still names a drug, and the gate fires.

        Deliberate. This note is assembled from structured triage data, so there
        is no legitimate reason for any drug name to appear in it in any form -
        which means the gate does not need to reason about clinical meaning, and
        should not try. A false refusal costs a clinician one retry.
        """
        with pytest.raises(MedicationContentError):
            assert_no_medication("The patient asked about aspirin-free options")

    def test_a_drug_name_inside_an_unrelated_word_does_not_trip_it(self):
        """The boundary still holds where it should: a gate that fired on every
        substring would refuse ordinary English.
        """
        assert_no_medication("The rash covers both aspirinlike patches")

    def test_the_gate_runs_on_the_deterministic_path_too(self, service, assessment):
        """Where it cannot fail by construction.

        A guard that only runs on the paths you expect to fail is not a guard,
        and the template is one prompt change away from not being deterministic.
        """
        note = service.draft_for(assessment=assessment)
        for _, text in note.ordered_sections():
            assert_no_medication(text)

    def test_a_prescribing_provider_is_refused_and_recorded(self, assessment):
        """The refusal is an outcome worth having a row for: a provider that
        keeps producing drug names should show up as failures, not silence.
        """

        class Prescribing:
            name = "bad-llm"
            simulated = False

            def summarise(self, *, facts):
                return providers.NoteDraft(
                    sections={"plan": "Start paracetamol 500mg twice daily."},
                    provider=self.name,
                    model_version="bad-1",
                    simulated=False,
                )

        with pytest.raises(MedicationContentError):
            SummaryService(summariser=Prescribing()).draft_for(assessment=assessment)

        assert ConsultationNote.objects.count() == 0
        call = AiCall.objects.get(capability=AiCapability.SUMMARISATION)
        assert call.succeeded is False

    def test_the_clinicians_own_additions_are_checked(self, service, assessment, doctor):
        """The same rule applied consistently. A clinician who genuinely needs to
        record a prescription should be doing it in a prescribing system with the
        safeguards for it, not in a field this app owns.
        """
        note = service.draft_for(assessment=assessment)
        with pytest.raises(MedicationContentError):
            service.sign_off(
                note=note, actor=doctor, clinician_text="Prescribed paracetamol."
            )


class TestTheDraft:
    def test_it_produces_a_note(self, service, assessment):
        note = service.draft_for(assessment=assessment)
        assert note.state == DraftState.DRAFT
        assert note.sections

    def test_the_sections_are_ordered_and_labelled(self, service, assessment):
        """A note is read top to bottom; a dict would leave the order to
        whatever the client parses into.
        """
        note = service.draft_for(assessment=assessment)
        labels = [label for label, _ in note.ordered_sections()]
        assert labels[0] == "Presenting complaint"
        assert labels[-1] == "Suggested next step"

    def test_there_is_no_medication_section(self, service, assessment):
        note = service.draft_for(assessment=assessment)
        assert "medication" not in note.sections

    def test_the_symptoms_are_readable_not_raw_feature_names(self, service, assessment):
        note = service.draft_for(assessment=assessment)
        complaint = note.sections["presenting_complaint"]
        assert "skin rash" in complaint
        assert "skin_rash" not in complaint
        # And it reads as a sentence rather than a list of labels.
        assert complaint.startswith("Reports itching and skin rash")

    def test_the_template_is_not_simulated(self, service, assessment):
        """It reads real recorded data, so a note from it can be signed."""
        note = service.draft_for(assessment=assessment)
        assert note.simulated is False
        assert note.can_be_confirmed is True

    def test_the_patients_words_appear_when_a_draft_exists(
        self, service, assessment, clinic, patient
    ):
        from apps.ai.services.extraction_service import ExtractionService

        intake = ExtractionService(extractor=providers.LexiconExtractor()).extract(
            text="mujhe khujli ho rahi hai", language="hi",
            patient=patient, clinic=clinic,
        )
        note = service.draft_for(assessment=assessment, intake_draft=intake)
        assert "mujhe khujli ho rahi hai" in note.sections["history"]

    def test_only_whitelisted_facts_reach_the_summariser(self, assessment):
        """What reaches a generator is what can appear in its output. Passing the
        ORM object would make that set whatever the model happens to have next
        month.
        """
        captured = {}

        class Capturing:
            name = "capture"
            simulated = False

            def summarise(self, *, facts):
                captured.update(facts)
                return providers.NoteDraft(sections={"plan": "ok"}, provider=self.name)

        SummaryService(summariser=Capturing()).draft_for(assessment=assessment)
        assert "contact" not in captured
        assert "patient" not in captured
        assert set(captured) <= {
            "symptoms", "days", "abstained", "differential",
            "urgency_level", "urgency_label",
            "source_text", "from_audio", "language", "language_label",
        }

    def test_persist_false_leaves_no_row(self, service, assessment):
        note = service.draft_for(assessment=assessment, persist=False)
        assert note.pk is None
        assert ConsultationNote.objects.count() == 0


class TestSignOff:
    def test_it_requires_an_actor(self, service, assessment):
        note = service.draft_for(assessment=assessment)
        with pytest.raises(DomainError) as exc:
            service.sign_off(note=note, actor=None)
        assert exc.value.code == "ai_signoff_needs_actor"

    def test_it_records_who_signed(self, service, assessment, doctor):
        note = service.draft_for(assessment=assessment)
        service.sign_off(note=note, actor=doctor)
        note.refresh_from_db()

        assert note.state == DraftState.CONFIRMED
        assert note.confirmed_by == doctor
        assert note.confirmed_at is not None

    def test_a_simulated_note_cannot_be_signed(self, service, assessment, doctor):
        note = service.draft_for(assessment=assessment)
        note.simulated = True
        note.save(update_fields=["simulated"])

        with pytest.raises(DomainError) as exc:
            service.sign_off(note=note, actor=doctor)
        assert exc.value.code == "ai_simulated_cannot_be_confirmed"

    def test_signing_twice_is_refused(self, service, assessment, doctor):
        note = service.draft_for(assessment=assessment)
        service.sign_off(note=note, actor=doctor)
        with pytest.raises(DomainError):
            service.sign_off(note=note, actor=doctor)

    def test_the_clinicians_edits_are_stored_separately(self, service, assessment, doctor):
        """So the AI draft and the human version stay distinguishable."""
        note = service.draft_for(assessment=assessment)
        service.sign_off(note=note, actor=doctor, clinician_text="Rash is localised.")
        note.refresh_from_db()

        assert note.clinician_text == "Rash is localised."
        assert "Rash is localised" not in str(note.sections)

    def test_it_is_written_to_the_access_log(self, service, assessment, doctor):
        from apps.audit.models import AccessLogEntry

        note = service.draft_for(assessment=assessment)
        AccessLogEntry.objects.all()._raw_delete(AccessLogEntry.objects.db)
        service.sign_off(note=note, actor=doctor)

        assert AccessLogEntry.objects.filter(reason__icontains="consultation note").exists()


class TestErasure:
    def test_a_note_goes_with_the_patient(self, service, assessment, patient):
        service.draft_for(assessment=assessment)
        assert ConsultationNote.objects.count() == 1
        patient.delete()
        assert ConsultationNote.objects.count() == 0

    def test_the_call_log_survives_but_holds_no_text(self, service, assessment, patient):
        import json

        service.draft_for(assessment=assessment)
        patient.delete()

        call = AiCall.objects.get(capability=AiCapability.SUMMARISATION)
        stored = json.dumps(
            {f.name: getattr(call, f.name) for f in AiCall._meta.fields}, default=str
        )
        assert "skin rash" not in stored
        assert call.model_version, "which version produced it must survive"
