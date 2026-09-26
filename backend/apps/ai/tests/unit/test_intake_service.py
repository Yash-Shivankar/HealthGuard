"""Tests for clinician sign-off and the hand-off to triage (AUDIT 4.3, 6).

This is the safety-critical file in Phase 7. Everything the AI app produces is a
draft, and this service is the only path by which a draft becomes part of a
patient's record. TPG 2020 requires a clinician in that position; these tests are
what establish the clinician cannot be skipped.

``TestSimulatedDraftsCannotBeConfirmed`` and ``TestTwoSignOffsForTwoClaims`` are
the two sets that matter most.
"""

from __future__ import annotations

import pytest

from apps.ai.models import DraftState, IntakeDraft
from apps.ai.services.extraction_service import ExtractionService
from apps.ai.services.intake_service import DEFAULT_DAYS, IntakeService
from apps.ai.services.providers import LexiconExtractor
from apps.accounts.models import Clinic, Role
from apps.accounts.services.membership_service import MembershipService
from apps.patients.models import Patient
from apps.triage.models import ReviewState
from utils.exceptions.domain import DomainError, ValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic():
    return Clinic.objects.create(name="AI Clinic")


@pytest.fixture
def patient(clinic):
    return Patient.objects.create(
        clinic=clinic, name="Draft Person", contact="9876500001",
        age=34, height_cm=165, weight_kg=60,
    )


@pytest.fixture
def doctor(clinic, django_user_model):
    user = django_user_model.objects.create_user(username="ai-doc", password="pw")
    MembershipService().grant(user=user, clinic=clinic, role=Role.DOCTOR)
    return user


@pytest.fixture
def service():
    return IntakeService()


@pytest.fixture
def real_draft(clinic, patient):
    """A draft from the lexicon extractor, which is a real provider."""
    return ExtractionService(extractor=LexiconExtractor()).extract(
        text="I have had itching and a skin rash for four days",
        language="en",
        patient=patient,
        clinic=clinic,
    )


class TestSimulatedDraftsCannotBeConfirmed:
    """The hardest rule in the app.

    With no speech vendor contracted, the default transcriber returns canned
    text. A canned transcript is words the patient never said, and no amount of
    clinician agreement makes fabricated content safe to file. So the refusal is
    unconditional rather than a warning the reviewer can accept.
    """

    def test_a_simulated_draft_is_refused(self, service, real_draft, doctor):
        real_draft.simulated = True
        real_draft.save(update_fields=["simulated"])

        with pytest.raises(DomainError) as exc:
            service.confirm(draft=real_draft, actor=doctor)
        assert exc.value.code == "ai_simulated_cannot_be_confirmed"

    def test_no_assessment_is_created(self, service, real_draft, doctor):
        from apps.triage.models import TriageAssessment

        real_draft.simulated = True
        real_draft.save(update_fields=["simulated"])

        with pytest.raises(DomainError):
            service.confirm(draft=real_draft, actor=doctor)
        assert TriageAssessment.objects.count() == 0

    def test_the_draft_stays_in_draft_state(self, service, real_draft, doctor):
        real_draft.simulated = True
        real_draft.save(update_fields=["simulated"])

        with pytest.raises(DomainError):
            service.confirm(draft=real_draft, actor=doctor)
        real_draft.refresh_from_db()
        assert real_draft.state == DraftState.DRAFT

    def test_can_be_confirmed_reports_it_before_anyone_tries(self, real_draft):
        """So a UI can disable the button rather than offering an action that
        will fail.
        """
        real_draft.simulated = True
        assert real_draft.can_be_confirmed is False

    def test_a_simulated_draft_can_still_be_discarded(self, service, real_draft, doctor):
        """Throwing away something fabricated is always safe, and a queue that
        cannot be cleared of stub rows would fill with them.
        """
        real_draft.simulated = True
        real_draft.save(update_fields=["simulated"])

        service.discard(draft=real_draft, actor=doctor)
        real_draft.refresh_from_db()
        assert real_draft.state == DraftState.DISCARDED


