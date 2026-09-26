"""ModelAdmin for ConsentArtefact.

Entirely read-only. A consent artefact is evidence of something a person did;
if staff can edit it, it stops being evidence (AUDIT 4.1, 8.9).
"""

from django.contrib import admin
from unfold.decorators import display

from apps.consent.models import ConsentArtefact
from common.admin import ReadOnlyModelAdmin


@admin.register(ConsentArtefact)
class ConsentArtefactAdmin(ReadOnlyModelAdmin):
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
    # Reading a consent artefact is not a patient-record read; logging every
    # list view here would bury the entries that matter.
    audit_reads = False
    readonly_fields = (
        "patient",
        "patient_ref",
        "purpose",
        "notice_version",
        "granted_at",
        "withdrawn_at",
        "created_at",
    )

    # Unfold renders a label with a colour token rather than inline CSS, so
    # the palette stays in one place and dark mode works (AUDIT 8.3).
    @display(
        description="State",
        label={"Active": "success", "Withdrawn": "warning"},
    )
    def state(self, obj: ConsentArtefact) -> str:
        # Colour is always paired with text, never carrying meaning alone.
        return "Withdrawn" if obj.is_withdrawn else "Active"

    # Deletion is refused by ReadOnlyModelAdmin. Restated here because the
    # reason is specific to this model: the artefact outlives the patient row
    # by design - it is what shows the Data Protection Board that an erasure
    # was lawful. Deleting it would destroy the evidence for the erasure.
