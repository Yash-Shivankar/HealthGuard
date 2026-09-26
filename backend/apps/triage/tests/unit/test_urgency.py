"""Tests for the urgency assessment."""

from __future__ import annotations

from apps.triage.services import urgency as triage


class TestSeverityScore:
    def test_empty_symptom_list_does_not_divide_by_zero(self):
        """``severity_sum / len(symptoms)`` raised ZeroDivisionError on an
        empty selection, which was a two-click path to a 500 (AUDIT 2.7).
        """
        assert triage.severity_score([], days=3) == 0.0

    def test_duration_changes_the_score(self):
        """``num_days`` was accepted, required, and then never referenced
        (AUDIT 2.12). The duration weighting is restored.
        """
        symptoms = ["high_fever", "vomiting"]
        assert triage.severity_score(symptoms, days=10) > triage.severity_score(
            symptoms, days=1
        )

    def test_zero_and_negative_durations_are_clamped(self):
        assert triage.severity_score(["high_fever"], days=0) == 0.0
        assert triage.severity_score(["high_fever"], days=-5) == 0.0

    def test_unknown_symptoms_contribute_zero_without_raising(self):
        assert triage.severity_score(["not_a_symptom"], days=5) == 0.0


class TestAssess:
    def test_no_symptoms_returns_the_unknown_level(self):
        urgency = triage.assess([], days=3)
        assert urgency.level == "unknown"
        assert not urgency.is_escalated

    def test_severe_and_long_running_escalates(self):
        urgency = triage.assess(["high_fever", "vomiting", "chest_pain"], days=30)
        assert urgency.level == "urgent"
        assert urgency.is_escalated

    def test_mild_and_recent_stays_routine(self):
        urgency = triage.assess(["itching"], days=1)
        assert urgency.level == "routine"
        assert not urgency.is_escalated

    def test_every_level_carries_a_text_label_and_advice(self):
        """Colour never carries meaning on its own (AUDIT 8.3)."""
        for symptoms, days in (([], 1), (["itching"], 1), (["chest_pain"], 60)):
            urgency = triage.assess(symptoms, days)
            assert urgency.label.strip()
            assert urgency.advice.strip()

    def test_escalation_is_monotonic_in_duration(self):
        symptoms = ["high_fever", "vomiting"]
        scores = [triage.severity_score(symptoms, days=d) for d in (1, 5, 20, 60)]
        assert scores == sorted(scores)


class TestAbstentionUrgency:
    def test_abstaining_still_points_at_a_clinician(self):
        """Declining to answer is not the same as reassuring the patient."""
        urgency = triage.abstention_urgency()
        assert urgency.is_escalated
        assert "doctor" in urgency.advice.lower()
