"""ModelAdmin for TriageAssessment - the prediction log (AUDIT 8.4, 8.5).

Append-only on the clinical fields: a finalised assessment is not edited, and
a correction is a new record with a reason. That is what an auditor expects to
see (8.6).

The one field staff *can* change is ``review_state``, because clinician
sign-off is the whole point of recording these. Every transition is captured
by ``simple_history``.
"""

from __future__ import annotations

from django.contrib import admin, messages
from simple_history.admin import SimpleHistoryAdmin
from unfold.decorators import display

from apps.audit.models import AuditAction
from apps.audit.services.audit_service import AuditService
from apps.triage.models import ReviewState, TriageAssessment, UrgencyLevel
from common.admin import AuditedModelAdmin

# Unfold label variants. Red is reserved for clinical danger and appears
# nowhere else in this project - not on delete, not on cancel (AUDIT 8.3).
_URGENCY_LABELS = {
    UrgencyLevel.URGENT.label: "danger",
    UrgencyLevel.CONSULT.label: "warning",
    UrgencyLevel.ROUTINE.label: "success",
    UrgencyLevel.UNKNOWN.label: "info",
}

_REVIEW_LABELS = {
    ReviewState.UNREVIEWED.label: "warning",
    ReviewState.REVIEWED.label: "success",
    ReviewState.CORRECTED.label: "info",
}


@admin.register(TriageAssessment)
class TriageAssessmentAdmin(AuditedModelAdmin, SimpleHistoryAdmin):
    list_display = (
        "created_at",
        "patient_display",
        "outcome",
        "urgency_badge",
        "review_badge",
        "days",
    )
    list_filter = ("clinic", "review_state", "urgency_level", "abstained", "created_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 50
    # Without this the patient column is an N+1 on every page load, which is
    # the number one cause of "the admin is slow" (AUDIT 8.6).
    list_select_related = ("patient", "clinic")
    # A <select> rendering 50,000 patients would hang the browser (8.6). The
    # autocomplete endpoint resolves through PatientAdmin.get_queryset, which
    # is a ScopedModelAdmin - that is the leak in 8.7 point 4, closed, and
    # asserted in common/tests/test_admin_scoping.py.
    autocomplete_fields = ("patient",)

    # Live as of Phase 4.
    tenant_field = "clinic_id"
    tenant_scoped_fks = ("patient", "clinic")

    readonly_fields = (
        "clinic",
        "patient",
        "symptoms",
        "days",
        "abstained",
        "differential",
        "urgency_level",
        "urgency_score",
        "model_fingerprint",
        "model_trained_at",
        "abstain_threshold",
        "created_at",
    )

    fieldsets = (
        ("Input", {"fields": ("clinic", "patient", "symptoms", "days")}),
        (
            "Output",
            {"fields": ("abstained", "differential", "urgency_level", "urgency_score")},
        ),
        (
            "Provenance",
            {
                "fields": (
                    "model_fingerprint",
                    "model_trained_at",
                    "abstain_threshold",
                    "created_at",
                ),
                "description": "Which model produced this result. Required for "
                "traceability under the CDSCO MDSW guidance.",
            },
        ),
        (
            "Review",
            {
                "fields": ("review_state",),
                "description": "Every AI output needs clinician sign-off before "
                "it enters a record (Telemedicine Practice Guidelines 2020). "
                "Each transition is recorded in this record's history.",
            },
        ),
    )

    actions = ["mark_reviewed"]

    @display(description="Patient")
    def patient_display(self, obj: TriageAssessment) -> str:
        return str(obj.patient) if obj.patient_id else "Anonymous"

    @display(description="Outcome")
    def outcome(self, obj: TriageAssessment) -> str:
        if obj.abstained:
            return "Abstained - no condition named"
        confidence = obj.top_confidence
        if confidence is None:
            return str(obj.top_disease)
        # "symptom match", never "confident": on this dataset a textbook
        # presentation returns a unanimous vote, and calling that confidence
        # would put back the false certainty the audit set out to remove.
        return f"{obj.top_disease} ({confidence:.0%} symptom match)"

    @display(description="Urgency", ordering="urgency_level", label=_URGENCY_LABELS)
    def urgency_badge(self, obj: TriageAssessment) -> str:
        return obj.get_urgency_level_display()

    @display(description="Review", ordering="review_state", label=_REVIEW_LABELS)
    def review_badge(self, obj: TriageAssessment) -> str:
        """A distinct, consistently applied "unreviewed" state.

        AUDIT 8.3 calls this a compliance control, not decoration: it is how
        the sign-off requirement gets enforced in practice.
        """
        return obj.get_review_state_display()

    def has_add_permission(self, request) -> bool:
        # Assessments are produced by the triage flow, not typed in.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description="Mark as reviewed by a clinician")
    def mark_reviewed(self, request, queryset):
        """Sign-off, recorded.

        Iterates rather than calling .update() so each row goes through
        save(), which is what creates the history entry. A bulk update would
        change the state and leave no trail of who signed it off - which is
        the opposite of the point.
        """
        audit = AuditService()
        signed = 0

        for assessment in queryset.filter(review_state=ReviewState.UNREVIEWED):
            assessment.review_state = ReviewState.REVIEWED
            assessment.save(update_fields=["review_state"])
            audit.record(
                action=AuditAction.UPDATE,
                actor=request.user,
                obj=assessment,
                request=request,
                reason="clinician sign-off",
            )
            signed += 1

        self.message_user(
            request,
            f"Marked {signed} assessment(s) as reviewed."
            if signed
            else "No unreviewed assessments were selected.",
            messages.SUCCESS if signed else messages.WARNING,
        )
