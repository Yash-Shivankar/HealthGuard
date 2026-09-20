"""ModelAdmin for TriageAssessment - the prediction log (AUDIT 8.4, 8.5).

Read-only on the clinical fields. A finalised assessment is append-only: a
correction is a new record with a reason, never an edit of the original. That
is what an auditor expects to see (AUDIT 8.6).
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.triage.models import ReviewState, TriageAssessment, UrgencyLevel

# Urgency colours. Red is reserved for clinical danger and is used nowhere
# else in this project (AUDIT 8.3).
_URGENCY_COLOURS = {
    UrgencyLevel.URGENT: "#dc2626",
    UrgencyLevel.CONSULT: "#b45309",
    UrgencyLevel.ROUTINE: "#15803d",
    UrgencyLevel.UNKNOWN: "#52606d",
}


@admin.register(TriageAssessment)
class TriageAssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "patient_display",
        "outcome",
        "urgency_badge",
        "review_badge",
        "days",
    )
    list_filter = ("review_state", "urgency_level", "abstained", "created_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 50
    # Without this the patient column is an N+1 on every page load, which is
    # the number one cause of "the admin is slow" (AUDIT 8.6).
    list_select_related = ("patient",)

    readonly_fields = (
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
        ("Input", {"fields": ("patient", "symptoms", "days")}),
        ("Output", {"fields": ("abstained", "differential", "urgency_level", "urgency_score")}),
        (
            "Provenance",
            {
                "fields": ("model_fingerprint", "model_trained_at", "abstain_threshold", "created_at"),
                "description": "Which model produced this result. Required for "
                "traceability under the CDSCO MDSW guidance.",
            },
        ),
        (
            "Review",
            {
                "fields": ("review_state",),
                "description": "Every AI output needs clinician sign-off before "
                "it enters a record (Telemedicine Practice Guidelines 2020).",
            },
        ),
    )

    @admin.display(description="Patient")
    def patient_display(self, obj: TriageAssessment) -> str:
        return str(obj.patient) if obj.patient_id else "Anonymous"

    @admin.display(description="Outcome")
    def outcome(self, obj: TriageAssessment) -> str:
        if obj.abstained:
            return "Abstained - no condition named"
        confidence = obj.top_confidence
        return f"{obj.top_disease} ({confidence:.0%} match)" if confidence else str(obj.top_disease)

    @admin.display(description="Urgency", ordering="urgency_level")
    def urgency_badge(self, obj: TriageAssessment) -> str:
        colour = _URGENCY_COLOURS.get(obj.urgency_level, "#52606d")
        return format_html(
            '<span style="color:{};">{}</span>', colour, obj.get_urgency_level_display()
        )

    @admin.display(description="Review", ordering="review_state")
    def review_badge(self, obj: TriageAssessment) -> str:
        if obj.review_state == ReviewState.UNREVIEWED:
            # Violet for "AI draft, unreviewed" - a distinct, consistently
            # applied state, which is how the sign-off requirement gets
            # enforced in practice rather than remembered (AUDIT 8.3).
            return format_html('<span style="color:#7c3aed;">{}</span>', obj.get_review_state_display())
        return obj.get_review_state_display()

    def has_add_permission(self, request) -> bool:
        # Assessments are produced by the triage flow, not typed in.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
