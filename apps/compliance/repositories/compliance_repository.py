"""The only place ORM queries against the compliance models live."""

from __future__ import annotations

from django.db.models import QuerySet
from django.utils import timezone

from apps.compliance.models import (
    BreachIncident,
    BreachStatus,
    DataSubjectRequest,
    DSRStatus,
    RetentionNotice,
)


class RetentionRepository:
    @staticmethod
    def create_notice(**fields) -> RetentionNotice:
        return RetentionNotice.objects.create(**fields)

    @staticmethod
    def open_notice_for(patient_ref: int) -> RetentionNotice | None:
        return RetentionNotice.objects.filter(
            patient_ref=patient_ref, erased_at__isnull=True, cancelled_at__isnull=True
        ).first()

    @staticmethod
    def due_notices() -> QuerySet[RetentionNotice]:
        """Open notices whose advance-notice period has elapsed."""
        return RetentionNotice.objects.filter(
            erased_at__isnull=True,
            cancelled_at__isnull=True,
            due_at__lte=timezone.now(),
        ).order_by("due_at")

    @staticmethod
    def open_notices() -> QuerySet[RetentionNotice]:
        return RetentionNotice.objects.filter(
            erased_at__isnull=True, cancelled_at__isnull=True
        )

    @staticmethod
    def count_open() -> int:
        return RetentionRepository.open_notices().count()


class DSRRepository:
    @staticmethod
    def create(**fields) -> DataSubjectRequest:
        return DataSubjectRequest.objects.create(**fields)

    @staticmethod
    def open_requests() -> QuerySet[DataSubjectRequest]:
        return DataSubjectRequest.objects.filter(
            status__in=[DSRStatus.OPEN, DSRStatus.IN_PROGRESS]
        ).order_by("due_at")

    @staticmethod
    def count_open() -> int:
        return DSRRepository.open_requests().count()

    @staticmethod
    def count_overdue() -> int:
        return (
            DSRRepository.open_requests().filter(due_at__lt=timezone.now()).count()
        )

    @staticmethod
    def next_due() -> DataSubjectRequest | None:
        return DSRRepository.open_requests().first()


class BreachRepository:
    @staticmethod
    def create(**fields) -> BreachIncident:
        return BreachIncident.objects.create(**fields)

    @staticmethod
    def open_incidents() -> QuerySet[BreachIncident]:
        return BreachIncident.objects.filter(status=BreachStatus.OPEN).order_by("due_at")

    @staticmethod
    def count_open() -> int:
        return BreachRepository.open_incidents().count()

    @staticmethod
    def most_urgent_open() -> BreachIncident | None:
        """Drives the 72-hour countdown tile (AUDIT 8.5)."""
        return BreachRepository.open_incidents().first()
