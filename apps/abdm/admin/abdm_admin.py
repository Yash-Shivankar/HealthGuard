"""Admin for the ABDM records.

The ABHA admin is the most sensitive list in the project: an ABHA resolves
nationally, so everything AUDIT 8.8 says about the contact number applies here
more strongly. Masked in the list, searchable by neither the number nor the
address, and not deletable - unlinking runs through the service, which records
why.
"""

from __future__ import annotations

from django.contrib import admin, messages
from unfold.decorators import display

from apps.abdm.models import (
    AbhaLink,
    FacilityRegistration,
    GatewayCall,
    PractitionerRegistration,
    RegistrationState,
)
from common.admin import AuditedModelAdmin, ReadOnlyModelAdmin

_STATE_LABELS = {
    RegistrationState.VERIFIED.label: "success",
    RegistrationState.PENDING.label: "warning",
    RegistrationState.FAILED.label: "warning",
    RegistrationState.UNREGISTERED.label: "info",
}


@admin.register(AbhaLink)
class AbhaLinkAdmin(AuditedModelAdmin):
    list_display = ("patient", "masked", "abha_address", "state_badge", "clinic", "created_at")
    list_filter = ("state", "clinic", "created_at")
    # Neither the number nor the address. Searching by ABHA would turn this
    # screen into a way to resolve a national identifier to a person, which is
    # the extraction path AUDIT 8.8 warns about in its milder form.
    search_fields = ("patient__name",)
    list_select_related = ("patient", "clinic")
    autocomplete_fields = ("patient",)
    readonly_fields = (
        "abha_number", "abha_address", "state", "verified_at", "last_error",
        "linked_by", "consent_artefact", "created_at", "updated_at",
    )
    ordering = ("-created_at",)
    tenant_field = "clinic_id"
    tenant_scoped_fks = ("patient", "clinic")

    fieldsets = (
        ("Patient", {"fields": ("patient", "clinic")}),
        (
            "ABHA",
            {
                "fields": ("abha_number", "abha_address", "state", "verified_at"),
                "description": "Read-only. Linking and unlinking run through "
                "AbhaService, which checks consent and records the reason.",
            },
        ),
        (
            "Provenance",
            {
                "fields": ("linked_by", "consent_artefact", "last_error"),
                "description": "The consent artefact is the specific notice the "
                "person agreed to, not merely that they agreed.",
            },
        ),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    @display(description="ABHA")
    def masked(self, obj: AbhaLink) -> str:
        return obj.masked_number

    @display(description="State", ordering="state", label=_STATE_LABELS)
    def state_badge(self, obj: AbhaLink) -> str:
        return obj.get_state_display()

    def has_add_permission(self, request) -> bool:
        # Created by the linkage flow, which checks consent first.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        """Unlinking goes through the service so the reason is recorded.

        An ABHA removed with no trace is indistinguishable from one that was
        never linked.
        """
        return False


@admin.register(FacilityRegistration)
class FacilityRegistrationAdmin(AuditedModelAdmin):
    list_display = ("clinic", "hfr_id", "state_badge", "verified_at")
    list_filter = ("state",)
    search_fields = ("clinic__name", "hfr_id")
    list_select_related = ("clinic",)
    readonly_fields = ("hfr_id", "state", "requested_at", "verified_at", "last_error")
    audit_reads = False
    # A facility registration is about the clinic, not about a patient, so it
    # is visible to whoever can see the clinic.
    tenant_field = "clinic_id"

    actions = ["register_with_hfr"]

    @display(description="State", ordering="state", label=_STATE_LABELS)
    def state_badge(self, obj: FacilityRegistration) -> str:
        return obj.get_state_display()

    def has_add_permission(self, request) -> bool:
        return False

    @admin.action(description="Register with the HFR")
    def register_with_hfr(self, request, queryset):
        from apps.abdm.services.registry_service import FacilityService
        from utils.exceptions.domain import DomainError

        service = FacilityService()
        done = failed = 0
        for registration in queryset:
            try:
                service.register(clinic=registration.clinic)
                done += 1
            except DomainError as exc:
                failed += 1
                self.message_user(request, f"{registration.clinic}: {exc.message}", messages.ERROR)

        if done:
            self.message_user(request, f"Registered {done} facility(ies).", messages.SUCCESS)
        if not done and not failed:
            self.message_user(request, "Nothing to do.", messages.WARNING)


@admin.register(PractitionerRegistration)
class PractitionerRegistrationAdmin(AuditedModelAdmin):
    list_display = ("user", "clinic", "hpr_id", "state_badge", "verified_at")
    list_filter = ("state", "clinic")
    search_fields = ("user__username", "hpr_id")
    list_select_related = ("user", "clinic")
    readonly_fields = ("hpr_id", "state", "verified_at", "last_error")
    audit_reads = False
    tenant_field = "clinic_id"
    tenant_scoped_fks = ("clinic",)

    @display(description="State", ordering="state", label=_STATE_LABELS)
    def state_badge(self, obj: PractitionerRegistration) -> str:
        return obj.get_state_display()


@admin.register(GatewayCall)
class GatewayCallAdmin(ReadOnlyModelAdmin):
    """The outbound-call record NHA's exit audit asks for.

    Read-only and append-only. No request or response bodies are stored, so
    there is nothing here to redact.
    """

    list_display = ("created_at", "operation", "outcome", "duration_ms", "clinic", "mode")
    list_filter = ("operation", "succeeded", "simulated", "created_at")
    search_fields = ("request_id", "correlation_id")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 100
    list_select_related = ("clinic",)
    audit_reads = False
    tenant_field = "clinic_id"

    @display(description="Outcome", label={"Succeeded": "success", "Failed": "warning"})
    def outcome(self, obj: GatewayCall) -> str:
        return "Succeeded" if obj.succeeded else "Failed"

    @display(description="Mode", label={"Live": "success", "Simulated": "info"})
    def mode(self, obj: GatewayCall) -> str:
        """Without this a sandbox-era record is indistinguishable from a
        production one, which would make the audit trail misleading rather
        than incomplete.
        """
        return "Simulated" if obj.simulated else "Live"
