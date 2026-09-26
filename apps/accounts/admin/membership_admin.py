"""ModelAdmin for Membership - who may see what.

Scoped by clinic, so a clinic owner manages their own staff and nobody else's.
Grants go through MembershipService rather than a bare save, because granting a
role also grants a permission group and writes an audit entry - and a row saved
without those is a person who appears to have access but does not, or worse, has
access nobody recorded giving them.
"""

from django.contrib import admin

from apps.accounts.models import Membership
from apps.accounts.services.membership_service import MembershipService
from common.admin import AuditedModelAdmin
from utils.exceptions.domain import DomainError


@admin.register(Membership)
class MembershipAdmin(AuditedModelAdmin):
    list_display = ("user", "clinic", "role", "is_active", "created_at", "revoked_at")
    list_filter = ("role", "is_active", "clinic")
    search_fields = ("user__username", "user__email", "clinic__name")
    autocomplete_fields = ("user", "clinic")
    list_select_related = ("user", "clinic")
    readonly_fields = ("created_at", "revoked_at")
    ordering = ("clinic__name", "user__username")

    tenant_field = "clinic_id"
    tenant_scoped_fks = ("clinic",)
    audit_reads = False

    actions = ["revoke_selected"]

    def save_model(self, request, obj, form, change):
        """Route a new grant through the service.

        An edit saves normally; a *new* membership has side effects (permission
        group, audit entry) that live in the service.
        """
        if change:
            return super().save_model(request, obj, form, change)

        try:
            MembershipService().grant(
                user=obj.user,
                clinic=obj.clinic,
                role=obj.role,
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            from django.contrib import messages

            self.message_user(request, exc.message, messages.ERROR)

    def has_delete_permission(self, request, obj=None) -> bool:
        """Revoke, do not delete.

        A deleted membership leaves audit entries pointing at a role nobody can
        explain.
        """
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description="Revoke selected memberships")
    def revoke_selected(self, request, queryset):
        from django.contrib import messages

        service = MembershipService()
        revoked = 0
        for membership in queryset.filter(is_active=True):
            service.revoke(membership=membership, actor=request.user, request=request)
            revoked += 1
        self.message_user(
            request,
            f"Revoked {revoked} membership(s). Permission groups were updated.",
            messages.SUCCESS if revoked else messages.WARNING,
        )
