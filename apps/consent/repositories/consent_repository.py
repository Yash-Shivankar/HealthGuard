"""The only place ORM queries against ConsentArtefact live."""

from __future__ import annotations

from django.db.models import QuerySet
from django.utils import timezone

from apps.consent.models import ConsentArtefact, ConsentPurpose


class ConsentRepository:
    @staticmethod
    def create(
        *, patient, purpose: str, notice_version: str
    ) -> ConsentArtefact:
        return ConsentArtefact.objects.create(
            patient=patient,
            patient_ref=patient.pk,
            purpose=purpose,
            notice_version=notice_version,
        )

    @staticmethod
    def active_for(patient, purpose: str) -> ConsentArtefact | None:
        return (
            ConsentArtefact.objects.filter(
                patient=patient, purpose=purpose, withdrawn_at__isnull=True
            )
            .order_by("-granted_at")
            .first()
        )

    @staticmethod
    def all_for(patient) -> QuerySet[ConsentArtefact]:
        return ConsentArtefact.objects.filter(patient=patient)

    @staticmethod
    def withdraw_active_for(patient) -> int:
        """Mark every active artefact for this patient as withdrawn.

        A single UPDATE rather than a read-modify-write loop: withdrawal is a
        legal obligation with a deadline, so it should be one atomic statement
        that cannot half-apply.
        """
        return ConsentArtefact.objects.filter(
            patient=patient, withdrawn_at__isnull=True
        ).update(withdrawn_at=timezone.now())

    @staticmethod
    def purposes() -> list[str]:
        return [choice.value for choice in ConsentPurpose]