class TestTwoSignOffsForTwoClaims:
    """Confirming symptoms is not agreeing with the differential.

    Easy to conflate and important not to: the first says "this is what the
    patient has", the second says "this machine output is clinically sensible".
    Letting the first imply the second would put a machine differential into a
    record on the strength of someone agreeing the patient said "itching".
    """

    def test_the_resulting_assessment_is_unreviewed(self, service, real_draft, doctor):
        result = service.confirm(draft=real_draft, actor=doctor)
        assert result.assessment.review_state == ReviewState.UNREVIEWED

    def test_the_draft_itself_is_confirmed(self, service, real_draft, doctor):
        service.confirm(draft=real_draft, actor=doctor)
        real_draft.refresh_from_db()
        assert real_draft.state == DraftState.CONFIRMED

    def test_the_assessment_still_needs_review(self, service, real_draft, doctor):
        result = service.confirm(draft=real_draft, actor=doctor)
        assert result.assessment.needs_review is True


class TestConfirmationRequiresAClinician:
    def test_no_actor_is_refused(self, service, real_draft):
        with pytest.raises(DomainError) as exc:
            service.confirm(draft=real_draft, actor=None)
        assert exc.value.code == "ai_confirmation_needs_actor"

    def test_the_signer_is_recorded(self, service, real_draft, doctor):
        """An unattributed sign-off is not a sign-off."""
        service.confirm(draft=real_draft, actor=doctor)
        real_draft.refresh_from_db()
        assert real_draft.confirmed_by == doctor
        assert real_draft.confirmed_at is not None

    def test_it_is_written_to_the_access_log(self, service, real_draft, doctor):
        from apps.audit.models import AccessLogEntry, AuditAction

        AccessLogEntry.objects.all()._raw_delete(AccessLogEntry.objects.db)
        service.confirm(draft=real_draft, actor=doctor)

        entry = AccessLogEntry.objects.filter(action=AuditAction.UPDATE).first()
        assert entry is not None
        assert "intake draft" in entry.reason

    def test_a_second_confirmation_is_refused(self, service, real_draft, doctor):
        """A re-confirmation is not an edit. The clinical content is append-only."""
        service.confirm(draft=real_draft, actor=doctor)

        with pytest.raises(DomainError) as exc:
            service.confirm(draft=real_draft, actor=doctor)
        assert exc.value.code == "ai_draft_already_reviewed"


class TestTheClinicianCanCorrectTheMachine:
    """The most valuable output of the review step.

    A flow that only allowed acceptance would be a rubber stamp, and the
    corrections are also the signal that tells you what to add to the lexicon.
    """

    def test_a_symptom_can_be_removed(self, service, real_draft, doctor):
        assert "skin_rash" in real_draft.proposed_symptom_names

        result = service.confirm(draft=real_draft, symptoms=["itching"], actor=doctor)
        assert result.assessment.symptoms == ["itching"]

    def test_a_symptom_the_extractor_missed_can_be_added(self, service, real_draft, doctor):
        result = service.confirm(
            draft=real_draft, symptoms=["itching", "high_fever"], actor=doctor
        )
        assert "high_fever" in result.assessment.symptoms

    def test_what_was_confirmed_is_recorded_separately_from_the_proposal(
        self, service, real_draft, doctor
    ):
        """So the review is visible as a diff after the fact."""
        service.confirm(draft=real_draft, symptoms=["itching"], actor=doctor)
        real_draft.refresh_from_db()

        assert real_draft.confirmed_symptoms == ["itching"]
        assert "skin_rash" in real_draft.proposed_symptom_names

    def test_accepting_the_proposal_unchanged_still_records_a_list(
        self, service, real_draft, doctor
    ):
        service.confirm(draft=real_draft, actor=doctor)
        real_draft.refresh_from_db()
        assert set(real_draft.confirmed_symptoms) == set(real_draft.proposed_symptom_names)

    def test_an_unknown_symptom_is_reported_not_dropped(self, service, real_draft, doctor):
        """A clinician who typed something the model does not know should be
        told, not left believing they recorded it.
        """
        with pytest.raises(ValidationError) as exc:
            service.confirm(
                draft=real_draft, symptoms=["itching", "not_a_symptom"], actor=doctor
            )
        assert exc.value.code == "ai_unknown_symptom"

    def test_confirming_nothing_is_refused(self, service, real_draft, doctor):
        """"I reject all of these" is a discard, and saying so is clearer than
        an assessment with no symptoms.
        """
        with pytest.raises(ValidationError) as exc:
            service.confirm(draft=real_draft, symptoms=[], actor=doctor)
        assert exc.value.code == "ai_no_symptoms_confirmed"

    def test_duplicates_collapse_and_order_is_kept(self, service, real_draft, doctor):
        result = service.confirm(
            draft=real_draft, symptoms=["skin_rash", "itching", "skin_rash"], actor=doctor
        )
        assert result.assessment.symptoms == ["skin_rash", "itching"]


