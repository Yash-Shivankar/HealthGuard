"""ModelAdmin for Patient.

One admin class per file under apps/<app>/admin/, as AUDIT 8.6 asks - a real
healthcare admin runs to hundreds of lines per app and a single admin.py stops
being reviewable fast.

This is the proportionate version for the models that exist today, not the
full Unfold admin described in AUDIT 8 - that is Phase 3 and needs the
multi-tenant models from Phase 4 before most of it means anything.
"""

from django.contrib import admin

from apps.consent.models import ConsentArtefact
from apps.patients.models import Patient


class ConsentInline(admin.TabularInline):
    """Consent state visible on the patient page without a second click.

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


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("name", "masked_contact_display", "age", "bmi", "created_at")
    list_filter = ("created_at",)
    # Deliberately not `contact`: an unthrottled admin search over phone
    # numbers is a bulk-extraction endpoint (AUDIT 8.8).
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at", "bmi")
    ordering = ("-created_at",)
    list_per_page = 50
    inlines = [ConsentInline]

    fieldsets = (
        ("Person", {"fields": ("name", "contact", "age", "height_cm", "weight_kg", "bmi")}),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Contact", ordering="contact")
    def masked_contact_display(self, obj: Patient) -> str:
        """Masked in the list; the full value is on the detail page only."""
        return obj.masked_contact

    def has_delete_permission(self, request, obj=None) -> bool:
        """Deletion runs through consent withdrawal, not through the admin.

        The DPDP Rules require notice before erasure; a bulk action gives
        none. `delete_selected` also skips the model's delete(), so any future
        soft-delete or audit hook would silently not fire (AUDIT 8.8).
        """
        return False
