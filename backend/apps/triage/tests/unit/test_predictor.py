"""Tests for model loading and inference."""

from __future__ import annotations

import pytest
from django.test import override_settings

from apps.triage.services import predictor


@pytest.fixture(scope="module")
def symptoms() -> list[str]:
    return predictor.artefact().feature_names


class TestArtefact:
    def test_artefact_loads(self):
        art = predictor.artefact()
        assert art.feature_names
        assert art.classes
        assert 0.0 < art.abstain_threshold < 1.0

    def test_artefact_is_loaded_once_per_process(self):
        """The old code fitted a fresh model on every request (AUDIT 2.8)."""
        assert predictor.artefact() is predictor.artefact()

    def test_feature_count_matches_the_cleaned_dataset(self, symptoms):
        """131, not 132: the empty duplicate fluid_overload column is dropped."""
        assert len(symptoms) == 131
        assert "fluid_overload.1" not in symptoms
        assert "fluid_overload" in symptoms


class TestPrediction:
    def test_returns_a_ranked_differential(self, symptoms):
        """Not a single disease name (AUDIT 6, Tier 0.3)."""
        result = predictor.predict(symptoms[:6])
        assert len(result.candidates) > 1
        confidences = [c.confidence for c in result.candidates]
        assert confidences == sorted(confidences, reverse=True)

    def test_is_deterministic(self, symptoms):
        """The unpinned tree could return different diseases for the same
        input on two consecutive submissions (AUDIT 2.8).
        """
        selection = symptoms[:6]
        first = predictor.predict(selection)
        second = predictor.predict(selection)
        assert [c.disease for c in first.candidates] == [c.disease for c in second.candidates]
        assert [c.confidence for c in first.candidates] == [
            c.confidence for c in second.candidates
        ]

    def test_unknown_symptoms_are_reported_not_raised(self, symptoms):
        """A name outside the training columns used to raise KeyError (2.7)."""
        result = predictor.predict([symptoms[0], "not_a_real_symptom"])
        assert result.unknown_symptoms == ["not_a_real_symptom"]
        assert result.recognised_symptoms == [symptoms[0]]

    def test_empty_symptom_list_abstains_instead_of_raising(self):
        result = predictor.predict([])
        assert result.abstained is True
        assert result.candidates == []

    def test_duplicate_symptoms_are_collapsed(self, symptoms):
        result = predictor.predict([symptoms[0], symptoms[0], symptoms[1]])
        assert result.recognised_symptoms == [symptoms[0], symptoms[1]]

    def test_a_known_pattern_is_recognised_confidently(self):
        """A textbook presentation should clear the abstain threshold."""
        result = predictor.predict(
            ["itching", "skin_rash", "nodal_skin_eruptions", "dischromic _patches"]
        )
        assert result.abstained is False
        assert result.top.confidence > result.threshold

    def test_confidence_percent_is_a_whole_number(self, symptoms):
        result = predictor.predict(symptoms[:5])
        assert isinstance(result.top.confidence_percent, int)


class TestAbstention:
    @override_settings(TRIAGE_ABSTAIN_THRESHOLD=0.99)
    def test_a_high_threshold_forces_abstention(self, symptoms):
        """The safety path: below threshold the app must not name a condition."""
        result = predictor.predict([symptoms[0]])
        assert result.abstained is True

    @override_settings(TRIAGE_ABSTAIN_THRESHOLD=0.01)
    def test_a_low_threshold_allows_an_answer(self, symptoms):
        result = predictor.predict([symptoms[0]])
        assert result.abstained is False

    def test_a_single_vague_symptom_is_treated_cautiously(self, symptoms):
        """One symptom should not produce a near-certain answer."""
        result = predictor.predict([symptoms[0]])
        assert result.top.confidence < 0.95
