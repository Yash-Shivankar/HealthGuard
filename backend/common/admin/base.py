"""Base ModelAdmin classes. Every admin in this project inherits from one.

AUDIT 8.11 lists writing these *first* as step 5, and notes that steps 5 and 6
are "the two steps people skip. Both are cheap now and expensive later."

## The four-place scoping discipline (AUDIT 8.7)

Default ``ModelAdmin`` shows every row in the table. In a multi-tenant
healthcare product that means one clinic's receptionist sees another clinic's
patients, which is a reportable DPDP breach on a 72-hour clock. Four places
need scoping, and the audit's warning is that most implementations only do the
first:

1. ``get_queryset``        - the list view; the obvious one
2. ``formfield_for_foreignkey`` - FK dropdowns, or a crafted POST assigns an
   object to another tenant's record
3. ``has_*_permission(obj)`` - a direct URL to
   ``/admin/<app>/<model>/<other-tenant-id>/change/`` bypasses ``get_queryset``
   entirely
4. the **autocomplete endpoint** - ``AutocompleteJsonView`` resolves through
   the *related* model's ModelAdmin, so a scoped admin whose related admin is
   unscoped leaks names as JSON. This class covers it by scoping
   ``get_queryset``, which is what that view calls; the test that proves it
   lives in ``common/tests/test_admin_scoping.py``.

## Fail closed

This is the rule that matters most, and it changed in Phase 4 when tenants
became real:

    A confined user with no clinic sees **nothing**, not everything.

While ``Clinic`` did not exist, ``get_tenant()`` returned ``None`` for
everybody, and "no tenant" harmlessly meant "nothing to scope by". Now that
tenants exist, treating ``None`` as unrestricted would mean a staff account
created before anyone assigned it a clinic could read every patient in the
system. So an unresolved tenant yields an empty queryset, and seeing across
clinics requires the explicit ``PLATFORM_OPS`` role - never the absence of a
membership.

``tenant_field = None`` still means "this model is global master data and is not
scoped". That is a per-model decision, not a per-user one, which is why the two
cases are kept apart.
"""

from __future__ import annotations

import logging

