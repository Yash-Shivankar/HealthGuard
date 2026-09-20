"""ModelAdmin for ConsentArtefact.

Entirely read-only. A consent artefact is evidence of something a person did;
if staff can edit it, it stops being evidence (AUDIT 4.1, 8.9).
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.consent.models import ConsentArtefact


@admin.register(ConsentArtefact)
class ConsentArtefactAdmin(admin.ModelAdmin):
    list_display = (
        "patient_ref",
        "purpose",
        "notice_version",
        "state",
        "granted_at",
        "withdrawn_at",
    )
    list_filter = ("purpose", "notice_version", "withdrawn_at")
    search_fields = ("patient_ref",)
    ordering = ("-granted_at",)
    list_per_page = 50
    list_select_related = ("patient",)
    readonly_fields = (
        "patient",
        "patient_ref",
        "purpose",
        "notice_version",
        "granted_at",
        "withdrawn_at",
        "created_at",
    )

    @admin.display(description="State")
    def state(self, obj: ConsentArtefact) -> str:
        # Colour paired with text, never carrying the meaning alone (8.3).
        if obj.is_withdrawn:
            return format_html('<span style="color:#b45309;">Withdrawn</span>')
        return format_html('<span style="color:#15803d;">Active</span>')

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        """Never. This record outlives the patient row by design - it is what
        shows the Data Protection Board that an erasure was lawful.
        """
        return False
