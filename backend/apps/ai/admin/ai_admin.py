"""Admin for AI drafts and the call log (AUDIT 8.6, 8.7, 8.8).

Scoped through :class:`~common.admin.base.ScopedModelAdmin`, like every other
tenant-bearing model: an intake draft holds the patient's own description of
their illness, which makes an unscoped list of them one of the more sensitive
screens in the system.

Two admin-specific decisions.

**The proposal is shown with its evidence, always.** A reviewer looking at a list
of symptom names has nothing to check them against, and a review that cannot fail
is not a review. So the changelist shows the phrase that produced each match.

**Nothing can be confirmed from here.** Sign-off runs through
``IntakeService``/``SummaryService``, which enforce the simulated-draft refusal,
the actor requirement and the audit entry. An admin action that flipped ``state``
would bypass all three, so the state field is read-only and the discard action is
the only bulk operation offered - discarding something fabricated is always safe.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.decorators import display

from apps.ai.models import AiCall, ConsultationNote, DraftState, IntakeDraft
from common.admin.base import ScopedModelAdmin


class AiDraftAdminBase(ScopedModelAdmin):
    """Shared read-only provenance and the state column.

    Abstract in spirit: the governance columns are identical for both draft
    types, and writing them twice is how the two screens drift apart.
    """

    tenant_field = "clinic_id"
    tenant_scoped_fks = ("patient", "clinic")
    list_select_related = ("patient", "clinic")

    @display(description="Status", label=True)
    def state_badge(self, obj) -> str:
        """Unfold renders this as a coloured chip.

        AUDIT 8.3 asks for AI-generated and unreviewed content to be visually
        distinct wherever it appears, and a list of drafts is exactly where a
        reviewer decides what to trust.
        """
        if obj.state == DraftState.CONFIRMED:
            return "Confirmed"
        if obj.state == DraftState.DISCARDED:
            return "Discarded"
        if obj.simulated:
            return "Simulated - cannot confirm"
        return "Needs review"

    @display(description="Source")
    def provenance(self, obj) -> str:
        parts = [obj.provider or "unknown"]
        if obj.model_version:
            parts.append(obj.model_version)
        label = " / ".join(parts)
        if obj.externally_processed:
            return format_html(
                '{} <span style="color:#b45309">(sent to a third party)</span>', label
            )
        return label

    @admin.action(description="Discard selected drafts")
    def discard_selected(self, request, queryset):
        """Bulk reject.

        Offered where confirmation is not, because rejecting is safe in every
        state this screen can show and a queue full of stub rows needs clearing.
        Routed through the service so each one gets its audit entry.
        """
        service = self._discard_service()
        discarded = 0
        for draft in queryset.filter(state=DraftState.DRAFT):
            service(draft=draft, actor=request.user, reason="discarded from admin", request=request)
            discarded += 1

        skipped = queryset.count() - discarded
        messages.success(request, f"Discarded {discarded} draft(s).")
        if skipped:
            messages.info(
                request, f"Left {skipped} alone: already confirmed or discarded."
            )

    def _discard_service(self):
        raise NotImplementedError


@admin.register(IntakeDraft)
class IntakeDraftAdmin(AiDraftAdminBase):
    list_display = (
        "created_at",
        "patient_display",
        "language",
        "state_badge",
        "proposal",
        "provenance",
    )
    list_filter = ("state", "language", "simulated", "from_audio", "externally_processed")
    search_fields = ("source_text",)
    date_hierarchy = "created_at"
    actions = ("discard_selected",)

    readonly_fields = (
        "created_at",
        "state",
        "patient",
        "clinic",
        "language",
        "source_text",
        "from_audio",
        "proposal_detail",
        "unmapped_phrases",
        "confirmed_symptoms",
        "assessment",
        "provider",
        "model_version",
        "simulated",
        "externally_processed",
        "confirmed_by",
        "confirmed_at",
    )

    fieldsets = (
        (
            "What the patient said",
            {
                "fields": ("created_at", "patient", "clinic", "language", "source_text", "from_audio"),
                "description": (
                    "The patient's own words. This is health data about them and "
                    "is erased with them."
                ),
            },
        ),
        (
            "What the machine read",
            {
                "fields": ("proposal_detail", "unmapped_phrases"),
                "description": (
                    "A proposal, not a finding. Each symptom is shown with the "
                    "phrase that produced it so the reading can be checked. "
                    "Unmapped phrases are the backlog for the lexicon."
                ),
            },
        ),
        (
            "Sign-off",
            {
                "fields": ("state", "confirmed_symptoms", "assessment", "confirmed_by", "confirmed_at"),
                "description": (
                    "Confirming a draft asserts that these are the patient's "
                    "symptoms. It does not agree with the resulting "
                    "differential, which needs its own review. Confirmation is "
                    "not available from this screen - use the clinician queue, "
                    "which enforces the refusals."
                ),
            },
        ),
        (
            "Provenance",
            {
                "fields": ("provider", "model_version", "simulated", "externally_processed"),
                "classes": ("collapse",),
            },
        ),
    )

    def has_add_permission(self, request) -> bool:
        """A draft records something that happened. Typing one in is not that."""
        return False

    @display(description="Patient")
    def patient_display(self, obj) -> str:
        """``Patient.__str__`` already masks the contact number (AUDIT 8.8).

        Same helper as the assessment queue, deliberately: two screens listing
        the same people should not disagree about how much of them to show.
        """
        return str(obj.patient) if obj.patient_id else "Anonymous"

    @display(description="Proposed")
    def proposal(self, obj) -> str:
        names = obj.proposed_symptom_names
        if not names:
            return "nothing recognised"
        shown = ", ".join(n.replace("_", " ") for n in names[:3])
        extra = len(names) - 3
        return f"{shown} (+{extra} more)" if extra > 0 else shown

    @display(description="Proposed symptoms")
    def proposal_detail(self, obj) -> str:
        entries = obj.proposed_symptoms or []
        if not entries:
            return mark_safe(
                "<em>Nothing in the description mapped to a known symptom. "
                "That is not the same as the patient having no symptoms.</em>"
            )
        rows = "".join(
            format_html(
                "<tr><td style='padding:2px 12px 2px 0'><code>{}</code></td>"
                "<td style='padding:2px 12px 2px 0'>matched on &ldquo;{}&rdquo;</td>"
                "<td style='padding:2px 0'>{}</td></tr>",
                entry.get("symptom", ""),
                entry.get("evidence", ""),
                f"{float(entry.get('confidence', 0)) * 100:.0f}% phrase specificity",
            )
            for entry in entries
        )
        return mark_safe(f"<table>{rows}</table>")

    def _discard_service(self):
        from apps.ai.services.intake_service import IntakeService

        return IntakeService().discard


@admin.register(ConsultationNote)
class ConsultationNoteAdmin(AiDraftAdminBase):
    list_display = ("created_at", "patient_display", "state_badge", "assessment", "provenance")
    list_filter = ("state", "simulated", "externally_processed")
    date_hierarchy = "created_at"

    readonly_fields = (
        "created_at",
        "state",
        "patient",
        "clinic",
        "assessment",
        "rendered_note",
        "clinician_text",
        "provider",
        "model_version",
        "simulated",
        "externally_processed",
        "confirmed_by",
        "confirmed_at",
    )

    fieldsets = (
        (
            "Draft note",
            {
                "fields": ("created_at", "patient", "clinic", "assessment", "rendered_note"),
                "description": (
                    "An AI draft. It carries no treatment or medication advice: "
                    "a draft that mentioned either is refused rather than "
                    "stored, because TPG 2020 forbids this system prescribing."
                ),
            },
        ),
        (
            "Sign-off",
            {"fields": ("state", "clinician_text", "confirmed_by", "confirmed_at")},
        ),
        (
            "Provenance",
            {
                "fields": ("provider", "model_version", "simulated", "externally_processed"),
                "classes": ("collapse",),
            },
        ),
    )

    def has_add_permission(self, request) -> bool:
        return False

    @display(description="Patient")
    def patient_display(self, obj) -> str:
        return str(obj.patient) if obj.patient_id else "Anonymous"

    @display(description="Note")
    def rendered_note(self, obj) -> str:
        sections = obj.ordered_sections()
        if not sections:
            return "-"
        blocks = "".join(
            format_html(
                "<p style='margin:0 0 10px'><strong>{}</strong><br>{}</p>", label, text
            )
            for label, text in sections
        )
        return mark_safe(blocks)

    def _discard_service(self):  # pragma: no cover - no bulk action registered
        raise NotImplementedError


@admin.register(AiCall)
class AiCallAdmin(ScopedModelAdmin):
    """The AI call log. Read-only, like the access log and the gateway log.

    **No text is here, only hashes.** The question this screen answers is which
    model version processed what, and whether anything left the building - both
    of which survive the erasure of the content itself.
    """

    tenant_field = "clinic_id"

    list_display = (
        "created_at",
        "capability",
        "provider",
        "model_version",
        "outcome",
        "duration_ms",
        "disclosure",
    )
    list_filter = ("capability", "provider", "succeeded", "simulated", "external")
    search_fields = ("input_sha256", "output_sha256", "correlation_id")
    date_hierarchy = "created_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        """Append-only: a log that can be edited is not evidence."""
        return False

    @display(description="Outcome", label=True)
    def outcome(self, obj) -> str:
        if not obj.succeeded:
            return "Failed"
        return "Simulated" if obj.simulated else "Real"

    @display(description="Data left the building")
    def disclosure(self, obj) -> str:
        """The field a DPDP audit asks about.

        Expected to read "no" everywhere until someone deliberately configures a
        vendor, which is exactly why it is worth a column.
        """
        if not obj.external:
            return "No"
        return "Yes, redacted" if obj.redacted else format_html(
            '<span style="color:#b91c1c">Yes, NOT redacted</span>'
        )
