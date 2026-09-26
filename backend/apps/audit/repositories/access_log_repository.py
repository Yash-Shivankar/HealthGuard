"""The only place ORM queries against AccessLogEntry live.

Note what is absent: there is no ``update`` and no ``delete``. The table is
append-only, and the cheapest way to keep it that way is to give callers no
method that would change a row (AUDIT 8.9).
"""

from __future__ import annotations

from django.db.models import QuerySet

from apps.audit.models import AccessLogEntry


class AccessLogRepository:
    @staticmethod
    def create(**fields) -> AccessLogEntry:
        return AccessLogEntry.objects.create(**fields)

    @staticmethod
    def bulk_create(entries: list[AccessLogEntry]) -> list[AccessLogEntry]:
        return AccessLogEntry.objects.bulk_create(entries)

    @staticmethod
    def for_object(object_type: str, object_id: str) -> QuerySet[AccessLogEntry]:
        return AccessLogEntry.objects.filter(
            object_type=object_type, object_id=str(object_id)
        ).select_related("actor")

    @staticmethod
    def for_actor(actor) -> QuerySet[AccessLogEntry]:
        return AccessLogEntry.objects.filter(actor=actor)

    @staticmethod
    def recent(limit: int = 100) -> QuerySet[AccessLogEntry]:
        return AccessLogEntry.objects.select_related("actor")[:limit]
