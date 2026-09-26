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

## Why this exists before there are tenants

There is no ``Clinic`` model yet - it arrives in Phase 4 with the ``accounts``
app. Building one now would be guessing at a design the audit has not settled.

So the machinery is here and tested, with the tenant lookup as the single
pluggable piece: :meth:`ScopedModelAdmin.get_tenant` returns ``None`` today, so
nothing is restricted, and Phase 4 changes one method. The discipline cannot be
skipped later because it is already written and already under test.
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

    def get_tenant(self, request):
        """The tenant this request may see. ``None`` means unrestricted.

        Phase 4 replaces the body of this method with the real lookup once
        ``accounts`` exists. Everything else in this class already routes
        through it.
        """
        return getattr(request.user, "clinic_id", None)

    def is_unrestricted(self, request) -> bool:
        """Superusers and untenanted users see everything.

        Note what this implies and AUDIT 8.8 states explicitly: **do not hand
        out ``is_superuser``**. It bypasses every check below.
        """
        if getattr(request.user, "is_superuser", False):
            return True
        return self.get_tenant(request) is None

    def _scope(self, queryset, request):
        if self.tenant_field is None or self.is_unrestricted(request):
            return queryset
        return queryset.filter(**{self.tenant_field: self.get_tenant(request)})

    def _belongs_to_tenant(self, obj, request) -> bool:
        if obj is None or self.tenant_field is None or self.is_unrestricted(request):
            return True
        return getattr(obj, self.tenant_field, None) == self.get_tenant(request)

    # --- 1. list view ------------------------------------------------------
    def get_queryset(self, request):
        """Also what ``AutocompleteJsonView`` calls, which covers place 4."""
        return self._scope(super().get_queryset(request), request)

    # --- 2. FK dropdowns ---------------------------------------------------
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name in self.tenant_scoped_fks and not self.is_unrestricted(request):
            related_admin = self.admin_site._registry.get(db_field.remote_field.model)
            if isinstance(related_admin, ScopedModelAdmin):
                # Route through the related admin so one rule governs both.
                kwargs["queryset"] = related_admin.get_queryset(request)
            else:
                # The related model has no scoped admin. Refusing loudly beats
                # rendering an unscoped dropdown that leaks other tenants.
                logger.error(
                    "%s scopes FK %r but %s has no ScopedModelAdmin; "
                    "returning an empty queryset rather than leaking",
                    type(self).__name__,
                    db_field.name,
                    db_field.remote_field.model._meta.label,
                )
                kwargs["queryset"] = db_field.remote_field.model._default_manager.none()
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
