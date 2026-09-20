"""The only place ORM queries against TriageAssessment live."""

from __future__ import annotations

from django.db.models import QuerySet

from apps.triage.models import ReviewState, TriageAssessment


class AssessmentRepository:
    @staticmethod
    def create(**fields) -> TriageAssessment:
        return TriageAssessment.objects.create(**fields)

    @staticmethod
    def get_by_id(assessment_id: int) -> TriageAssessment | None:
        return (
            TriageAssessment.objects.select_related("patient")
            .filter(pk=assessment_id)
            .first()
        )

    @staticmethod
    def for_patient(patient) -> QuerySet[TriageAssessment]:
        return TriageAssessment.objects.filter(patient=patient)

    @staticmethod
    def awaiting_review() -> QuerySet[TriageAssessment]:
        """Feeds the Phase 3 compliance dashboard tile (AUDIT 8.5).

        select_related because the admin list page renders the patient, and the
        default N+1 there is the number one cause of "the admin is slow" (8.6).
        """
        return (
            TriageAssessment.objects.select_related("patient")
            .filter(review_state=ReviewState.UNREVIEWED)
            .order_by("created_at")
        )
