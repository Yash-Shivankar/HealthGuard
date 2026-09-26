"""The only place ORM queries against Clinic and Membership live."""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.db.models import QuerySet

from apps.accounts.models import Clinic, Membership, Role

#: Prefix on every group this project manages. Groups without it belong to the
#: operator and are left alone by the sync in MembershipService.
MANAGED_GROUP_PREFIX = "HealthGuard: "


class MembershipRepository:
    @staticmethod
    def active_for_user(user) -> QuerySet[Membership]:
        return (
            Membership.objects.filter(user=user, is_active=True)
            .select_related("clinic")
            .order_by("clinic__name")
        )

    @staticmethod
    def get_active(user, clinic_id: int | None) -> Membership | None:
        return (
            Membership.objects.filter(user=user, clinic_id=clinic_id, is_active=True)
            .select_related("clinic")
            .first()
        )

    @staticmethod
    def create(*, user, clinic, role: str) -> Membership:
        return Membership.objects.create(user=user, clinic=clinic, role=role)

    @staticmethod
    def for_clinic(clinic) -> QuerySet[Membership]:
        return Membership.objects.filter(clinic=clinic).select_related("user")

    @staticmethod
    def has_unrestricted_role(user) -> bool:
        """Cross-clinic access, without handing out is_superuser (AUDIT 8.8)."""
        return Membership.objects.filter(
            user=user, is_active=True, role=Role.PLATFORM_OPS
        ).exists()


class ClinicRepository:
    @staticmethod
    def get_by_id(clinic_id: int) -> Clinic | None:
        return Clinic.objects.filter(pk=clinic_id).first()

    @staticmethod
    def active() -> QuerySet[Clinic]:
        return Clinic.objects.filter(is_active=True)

    @staticmethod
    def count_active() -> int:
        return Clinic.objects.filter(is_active=True).count()

    @staticmethod
    def count_active_staff_by_role(role: str) -> int:
        return (
            Membership.objects.filter(
                role=role, is_active=True, clinic__is_active=True
            )
            .values("user")
            .distinct()
            .count()
        )


class RoleGroupRepository:
    """Access to the permission groups that back the roles.

    Here rather than in the service because a service must not touch the ORM -
    the architecture test in apps/triage/tests/unit/test_architecture.py caught
    exactly this when the group lookup was written inline.
    """

    @staticmethod
    def managed_groups() -> list[Group]:
        """Every group this project owns, so a revoked role loses its grants."""
        return list(Group.objects.filter(name__startswith=MANAGED_GROUP_PREFIX))

    @staticmethod
    def set_user_groups(user, groups: list[Group], managed: list[Group]) -> None:
        """Replace the user's managed groups with exactly ``groups``.

        Groups outside the managed set are untouched: an operator may add a
        person to their own group for a reason this code does not know about.
        """
        stale = [g for g in managed if g not in groups]
        user.groups.remove(*stale)
        user.groups.add(*groups)
