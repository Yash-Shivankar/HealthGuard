"""Resolving which clinic a request may see.

This is the single answer to "whose data is this person allowed to touch", and
everything that scopes a query asks it.

## Fail closed

The important rule, and a correction to how the Phase 3 base class behaved when
no tenants existed yet:

    A signed-in user with no clinic and no unrestricted role sees **nothing**,
    not everything.

While ``Clinic`` did not exist, ``get_tenant()`` returned ``None`` for
everybody and "no tenant" safely meant "nothing to scope by". Now that tenants
are real, treating ``None`` as unrestricted would mean a staff account created
before anyone assigned it a clinic could read every patient in the system. So
``None`` means refused, and seeing across clinics requires the explicit
PLATFORM_OPS role.

## Switching clinics

A person with memberships at several clinics has one active at a time, held in
the session. An unrecognised or revoked id in the session is ignored rather
than trusted - the session is client-held state and a stale value must not
become an authorisation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.accounts.models import Clinic, Membership
from apps.accounts.repositories.membership_repository import MembershipRepository
from utils.exceptions.domain import DomainError

logger = logging.getLogger(__name__)

SESSION_KEY = "active_clinic_id"


@dataclass(frozen=True)
class TenantContext:
    """What one request is allowed to see."""

    clinic: Clinic | None
    membership: Membership | None
    unrestricted: bool

    @property
    def clinic_id(self) -> int | None:
        return self.clinic.pk if self.clinic else None

    @property
    def is_confined(self) -> bool:
        """True when queries must be filtered to a single clinic."""
        return not self.unrestricted

    @property
    def sees_nothing(self) -> bool:
        """A confined context with no clinic is refused, not unrestricted."""
        return self.is_confined and self.clinic is None


#: Used for anonymous requests and for anyone without a usable membership.
DENIED = TenantContext(clinic=None, membership=None, unrestricted=False)


class TenancyService:
    def __init__(self, repository: MembershipRepository | None = None):
        self.repository = repository or MembershipRepository()

    def resolve(self, user, session=None) -> TenantContext:
        """Work out the tenant context for a request."""
        if user is None or not getattr(user, "is_authenticated", False):
            return DENIED

        # A superuser is unrestricted by definition. AUDIT 8.8's advice stands:
        # do not hand this out - use PLATFORM_OPS for support access instead,
        # because that is narrower, auditable and revocable.
        if user.is_superuser:
            return TenantContext(clinic=None, membership=None, unrestricted=True)

        memberships = list(self.repository.active_for_user(user))
        if not memberships:
            logger.info(
                "Signed-in user has no active membership; access denied",
                extra={"event": "tenancy_denied", "username": user.get_username()},
            )
            return DENIED

        unrestricted = next((m for m in memberships if m.is_unrestricted), None)
        if unrestricted is not None:
            return TenantContext(
                clinic=None, membership=unrestricted, unrestricted=True
            )

        chosen = self._chosen(memberships, session)
        return TenantContext(
            clinic=chosen.clinic, membership=chosen, unrestricted=False
        )

    def _chosen(self, memberships: list[Membership], session) -> Membership:
        requested = session.get(SESSION_KEY) if session is not None else None

        if requested is not None:
            for membership in memberships:
                if membership.clinic_id == requested:
                    return membership
            # Stale or tampered: fall through to the default rather than
            # honouring it. The session is client-held state.
            logger.info(
                "Ignoring unusable active-clinic id in session",
                extra={"event": "tenancy_stale_session", "requested": requested},
            )

        return memberships[0]

    def switch(self, user, session, clinic_id: int) -> TenantContext:
        """Change the active clinic, if the user is a member of it."""
        membership = self.repository.get_active(user, clinic_id)
        if membership is None or membership.clinic_id is None:
            raise DomainError("You are not a member of that clinic.", code="not_a_member")

        session[SESSION_KEY] = membership.clinic_id
        logger.info(
            "Active clinic switched",
            extra={"event": "tenancy_switched", "clinic_id": membership.clinic_id},
        )
        return TenantContext(
            clinic=membership.clinic, membership=membership, unrestricted=False
        )

    def available_clinics(self, user) -> list[Clinic]:
        if user is None or not getattr(user, "is_authenticated", False):
            return []
        return [
            m.clinic for m in self.repository.active_for_user(user) if m.clinic_id
        ]