class TestDuration:
    def test_the_clinicians_number_wins(self, service, real_draft, doctor):
        """A clinical judgement beats a guess about a sentence."""
        result = service.confirm(draft=real_draft, days=9, actor=doctor)
        assert result.assessment.days == 9

    def test_otherwise_it_reads_what_the_patient_said(self, service, real_draft, doctor):
        result = service.confirm(draft=real_draft, actor=doctor)
        assert result.assessment.days == 4

    def test_silence_falls_back_to_the_default(self, service, clinic, patient, doctor):
        draft = ExtractionService(extractor=LexiconExtractor()).extract(
            text="I have itching", language="en", patient=patient, clinic=clinic
        )
        result = service.confirm(draft=draft, actor=doctor)
        assert result.assessment.days == DEFAULT_DAYS


class TestTheDraftAndTheRecordStayLinked:
    def test_the_draft_points_at_the_assessment_it_produced(
        self, service, real_draft, doctor
    ):
        result = service.confirm(draft=real_draft, actor=doctor)
        real_draft.refresh_from_db()
        assert real_draft.assessment_id == result.assessment.pk

    def test_the_assessment_can_find_its_draft(self, service, real_draft, doctor):
        result = service.confirm(draft=real_draft, actor=doctor)
        assert result.assessment.intake_drafts.first() == real_draft

    def test_erasing_the_patient_takes_the_draft(self, real_draft, patient):
        """A description of someone's illness is health data about them."""
        assert IntakeDraft.objects.count() == 1
        patient.delete()
        assert IntakeDraft.objects.count() == 0

    def test_the_patients_words_are_gone_after_erasure(self, real_draft, patient):
        from apps.ai.models import IntakeDraft as Draft

        patient.delete()
        remaining = " ".join(Draft.objects.values_list("source_text", flat=True))
        assert "itching" not in remaining


class TestDiscard:
    def test_it_moves_to_discarded(self, service, real_draft, doctor):
        service.discard(draft=real_draft, actor=doctor, reason="not what the patient said")
        real_draft.refresh_from_db()
        assert real_draft.state == DraftState.DISCARDED

    def test_the_reason_reaches_the_audit_trail(self, service, real_draft, doctor):
        from apps.audit.models import AccessLogEntry

        AccessLogEntry.objects.all()._raw_delete(AccessLogEntry.objects.db)
        service.discard(draft=real_draft, actor=doctor, reason="misheard")

        entry = AccessLogEntry.objects.first()
        assert "misheard" in entry.reason

    def test_discarding_twice_is_refused(self, service, real_draft, doctor):
        service.discard(draft=real_draft, actor=doctor)
        with pytest.raises(DomainError):
            service.discard(draft=real_draft, actor=doctor)

    def test_a_discarded_draft_cannot_then_be_confirmed(self, service, real_draft, doctor):
        service.discard(draft=real_draft, actor=doctor)
        with pytest.raises(DomainError) as exc:
            service.confirm(draft=real_draft, actor=doctor)
        assert exc.value.code == "ai_draft_already_reviewed"
