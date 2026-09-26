"""Tests for tenant resolution and role grants."""

from __future__ import annotations

import pytest

from apps.accounts.models import Clinic, Membership, Role
from apps.accounts.services.membership_service import MembershipService
from apps.accounts.services.tenancy_service import SESSION_KEY, TenancyService
from utils.exceptions.domain import DomainError

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinics():
    return {
        "north": Clinic.objects.create(name="North Clinic"),
        "south": Clinic.objects.create(name="South Clinic"),
    }


@pytest.fixture
def service() -> TenancyService:
    return TenancyService()


@pytest.fixture
def grants() -> MembershipService:
    return MembershipService()


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="staff", password="pw")


class TestResolve:
    def test_an_anonymous_caller_is_denied(self, service):
        from django.contrib.auth.models import AnonymousUser

        context = service.resolve(AnonymousUser())
        assert context.sees_nothing
        assert context.clinic_id is None

    def test_a_user_with_no_membership_sees_nothing(self, service, user):
        """The fail-closed case. Before tenants existed, no clinic meant
        "nothing to scope by" and everything passed through; now it means
        refused.
        """
        context = service.resolve(user)
        assert context.sees_nothing
        assert context.is_confined
        assert not context.unrestricted

    def test_a_single_membership_resolves_to_that_clinic(
        self, service, grants, user, clinics
    ):
        grants.grant(user=user, clinic=clinics["north"], role=Role.FRONT_DESK)
        context = service.resolve(user)
        assert context.clinic_id == clinics["north"].pk
        assert context.is_confined
        assert not context.sees_nothing

    def test_a_superuser_is_unrestricted(self, service, django_user_model):
        root = django_user_model.objects.create_superuser(
            username="root", password="pw", email="r@example.com"
        )
        context = service.resolve(root)
        assert context.unrestricted
        assert not context.is_confined

    def test_platform_ops_is_unrestricted_without_being_a_superuser(
        self, service, grants, user
    ):
        """AUDIT 8.8: do not hand out is_superuser. Cross-tenant support access
        is a role, which can be audited and revoked.
        """
        grants.grant(user=user, clinic=None, role=Role.PLATFORM_OPS)
        context = service.resolve(user)
        assert context.unrestricted
        assert not user.is_superuser

    def test_a_revoked_membership_does_not_resolve(
        self, service, grants, user, clinics
    ):
        membership = grants.grant(
            user=user, clinic=clinics["north"], role=Role.FRONT_DESK
        )
        grants.revoke(membership=membership)
        assert service.resolve(user).sees_nothing


class TestMultipleClinics:
    """A locum doctor works at more than one clinic - the reason Membership is
    a join rather than a field on the user.
    """

    @pytest.fixture
    def locum(self, grants, user, clinics):
        grants.grant(user=user, clinic=clinics["north"], role=Role.DOCTOR)
        grants.grant(user=user, clinic=clinics["south"], role=Role.DOCTOR)
        return user

    def test_one_clinic_is_active_at_a_time(self, service, locum):
        context = service.resolve(locum, session={})
        assert context.clinic_id is not None
        assert context.is_confined

    def test_the_session_selects_which(self, service, locum, clinics):
        session = {SESSION_KEY: clinics["south"].pk}
        assert service.resolve(locum, session).clinic_id == clinics["south"].pk

    def test_a_stale_session_value_is_ignored_not_trusted(
        self, service, locum, clinics
    ):
        """The session is client-held state; a stale id must not become an
        authorisation.
        """
        session = {SESSION_KEY: 999999}
        context = service.resolve(locum, session)
        assert context.clinic_id in {clinics["north"].pk, clinics["south"].pk}

    def test_a_clinic_the_user_does_not_belong_to_is_ignored(
        self, service, grants, django_user_model, clinics
    ):
        outsider = django_user_model.objects.create_user(username="out", password="pw")
        grants.grant(user=outsider, clinic=clinics["north"], role=Role.DOCTOR)

        session = {SESSION_KEY: clinics["south"].pk}
        assert service.resolve(outsider, session).clinic_id == clinics["north"].pk

    def test_switching_requires_membership(self, service, grants, user, clinics):
        grants.grant(user=user, clinic=clinics["north"], role=Role.DOCTOR)
        with pytest.raises(DomainError):
            service.switch(user, {}, clinics["south"].pk)

    def test_switching_to_a_clinic_you_belong_to_works(self, service, locum, clinics):
        session = {}
        context = service.switch(locum, session, clinics["south"].pk)
        assert context.clinic_id == clinics["south"].pk
        assert session[SESSION_KEY] == clinics["south"].pk


class TestGrants:
    def test_a_role_grants_the_matching_permission_group(
        self, grants, user, clinics
    ):
        from django.core.management import call_command

        call_command("seed_roles", verbosity=0)
        grants.grant(user=user, clinic=clinics["north"], role=Role.FRONT_DESK)

        user.refresh_from_db()
        assert user.is_staff, "a role must grant admin access"
        assert user.has_perm("patients.add_patient")
        assert not user.has_perm("triage.view_triageassessment"), (
            "front desk does not need the prediction log"
        )

    def test_revoking_removes_the_permissions(self, grants, user, clinics):
        from django.core.management import call_command

        call_command("seed_roles", verbosity=0)
        membership = grants.grant(
            user=user, clinic=clinics["north"], role=Role.FRONT_DESK
        )
        grants.revoke(membership=membership)

        user = type(user).objects.get(pk=user.pk)
        assert not user.is_staff
        assert not user.has_perm("patients.add_patient")

    def test_a_second_role_at_the_same_clinic_is_refused(
        self, grants, user, clinics
    ):
        grants.grant(user=user, clinic=clinics["north"], role=Role.FRONT_DESK)
        with pytest.raises(DomainError):
            grants.grant(user=user, clinic=clinics["north"], role=Role.DOCTOR)

    def test_platform_ops_cannot_be_scoped_to_a_clinic(self, grants, user, clinics):
        with pytest.raises(DomainError):
            grants.grant(
                user=user, clinic=clinics["north"], role=Role.PLATFORM_OPS
            )

    def test_a_clinic_role_needs_a_clinic(self, grants, user):
        with pytest.raises(DomainError):
            grants.grant(user=user, clinic=None, role=Role.DOCTOR)

    def test_the_database_also_refuses_a_clinicless_doctor(self, user):
        """Enforced as a constraint, not only in the service: a clinic-less
        doctor would be invisible to every scoped query and hard to notice.
        """
        from django.db.utils import IntegrityError

        with pytest.raises(IntegrityError):
            Membership.objects.create(user=user, clinic=None, role=Role.DOCTOR)

    def test_an_unmanaged_group_is_left_alone(self, grants, user, clinics):
        """An operator may add someone to their own group for a reason this
        code does not know about.
        """
        from django.contrib.auth.models import Group
        from django.core.management import call_command

        call_command("seed_roles", verbosity=0)
        theirs = Group.objects.create(name="Fire wardens")
        user.groups.add(theirs)

        membership = grants.grant(
            user=user, clinic=clinics["north"], role=Role.FRONT_DESK
        )
        grants.revoke(membership=membership)

        assert theirs in user.groups.all()
