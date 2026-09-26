"""ModelAdmin for the access log (AUDIT 8.9).

    Surface both in the admin as read-only. When the Data Protection Board asks
    what happened, you open a screen rather than grep server logs.

Read-only is not a convenience here. A log staff can edit is not evidence, so
add, change and delete are all refused, and the model itself raises on any
attempt to modify a saved row.
"""

from __future__ import annotations

from django.contrib import admin
from unfold.decorators import display

from apps.audit.models import AccessLogEntry, AuditAction
from common.admin import ReadOnlyModelAdmin

# Reads are routine; writes, erasures and unmasking are the entries an auditor
# looks for. Red is reserved for clinical danger, so nothing here uses it.
_ACTION_LABELS = {
    AuditAction.VIEW_LIST.label: "info",
    AuditAction.VIEW_OBJECT.label: "info",
    AuditAction.ADMIN_LOGIN.label: "info",
    AuditAction.CREATE.label: "success",
    AuditAction.UPDATE.label: "warning",
    AuditAction.DELETE.label: "warning",
    AuditAction.UNMASK.label: "warning",
    AuditAction.EXPORT.label: "warning",
    AuditAction.CONSENT_WITHDRAWN.label: "warning",
}


@admin.register(AccessLogEntry)
class AccessLogEntryAdmin(ReadOnlyModelAdmin):
    list_display = (
        "created_at",
        "actor_display",
        "action_badge",
        "target",
        "ip_address",
        "request_id",
    )
    list_filter = ("action", "object_type", "created_at")
    # Searching by actor and object id, not by anything that would turn this
    # screen into a way to look up people (AUDIT 8.8).
    search_fields = ("actor_label", "object_id", "request_id")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 100
    list_select_related = ("actor",)

    # Reading the audit log must not itself generate audit-log entries: one
    # visit would otherwise append a row, which appends on the next visit, and
    # the signal drowns in its own noise.
    audit_reads = False

    @display(description="Actor", ordering="actor_label")
    def actor_display(self, obj: AccessLogEntry) -> str:
        return obj.actor_label or "system"

    @display(description="Action", ordering="action", label=_ACTION_LABELS)
    def action_badge(self, obj: AccessLogEntry) -> str:
        return obj.get_action_display()

    @display(description="Target")
    def target(self, obj: AccessLogEntry) -> str:
        if not obj.object_type:
            return "-"
        # Deliberately the type and id, never a resolved label: resolving it
        # would put the subject's name back into a table that exists partly to
        # outlive their erasure.
        return f"{obj.object_type} #{obj.object_id}" if obj.object_id else obj.object_type
