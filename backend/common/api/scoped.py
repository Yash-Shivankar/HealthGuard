"""Tenant scoping for the API (AUDIT 8.7, applied to DRF).

**The admin scoping does not protect the API.** ``ScopedModelAdmin`` overrides
``ModelAdmin`` hooks; a DRF viewset shares none of them. An endpoint added
without this mixin is unscoped, and the audit's warning about one clinic's staff
seeing another clinic's patients applies to a JSON list exactly as it applies to
a changelist - arguably more, because a JSON list is easier to page through.

So the same discipline is restated here for DRF, with the same rule:

    A confined caller with no clinic sees **nothing**, not everything.

## What the four places become over HTTP

1. ``get_queryset``          the list endpoint
2. serializer field querysets  a write that names another tenant's object
3. object lookup             ``/api/v1/patients/<other-tenant-id>/`` must 404,
                             which it does because the lookup runs against the
                             scoped queryset rather than the manager
4. search and filter         a filter backend applied *after* the scoped
                             queryset, never against the manager

Point 3 is why this mixin scopes ``get_queryset`` rather than overriding
``get_object``: DRF's ``get_object`` calls ``get_queryset`` and raises 404 when
the lookup misses, so a scoped queryset turns a cross-tenant fetch into a 404
without any per-view code. A 404 rather than a 403 is deliberate - 403 confirms
the record exists.
"""

from __future__ import annotations

import logging

from rest_framework.exceptions import PermissionDenied

from common.middleware.tenancy import tenant_of

logger = logging.getLogger(__name__)


class TenantScopedMixin:
    """Restrict a viewset's queryset to the caller's clinic.

    Subclasses set :attr:`tenant_field`. ``None`` means the resource is not
    tenant-scoped, which must be a deliberate decision per resource - the
    default is to scope.
    """

    #: Field on the model carrying the tenant, e.g. ``"clinic_id"``.
    tenant_field: str | None = "clinic_id"

    def tenant(self):
        return tenant_of(self.request)

    def base_queryset(self):
        """The unscoped starting point.

        Exists so a controller can name a **repository** method here instead of
        setting DRF's usual ``queryset = Model.objects...``. That attribute is
        idiomatic DRF and it is also ORM access in a controller, which the
        architecture test rejects - it caught this on the first version of these
        endpoints. Overriding this hook keeps both rules satisfied.
        """
        return super().get_queryset()

    def get_queryset(self):
        queryset = self.base_queryset()

        if self.tenant_field is None:
            return queryset

        context = self.tenant()
        if context.unrestricted:
            return queryset

        if context.clinic_id is None:
            # Fail closed. An unassigned account must not inherit the whole
            # database as a side effect of having no clinic.
            logger.warning(
                "Refusing %s over the API to a caller with no clinic",
                queryset.model._meta.label,
                extra={"event": "api_scope_denied"},
            )
            return queryset.none()

        return queryset.filter(**{self.tenant_field: context.clinic_id})


class RequireClinicMixin:
    """Refuse the request outright unless the caller has a clinic.

    For endpoints where an empty list would be misleading rather than correct:
    a review queue that silently shows nothing looks like "no work to do", when
    it means "you are not attached to a clinic". Raising says which.
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        context = tenant_of(request)
        if context.unrestricted:
            return
        if context.clinic_id is None:
            raise PermissionDenied(
                "Your account is not attached to a clinic. Ask an administrator "
                "to grant you a role before using this."
            )
