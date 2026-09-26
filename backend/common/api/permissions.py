"""Explicit permissions for action endpoints.

``DjangoModelPermissions`` maps HTTP verbs onto model permissions - POST to
``add``, PATCH to ``change`` - which is right for a resource and wrong for an
action.

``POST /ai/drafts/1/confirm/`` is a **change** to draft 1. Under the default map
it would demand ``add_intakedraft``, which is precisely backwards: a draft
records something that happened, so nothing should be able to add one, and the
admin says so by refusing add permission outright. Granting ``add`` to make the
verb map happy would hand out the one permission the design excludes.

``POST /ai/notes/for-assessment/1/`` is worse: its queryset is assessments, so
the default map would ask for ``triage.add_triageassessment`` to draft a note.

So the permission is named on the view instead of inferred from the method. More
typing, and it means the answer to "who can call this?" is in the view rather
than in DRF's table.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission


class RequiresPermissions(BasePermission):
    """Require the permissions a view names in ``required_permissions``.

    A view with no ``required_permissions`` is **refused**, not allowed. A
    permission class that defaults open would turn a forgotten attribute into a
    public endpoint, and this project has already been bitten by a missing view
    attribute failing silently at request time (see the throttle-scope guard in
    the architecture tests).
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view) -> bool:
        required = getattr(view, "required_permissions", None)
        if not required:
            return False

        user = request.user
        if not (user and user.is_authenticated):
            return False

        return user.has_perms(tuple(required))
