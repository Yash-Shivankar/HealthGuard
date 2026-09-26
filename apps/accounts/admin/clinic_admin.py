"""ModelAdmin for Clinic.

Clinics are the tenant, so this admin is **not** clinic-scoped - scoping it by
the caller's clinic would let a clinic owner edit their own tenant record.
Instead it is restricted by permission: only owners and platform ops hold
`accounts.view_clinic`, and only a superuser can create one, because onboarding
a tenant is a platform decision rather than a clinic one.
"""

from django.contrib import admin

from apps.accounts.models import Clinic
from common.admin import AuditedModelAdmin


@admin.register(Clinic)
class ClinicAdmin(AuditedModelAdmin):
    list_display = ("name", "hfr_facility_id", "staff_count", "patient_count", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "hfr_facility_id")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    ordering = ("name",)

    # The tenant table itself. See the module docstring.
    tenant_field = None
    # Per-row read logging here would be noise: a clinic record holds no
    # patient data.
    audit_reads = False

    fieldsets = (
        ("Clinic", {"fields": ("name", "slug", "is_active")}),
        ("Contact", {"fields": ("contact_email", "contact_phone")}),
        (
            "ABDM",
            {
                "fields": ("hfr_facility_id",),
                "description": "Health Facility Registry id. Used from Phase 5 "
                "onwards; recorded here so the clinic record is complete first.",
            },
        ),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Staff")
    def staff_count(self, obj: Clinic) -> int:
        return obj.memberships.filter(is_active=True).count()

    @admin.display(description="Patients")
    def patient_count(self, obj: Clinic) -> int:
        return obj.patients.count()

    def has_add_permission(self, request) -> bool:
        # Onboarding a tenant is a platform action.
        return bool(getattr(request.user, "is_superuser", False))

    def has_delete_permission(self, request, obj=None) -> bool:
        """Never. Deleting a clinic would cascade or block on patient records.

        Deactivating is the supported path: the records stay, erasure runs
        through retention with its notice period (AUDIT 4.1).
        """
        return False
