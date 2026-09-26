"""Data access for the AI app.

Every query the services and controllers need lives here: the architecture test
rejects ``.objects.`` anywhere else, and it has caught six violations across the
project so far - each time fixed by adding a method here rather than by
weakening the test.
"""

from __future__ import annotations

from apps.ai.models import AiCall, ConsultationNote, DraftState, IntakeDraft


class IntakeDraftRepository:
    @staticmethod
    def create(**fields) -> IntakeDraft:
        return IntakeDraft.objects.create(**fields)

    @staticmethod
    def save(draft: IntakeDraft, fields: list[str]) -> IntakeDraft:
        draft.save(update_fields=fields)
        return draft

    @staticmethod
    def find(pk) -> IntakeDraft | None:
        if pk is None:
            return None
        return IntakeDraft.objects.filter(pk=pk).first()

    @staticmethod
    def all_for_queue():
        """The review queue, with the joins the list rendering needs."""
        return IntakeDraft.objects.select_related("patient", "clinic", "confirmed_by")

    @staticmethod
    def pending_for_clinic(clinic_id):
        return IntakeDraft.objects.filter(
            clinic_id=clinic_id, state=DraftState.DRAFT
        ).select_related("patient")

    @staticmethod
    def count_pending(clinic_id=None) -> int:
        queryset = IntakeDraft.objects.filter(state=DraftState.DRAFT)
        if clinic_id is not None:
            queryset = queryset.filter(clinic_id=clinic_id)
        return queryset.count()

    @staticmethod
    def language_breakdown(clinic_id=None) -> list[dict]:
        """Drafts per input language.

        The one fairness-adjacent number this project can honestly produce
        today. It says which languages are being used, not whether they are
        served equally well - see AUDIT 6 and the AI card.
        """
        from django.db.models import Count

        queryset = IntakeDraft.objects.all()
        if clinic_id is not None:
            queryset = queryset.filter(clinic_id=clinic_id)
        return list(
            queryset.values("language")
            .annotate(count=Count("id"))
            .order_by("-count")
        )


class ConsultationNoteRepository:
    @staticmethod
    def create(**fields) -> ConsultationNote:
        return ConsultationNote.objects.create(**fields)

    @staticmethod
    def save(note: ConsultationNote, fields: list[str]) -> ConsultationNote:
        note.save(update_fields=fields)
        return note

    @staticmethod
    def all_for_queue():
        return ConsultationNote.objects.select_related(
            "patient", "clinic", "assessment", "confirmed_by"
        )

    @staticmethod
    def count_pending(clinic_id=None) -> int:
        queryset = ConsultationNote.objects.filter(state=DraftState.DRAFT)
        if clinic_id is not None:
            queryset = queryset.filter(clinic_id=clinic_id)
        return queryset.count()


class AiCallRepository:
    @staticmethod
    def create(**fields) -> AiCall:
        return AiCall.objects.create(**fields)

    @staticmethod
    def recent(limit: int = 50):
        return AiCall.objects.all()[:limit]

    @staticmethod
    def count_external() -> int:
        """How many calls sent data to a third party.

        The number a DPDP audit asks for. Expected to be zero until someone
        deliberately configures a vendor, which makes it a useful assertion.
        """
        return AiCall.objects.filter(external=True).count()
