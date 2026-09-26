"""Unit tests for AssessmentService.

The service is the only implementation of the triage rules; the HTML page and
the JSON API both call it. These tests are what stop the two surfaces drifting
apart, because neither of them gets its own copy of this logic.
"""

from __future__ import annotations

import pytest

from apps.patients.models import Patient
from apps.triage.models import ReviewState, TriageAssessment, UrgencyLevel
from apps.triage.services import predictor
from apps.triage.services.assessment_service import AssessmentService, humanise

pytestmark = pytest.mark.django_db

KNOWN_PATTERN = ["itching", "skin_rash", "nodal_skin_eruptions", "dischromic _patches"]


@pytest.fixture
def service() -> AssessmentService:
    return AssessmentService()


@pytest.fixture
def patient() -> Patient:
    return Patient.objects.create(
        name="Triage Person", contact="9876543210", age=30, height_cm=170, weight_kg=65
    )


class TestAssess:
    def test_records_an_assessment(self, service):
        result = service.assess(symptoms=KNOWN_PATTERN, days=3)
        assert TriageAssessment.objects.count() == 1
        assert result.assessment.pk

    def test_links_to_a_patient_when_given_one(self, service, patient):
        result = service.assess(symptoms=KNOWN_PATTERN, days=3, patient=patient)
        assert result.assessment.patient_id == patient.pk
        assert not result.assessment.is_anonymous

    def test_an_anonymous_check_is_allowed(self, service):
        """Collecting identity for a function that does not need it is exactly
        what the DPDP notice obligations are aimed at.
        """
        result = service.assess(symptoms=KNOWN_PATTERN, days=3)
        assert result.assessment.is_anonymous

    def test_persist_false_writes_nothing(self, service):
        result = service.assess(symptoms=KNOWN_PATTERN, days=3, persist=False)
        assert TriageAssessment.objects.count() == 0
        assert result.assessment.pk is None

    def test_every_assessment_starts_unreviewed(self, service):
        result = service.assess(symptoms=KNOWN_PATTERN, days=3)
        assert result.assessment.review_state == ReviewState.UNREVIEWED
        assert result.assessment.needs_review

    def test_records_model_provenance(self, service):
        result = service.assess(symptoms=KNOWN_PATTERN, days=3)
        artefact = predictor.artefact()
        assert result.assessment.model_fingerprint == artefact.data_fingerprint
        assert result.assessment.abstain_threshold == result.differential.threshold

    def test_the_differential_snapshot_is_stored(self, service):
        result = service.assess(symptoms=KNOWN_PATTERN, days=3)
        stored = result.assessment.differential
        assert stored
        assert {"disease", "confidence"} <= stored[0].keys()
        assert stored[0]["disease"] == result.top.disease

    def test_clinical_content_accompanies_a_named_condition(self, service):
        result = service.assess(symptoms=KNOWN_PATTERN, days=3)
        assert result.description
        assert result.precautions
        assert isinstance(result.remedies, list)


class TestAbstention:
    def test_a_vague_symptom_abstains(self, service):
        result = service.assess(symptoms=["fatigue"], days=2)
        assert result.abstained
        assert result.assessment.abstained

    def test_abstaining_returns_no_clinical_content(self, service):
        """Naming precautions for a condition we declined to name would be
        incoherent, and would leak the guess we said we were not making.
        """
        result = service.assess(symptoms=["fatigue"], days=2)
        assert result.description == ""
        assert result.precautions == []
        assert result.remedies == []

    def test_abstaining_still_points_at_a_clinician(self, service):
        result = service.assess(symptoms=["fatigue"], days=2)
        assert result.urgency.is_escalated
        assert "doctor" in result.urgency.advice.lower()

    def test_an_abstained_assessment_is_still_recorded(self, service):
        """A prediction log missing its abstentions would misrepresent how
        often the model declines.
        """
        service.assess(symptoms=["fatigue"], days=2)
        assert TriageAssessment.objects.get().abstained


class TestUrgencyRecording:
    def test_urgency_level_is_stored(self, service):
        result = service.assess(symptoms=KNOWN_PATTERN, days=3)
        assert result.assessment.urgency_level in UrgencyLevel.values

    def test_duration_changes_the_recorded_urgency_score(self, service):
        """Duration was collected, required, and then ignored (AUDIT 2.12)."""
        brief = service.assess(symptoms=KNOWN_PATTERN, days=1)
        prolonged = service.assess(symptoms=KNOWN_PATTERN, days=45)
        assert prolonged.assessment.urgency_score > brief.assessment.urgency_score

    def test_duration_is_ignored_when_the_model_abstains(self, service):
        """Deliberate: if we cannot name a condition, we should not be
        computing a severity-weighted urgency from symptoms we could not
        interpret. The safe fixed answer is to point at a clinician.
        """
        brief = service.assess(symptoms=["fatigue"], days=1)
        prolonged = service.assess(symptoms=["fatigue"], days=90)
        assert brief.abstained and prolonged.abstained
        assert brief.urgency.level == prolonged.urgency.level
        assert prolonged.assessment.urgency_score == brief.assessment.urgency_score


class TestSymptomVocabulary:
    def test_comes_from_the_artefact(self, service):
        symptoms = service.available_symptoms()
        assert len(symptoms) == len(predictor.available_symptoms())
        assert {"value", "label"} <= symptoms[0].keys()

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("foul_smell_of urine", "Foul smell of urine"),
            ("dischromic _patches", "Dischromic  patches"),
            ("itching", "Itching"),
        ],
    )
    def test_humanise_cleans_raw_feature_names(self, raw, expected):
        assert humanise(raw) == expected


class TestModelProperties:
    def test_top_disease_is_none_when_abstained(self):
        assessment = TriageAssessment(abstained=True, differential=[])
        assert assessment.top_disease is None
        assert assessment.top_confidence is None

    def test_top_disease_reads_the_first_candidate(self):
        assessment = TriageAssessment(
            abstained=False, differential=[{"disease": "Malaria", "confidence": 0.8}]
        )
        assert assessment.top_disease == "Malaria"
        assert assessment.top_confidence == 0.8
