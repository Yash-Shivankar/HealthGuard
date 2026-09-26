"""Granting and revoking access to a clinic.

Keeping this in a service rather than a signal is deliberate: a signal on
Membership.save would fire from fixtures, tests and data migrations, and group
membership is an authorisation decision that should have one visible caller.
"""

from __future__ import annotations

import logging

from apps.accounts.models import Membership, Role
from apps.accounts.repositories.membership_repository import (
    MembershipRepository,
    RoleGroupRepository,
)
from apps.audit.models import AuditAction
from apps.audit.services.audit_service import AuditService
from utils.exceptions.domain import DomainError

logger = logging.getLogger(__name__)


class MembershipService:
    def __init__(
        self,
        repository: MembershipRepository | None = None,
        groups: RoleGroupRepository | None = None,
        audit: AuditService | None = None,
    ):
        self.repository = repository or MembershipRepository()
        self.groups = groups or RoleGroupRepository()
        self.audit = audit or AuditService()

    def grant(self, *, user, clinic, role: str, actor=None, request=None) -> Membership:
        """Give someone a role at a clinic, and the matching permission group."""
        if role == Role.PLATFORM_OPS and clinic is not None:
            raise DomainError(
                "Platform operations is not clinic-scoped; grant it without a clinic.",
                code="invalid_role_scope",
            )
        if role != Role.PLATFORM_OPS and clinic is None:
            raise DomainError(
                "This role needs a clinic.", code="clinic_required"
            )

        existing = self.repository.get_active(user, clinic.pk if clinic else None)
        if existing is not None:
            raise DomainError(
                "That person already has a role at this clinic.",
                code="already_a_member",
            )

        membership = self.repository.create(user=user, clinic=clinic, role=role)
        self._sync_groups(user)

        # Staff access is a change worth recording: it is how someone came to
        # be able to read patient records at all.
        self.audit.record(
            action=AuditAction.CREATE,
            actor=actor,
            obj=membership,
            request=request,
            reason=f"granted {role}",
        )
        logger.info(
            "Membership granted",
            extra={
                "event": "membership_granted",
                "role": role,
                "clinic_id": clinic.pk if clinic else None,
            },
        )
        return membership

    def revoke(self, *, membership: Membership, actor=None, request=None) -> None:
        membership.revoke()
        self._sync_groups(membership.user)
        self.audit.record(
            action=AuditAction.UPDATE,
            actor=actor,
            obj=membership,
            request=request,
            reason="membership revoked",
        )
        logger.info(
            "Membership revoked",
            extra={"event": "membership_revoked", "membership_id": membership.pk},
        )

    def _sync_groups(self, user) -> None:
        """Make the user's groups match their active memberships exactly.

        Recomputed rather than incrementally adjusted: a revoked role must
        actually lose its permissions, and add/remove bookkeeping is where that
        kind of bug lives.
        """
        wanted_names = {
            Role.group_name(m.role) for m in self.repository.active_for_user(user)
        }
        managed = self.groups.managed_groups()
        wanted = [g for g in managed if g.name in wanted_names]

        self.groups.set_user_groups(user, wanted, managed)

        # Staff flag follows from holding any role at all: without it Django
        # refuses admin access regardless of permissions.
        should_be_staff = bool(wanted_names)
        if user.is_staff != should_be_staff:
            user.is_staff = should_be_staff
            user.save(update_fields=["is_staff"])
