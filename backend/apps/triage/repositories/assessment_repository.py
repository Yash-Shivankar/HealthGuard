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
    def all_for_queue() -> QuerySet[TriageAssessment]:
        """Base queryset for the review-queue endpoints."""
        return TriageAssessment.objects.select_related("patient", "clinic").all()

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

    # --- Aggregates for the operations dashboard (AUDIT 8.5) ---------------
    #
    # Every query below is covered by an index declared on TriageAssessment:
    # -created_at, (review_state, -created_at) and (urgency_level, -created_at).
    # The audit's warning is explicit - "the dashboard must not run twelve
    # unindexed aggregate queries on every page load".

    @staticmethod
    def count() -> int:
        return TriageAssessment.objects.count()

    @staticmethod
    def count_since(cutoff) -> int:
        return TriageAssessment.objects.filter(created_at__gte=cutoff).count()

    @staticmethod
    def count_anonymous() -> int:
        return TriageAssessment.objects.filter(patient__isnull=True).count()

    @staticmethod
    def count_unreviewed() -> int:
        return TriageAssessment.objects.filter(
            review_state=ReviewState.UNREVIEWED
        ).count()

    @staticmethod
    def count_unreviewed_older_than(cutoff) -> int:
        """The ageing indicator: unreviewed beyond the SLA is the number that
        matters, not the raw backlog (AUDIT 8.5).
        """
        return TriageAssessment.objects.filter(
            review_state=ReviewState.UNREVIEWED, created_at__lt=cutoff
        ).count()

    @staticmethod
    def count_since_by_urgency(cutoff, levels: list[str]) -> int:
        return TriageAssessment.objects.filter(
            created_at__gte=cutoff, urgency_level__in=levels
        ).count()

    @staticmethod
    def count_abstained_since(cutoff) -> int:
        return TriageAssessment.objects.filter(
            created_at__gte=cutoff, abstained=True
        ).count()

    @staticmethod
    def daily_counts_since(cutoff) -> list[dict]:
        from django.db.models import Count
        from django.db.models.functions import TruncDate

        return list(
            TriageAssessment.objects.filter(created_at__gte=cutoff)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("pk"))
            .order_by("day")
        )

    @staticmethod
    def counts_by_urgency() -> list[dict]:
        from django.db.models import Count

        return list(
            TriageAssessment.objects.values("urgency_level")
            .annotate(count=Count("pk"))
            .order_by("-count")
        )

    @staticmethod
    def named_differentials() -> list[list]:
        """Stored differentials for assessments that named a condition.

        Returns the raw JSON so the caller can rank in Python. A JSON-path
        aggregate would be faster but would tie the dashboard to Postgres
        while tests run on SQLite; revisit when the table is large enough to
        justify the coupling.
        """
        return list(
            TriageAssessment.objects.filter(abstained=False).values_list(
                "differential", flat=True
            )
        )
