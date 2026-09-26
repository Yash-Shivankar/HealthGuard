"""Admin for the DPDP deadline queues (AUDIT 4.1, 8.5).

Every list here is ordered by due date, because these are countdowns rather
than records. The overdue column is the one that matters: a queue sorted by
creation date tells you what happened, a queue sorted by deadline tells you
what to do next.
"""

from __future__ import annotations

from django.contrib import admin, messages
from unfold.decorators import display

from apps.compliance.models import (
    BreachIncident,
    BreachStatus,
    DataSubjectRequest,
    DSRStatus,
    RetentionNotice,
)
from common.admin import AuditedModelAdmin, ReadOnlyModelAdmin

_OVERDUE_LABELS = {"Overdue": "danger", "Due soon": "warning", "On time": "success"}


def _due_label(obj) -> str:
    if obj.is_overdue:
        return "Overdue"
    remaining = obj.hours_remaining
    return "Due soon" if remaining is not None and remaining < 24 else "On time"


@admin.register(RetentionNotice)
class RetentionNoticeAdmin(ReadOnlyModelAdmin):
    """Read-only: a notice is evidence that notice was given.

    Issued and closed by ``manage.py run_retention``. Editing a due date by
    hand would make the 48-hour guarantee unprovable, which is the only thing
    this table is for.
    """

    list_display = (
        "patient_ref",
        "clinic",
        "reason",
        "issued_at",
        "due_at",
        "due_badge",
        "state",
    )
    list_filter = ("clinic", "erased_at", "cancelled_at", "due_at")
    search_fields = ("patient_ref",)
    ordering = ("due_at",)
    list_select_related = ("clinic",)
    tenant_field = "clinic_id"
    audit_reads = False

    @display(description="Deadline", label=_OVERDUE_LABELS)
    def due_badge(self, obj: RetentionNotice) -> str:
        return _due_label(obj)

    @display(description="State")
    def state(self, obj: RetentionNotice) -> str:
        if obj.erased_at:
            return "Erased"
        if obj.cancelled_at:
            return "Cancelled - person returned"
        return "Awaiting erasure"


@admin.register(DataSubjectRequest)
class DataSubjectRequestAdmin(AuditedModelAdmin):
    """The 90-day grievance and rights queue.

    Writable, because handling a request is the work. The resolution text is
    the record of what was done, so a request cannot close without one -
    enforced by the action below rather than by a nullable field nobody fills.
    """

    list_display = (
        "requester_name",
        "kind",
        "status",
        "clinic",
        "received_at",
        "due_at",
        "due_badge",
    )
    list_filter = ("status", "kind", "clinic", "due_at")
    search_fields = ("requester_name", "requester_contact", "patient_ref")
    ordering = ("due_at",)
    list_select_related = ("clinic", "assigned_to")
    autocomplete_fields = ("clinic", "assigned_to")
    readonly_fields = ("received_at", "due_at", "resolved_at")
    tenant_field = "clinic_id"
    tenant_scoped_fks = ("clinic",)

    fieldsets = (
        ("Request", {"fields": ("kind", "status", "detail")}),
        ("Requester", {"fields": ("requester_name", "requester_contact")}),
        (
            "Matching",
            {
                "fields": ("clinic", "patient_ref"),
                "description": "A request may arrive from someone whose record "
                "cannot be found. Matching it is part of handling the request, "
                "not a precondition for accepting it.",
            },
        ),
        ("Handling", {"fields": ("assigned_to", "resolution")}),
        (
            "Clock",
            {
                "fields": ("received_at", "due_at", "resolved_at"),
                "description": "90 days from receipt. The clock runs from when "
                "the request arrived, not from when someone picked it up.",
            },
        ),
    )

    actions = ["mark_resolved"]

    @display(description="Deadline", label=_OVERDUE_LABELS)
    def due_badge(self, obj: DataSubjectRequest) -> str:
        return _due_label(obj)

    def has_delete_permission(self, request, obj=None) -> bool:
        """A request that was made cannot be un-made."""
        return False

    @admin.action(description="Mark resolved")
    def mark_resolved(self, request, queryset):
        open_statuses = [DSRStatus.OPEN, DSRStatus.IN_PROGRESS]
        resolvable = queryset.filter(status__in=open_statuses).exclude(resolution="")
        without_resolution = queryset.filter(
            status__in=open_statuses, resolution=""
        ).count()

        resolved = 0
        for dsr in resolvable:
            dsr.resolve(resolution=dsr.resolution)
            resolved += 1

        if resolved:
            self.message_user(
                request, f"Resolved {resolved} request(s).", messages.SUCCESS
            )
        if without_resolution:
            self.message_user(
                request,
                f"{without_resolution} request(s) were skipped: write what was "
                "done in the resolution field first. Closing a request with no "
                "record of the outcome is not closing it.",
                messages.WARNING,
            )


@admin.register(BreachIncident)
class BreachIncidentAdmin(AuditedModelAdmin):
    """The 72-hour breach clock.

    The only use of red in this project besides clinical urgency, and it is the
    right one: an unreported breach past 72 hours is danger, not chrome
    (AUDIT 8.3).
    """

    list_display = (
        "summary",
        "status",
        "clinic",
        "records_affected",
        "detected_at",
        "due_at",
        "due_badge",
    )
    list_filter = ("status", "clinic", "detected_at")
    search_fields = ("summary", "detail")
    ordering = ("due_at",)
    list_select_related = ("clinic", "raised_by")
    autocomplete_fields = ("clinic",)
    readonly_fields = ("due_at", "reported_at", "closed_at", "created_at")
    tenant_field = "clinic_id"
    tenant_scoped_fks = ("clinic",)

    fieldsets = (
        ("Incident", {"fields": ("summary", "detail", "status", "records_affected")}),
        ("Scope", {"fields": ("clinic",)}),
        (
            "Clock",
            {
                "fields": ("detected_at", "due_at", "reported_at", "closed_at"),
                "description": "72 hours from detection. The clock runs from "
                "when the breach became known - recording detected_at honestly "
                "is the point, and a row created later does not reset it.",
            },
        ),
        ("Audit", {"fields": ("created_at",)}),
    )

    actions = ["mark_reported"]

    @display(description="Report deadline", label=_OVERDUE_LABELS)
    def due_badge(self, obj: BreachIncident) -> str:
        return _due_label(obj)

    def save_model(self, request, obj, form, change):
        if not change and obj.raised_by_id is None:
            obj.raised_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None) -> bool:
        """A breach record cannot be deleted. That is the whole point of it."""
        return False

    @admin.action(description="Mark as reported to the Board")
    def mark_reported(self, request, queryset):
        reported = 0
        for incident in queryset.filter(status=BreachStatus.OPEN):
            incident.mark_reported()
            reported += 1
        self.message_user(
            request,
            f"Marked {reported} incident(s) as reported.",
            messages.SUCCESS if reported else messages.WARNING,
        )
