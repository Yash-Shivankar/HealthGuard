"""Attach the tenant context to every request.

Sets ``request.tenant`` and, because AUDIT 8.7's scoping reads it,
``request.user.clinic_id``. The Phase 3 admin base classes are unchanged by
this: they already asked ``get_tenant()`` for the answer, and this is where the
answer now comes from.

Must run after AuthenticationMiddleware, which is what puts ``request.user``
there in the first place.
"""

from __future__ import annotations

from apps.accounts.services.tenancy_service import DENIED, TenancyService


class TenancyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        session = getattr(request, "session", None)

        context = TenancyService().resolve(user, session)
        request.tenant = context

        if user is not None and getattr(user, "is_authenticated", False):
            # The scoping in common/admin reads user.clinic_id. Setting it on
            # the instance keeps that contract without a custom user model.
            user.clinic_id = context.clinic_id
            user.tenant_unrestricted = context.unrestricted
        return self.get_response(request)


def tenant_of(request):
    """Tenant context for a request, defaulting to denied.

    Callers outside the request cycle - a management command, a test - get a
    denied context rather than an unrestricted one.
    """
    return getattr(request, "tenant", DENIED)