from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from apps.audit.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class ScopedModelAdmin(UnfoldModelAdmin):
    """A ModelAdmin that restricts every access path to the caller's tenant."""

    #: Field on *this* model carrying the tenant, e.g. ``"clinic_id"``.
    #: ``None`` means the model is global master data and is not scoped.
    tenant_field: str | None = None

    #: FK field names on this model that must be limited to the same tenant.
    tenant_scoped_fks: tuple[str, ...] = ()

    #: The model that *is* the tenant. A foreign key pointing at it is a
    #: special case: it cannot be routed through that model's own admin,
    #: because the tenant table is deliberately unscoped - scoping it would
    #: stop a clinic owner seeing their own clinic record. So an FK to the
    #: tenant is narrowed directly to the caller's tenant instead.
    #:
    #: Found by the Phase 4 isolation tests: without this, the clinic dropdown
    #: on a scoped model offered *every* clinic, and a crafted POST could move
    #: a patient to another clinic.
    tenant_model_label: str = "accounts.Clinic"

    def get_tenant(self, request):
        """The clinic this request may see, or ``None`` if unresolved.

        Populated by ``common.middleware.tenancy.TenancyMiddleware`` from the
        caller's active membership. ``None`` is **not** a licence to see
        everything - see the module docstring.
        """
        return getattr(request.user, "clinic_id", None)

    def is_unrestricted(self, request) -> bool:
        """Whether this request may see across clinics.

        True only for a superuser or the explicit ``PLATFORM_OPS`` role. It is
        deliberately **not** true merely because no tenant resolved.

        AUDIT 8.8: **do not hand out ``is_superuser``** - it bypasses every
        check below. Use ``PLATFORM_OPS`` for support access, which is narrower,
        auditable and revocable.
        """
        user = getattr(request, "user", None)
        if user is None:
            return False
        if getattr(user, "is_superuser", False):
            return True
        return bool(getattr(user, "tenant_unrestricted", False))

    def _scope(self, queryset, request):
        # Global master data: not scoped by anyone's clinic.
        if self.tenant_field is None:
            return queryset
        if self.is_unrestricted(request):
            return queryset

        tenant = self.get_tenant(request)
        if tenant is None:
            # Fail closed. An unassigned account must not inherit the whole
            # database as a side effect of having no clinic.
            logger.warning(
                "Refusing %s to a confined caller with no clinic",
                self.model._meta.label,
                extra={"event": "admin_scope_denied"},
            )
            return queryset.none()

        return queryset.filter(**{self.tenant_field: tenant})

    def _belongs_to_tenant(self, obj, request) -> bool:
        if obj is None or self.tenant_field is None:
            return True
        if self.is_unrestricted(request):
            return True

        tenant = self.get_tenant(request)
        if tenant is None:
            return False
        return getattr(obj, self.tenant_field, None) == tenant

    # --- 1. list view ------------------------------------------------------
    def get_queryset(self, request):
        """Also what ``AutocompleteJsonView`` calls, which covers place 4."""
        return self._scope(super().get_queryset(request), request)

    # --- 2. FK dropdowns ---------------------------------------------------
    # This also validates a POST: Django checks the submitted value against the
    # field's queryset, so narrowing it here is what stops a hand-rolled form
    # assigning a record to another tenant.
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name in self.tenant_scoped_fks and not self.is_unrestricted(request):
            related_model = db_field.remote_field.model
            manager = related_model._default_manager

            if related_model._meta.label == self.tenant_model_label:
                # The FK points at the tenant table itself. Offer only the
                # caller's own tenant; see tenant_model_label above.
                tenant = self.get_tenant(request)
                kwargs["queryset"] = (
                    manager.filter(pk=tenant) if tenant is not None else manager.none()
                )
            else:
                related_admin = self.admin_site._registry.get(related_model)
                if isinstance(related_admin, ScopedModelAdmin):
                    # Route through the related admin so one rule governs both.
                    kwargs["queryset"] = related_admin.get_queryset(request)
                else:
                    # No scoped admin on the related model. Refusing loudly
                    # beats rendering a dropdown that leaks other tenants.
                    logger.error(
                        "%s scopes FK %r but %s has no ScopedModelAdmin; "
                        "returning an empty queryset rather than leaking",
                        type(self).__name__,
                        db_field.name,
                        related_model._meta.label,
                    )
                    kwargs["queryset"] = manager.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # --- 3. object-level permissions ---------------------------------------
    # get_queryset does NOT protect a direct URL to another tenant's object.
    def has_view_permission(self, request, obj=None) -> bool:
        return super().has_view_permission(request, obj) and self._belongs_to_tenant(
            obj, request
        )

    def has_change_permission(self, request, obj=None) -> bool:
        return super().has_change_permission(request, obj) and self._belongs_to_tenant(
            obj, request
        )

    def has_delete_permission(self, request, obj=None) -> bool:
        return super().has_delete_permission(request, obj) and self._belongs_to_tenant(
            obj, request
        )


class AuditedModelAdmin(ScopedModelAdmin):
    """Records who looked at what, and who changed what (AUDIT 8.9).

    Django's ``LogEntry`` covers changes. Reads are the event that matters for
    a patient record, and nothing in Django records them - so this does.
    """

    #: Set False on admins where per-row read logging would be noise rather
    #: than evidence (reference data, the log itself).
    audit_reads: bool = True

    @property
    def audit_service(self) -> AuditService:
        return AuditService()

    def changelist_view(self, request, extra_context=None):
        if self.audit_reads:
            self.audit_service.record_list_view(
                actor=request.user, model=self.model, request=request
            )
        return super().changelist_view(request, extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if self.audit_reads and request.method == "GET":
            obj = self.get_object(request, object_id)
            if obj is not None:
                # Opening the change form *is* reading the record, whether or
                # not anything is subsequently saved.
                self.audit_service.record_object_view(
                    actor=request.user, obj=obj, request=request
                )
        return super().change_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self.audit_service.record_change(
            actor=request.user, obj=obj, created=not change, request=request
        )

    def delete_model(self, request, obj):
        # Recorded before the delete, while the object still has a pk.
        self.audit_service.record_delete(actor=request.user, obj=obj, request=request)
        super().delete_model(request, obj)


class ReadOnlyModelAdmin(AuditedModelAdmin):
    """Visible, never editable.

    For records that are evidence rather than data: consent artefacts, the
    access log, finalised clinical assessments. A record staff can edit stops
    being evidence (AUDIT 8.6, 8.9).
    """

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        # View permission is granted separately, so the object is still
        # readable; only saving is refused.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.display(description="Contact")
def masked_contact(obj) -> str:
    """List-display helper for the one masked field that exists today."""
    return obj.masked_contact
