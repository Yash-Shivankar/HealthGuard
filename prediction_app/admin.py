"""Django admin registration for the triage app.

There was no admin.py anywhere in the project before this, and no admin route,
so the admin registered nothing and could not be reached (AUDIT 2.16).

This is the proportionate version for the models that exist today, not the
full admin described in AUDIT 8 - that needs the multi-tenant models from
Phase 2 before most of it means anything.  The controls that are here are the
ones that matter as soon as a single patient record exists:

* contact numbers are masked in the list view; the full value is on the detail
  page only (8.8)
* clinical/intake records cannot be deleted through the admin - deletion must
  run through a retention process with the notice period the DPDP Rules
  require, and `delete_selected` would bypass the model's own delete() anyway
  (8.8)
* consent state is visible at a glance, because an unconsented record is a
  compliance problem you want to see without clicking into it
"""

from django.contrib import admin
from django.utils.html import format_html

from prediction_app.models import UserInfo


@admin.register(UserInfo)
class UserInfoAdmin(admin.ModelAdmin):
    list_display = ("name", "masked_contact_display", "age", "consent_state", "created_at")
    list_filter = ("consent_given", "created_at")
    search_fields = ("name",)  # deliberately not contact - see below
    readonly_fields = (
        "consent_given",
        "consent_notice_version",
        "consent_at",
        "consent_withdrawn_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)
    list_per_page = 50

    fieldsets = (
        ("Person", {"fields": ("name", "contact", "age", "height_cm", "weight_kg")}),
        (
            "Consent",
            {
                "fields": (
                    "consent_given",
                    "consent_notice_version",
                    "consent_at",
                    "consent_withdrawn_at",
                ),
                "description": "Recorded at intake. Read-only: consent is evidence, "
                "not an editable field.",
            },
        ),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Contact", ordering="contact")
    def masked_contact_display(self, obj: UserInfo) -> str:
        return obj.masked_contact

    @admin.display(description="Consent")
    def consent_state(self, obj: UserInfo) -> str:
        # Colour is paired with text, never carrying the meaning alone (8.3).
        if obj.consent_withdrawn_at is not None:
            return format_html('<span style="color:#b45309;">Withdrawn</span>')
        if obj.consent_given:
            return format_html('<span style="color:#15803d;">Given</span>')
        return format_html('<span style="color:#b45309;">Missing</span>')

    def has_delete_permission(self, request, obj=None) -> bool:
        """Deletion runs through retention, not through the admin (8.8).

        The DPDP Rules require notice before erasure; a bulk action in the
        admin gives none, and `delete_selected` skips the model's delete()
        so any future soft-delete or audit hook would silently not fire.
        """
        return False
