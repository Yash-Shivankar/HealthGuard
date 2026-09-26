"""ModelAdmin for Patient (AUDIT 8.6, 8.8).

One admin class per file under ``apps/<app>/admin/``: a real healthcare admin
runs to hundreds of lines per app and a single ``admin.py`` stops being
reviewable fast.

The audit's rule for this file, quoted because it is the easy one to get
wrong: **"No `admin.site.register(Model)` with default options, ever. A bare
registration on a patient model exposes every field to every staff user."**
"""

from __future__ import annotations

from django.contrib import admin, messages
from simple_history.admin import SimpleHistoryAdmin
from unfold.decorators import display

from apps.audit.models import AuditAction
from apps.audit.services.audit_service import AuditService
from apps.consent.models import ConsentArtefact
from apps.patients.models import Patient
from common.admin import AuditedModelAdmin


class ConsentInline(admin.TabularInline):
    """Consent state on the patient page, without a second click.

    An unconsented or withdrawn record is a compliance problem you want to see
    immediately, not one you have to go looking for.
    """

    model = ConsentArtefact
    extra = 0
    can_delete = False
    fields = ("purpose", "notice_version", "granted_at", "withdrawn_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        # Consent is evidence of something a person did, not a row staff create.
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Patient)
class PatientAdmin(AuditedModelAdmin, SimpleHistoryAdmin):
    # The admin is the largest concentrated PII surface in the product, so
    # exposure is a design parameter here, not an afterthought (AUDIT 8.8).
    list_display = (
        "name",
        "masked_contact_display",
        "age",
        "bmi_display",
        "clinic",
        "created_at",
    )
    list_filter = ("clinic", "created_at")
    list_select_related = ("clinic",)
    # Deliberately **not** `contact`. An unthrottled admin search over phone
    # numbers is a bulk-extraction endpoint (8.8).
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at", "bmi_display")
    ordering = ("-created_at",)
    list_per_page = 50
    inlines = [ConsentInline]

    # Live as of Phase 4. The four-place discipline in ScopedModelAdmin was
    # written and tested in Phase 3 against a stand-in scope; this is the only
    # line that had to change.
    tenant_field = "clinic_id"
    tenant_scoped_fks = ("clinic",)

    fieldsets = (
        ("Clinic", {"fields": ("clinic",)}),
        (
            "Person",
            {"fields": ("name", "contact", "age", "height_cm", "weight_kg", "bmi_display")},
        ),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    actions = ["erase_under_right_to_erasure"]

    @display(description="Contact", ordering="contact")
    def masked_contact_display(self, obj: Patient) -> str:
        """Masked in the list; the full value appears on the detail page only.

        Opening that page is recorded by AuditedModelAdmin.change_view, which
        is the audit's "the unmasking is itself a logged access event" (8.8).
        """
        return obj.masked_contact

    @display(description="BMI")
    def bmi_display(self, obj: Patient) -> str:
        return f"{obj.bmi}" if obj.bmi is not None else "-"

    def has_delete_permission(self, request, obj=None) -> bool:
        """Deletion does not happen through the admin's delete button (8.8).

        The DPDP Rules require notice before erasure, and `delete_selected`
        skips the model's own `delete()`, so soft-delete and audit hooks would
        silently not fire. The reviewed path is the action below.
        """
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Disable the built-in bulk delete globally, for the reason above.
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description="Erase under right to erasure (DPDP)")
    def erase_under_right_to_erasure(self, request, queryset):
        """The one supported deletion path.

        Withdraws consent, records the erasure by reference, then removes the
        patient - which cascades their assessments and their history rows.
        What survives holds no personal data: the withdrawn ConsentArtefact
        and the AccessLogEntry.
        """
        from apps.patients.services.patient_service import PatientService

        service = PatientService()
        audit = AuditService()
        erased = 0

        for patient in queryset:
            audit.record(
                action=AuditAction.DELETE,
                actor=request.user,
                obj=patient,
                request=request,
                reason="right to erasure, actioned from admin",
            )
            service.forget(patient)
            erased += 1

        self.message_user(
            request,
            f"Erased {erased} patient record(s). Consent artefacts were retained, "
            "marked withdrawn, as evidence the erasure was requested.",
            messages.SUCCESS,
        )
