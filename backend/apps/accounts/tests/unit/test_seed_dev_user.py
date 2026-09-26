"""Tests for the development sign-in account.

``TestItRefusesOutsideDevelopment`` is the set that matters. The account has a
published password, and the guard is the only thing standing between that and a
deployment holding health records.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from apps.accounts.models import Clinic, Membership, Role

pytestmark = pytest.mark.django_db

EMAIL = "dev-account@example.test"


@pytest.fixture(autouse=True)
def roles():
    call_command("seed_roles", verbosity=0)


@pytest.fixture(autouse=True)
def debug_on(settings):
    """The command is a development tool; the default fixture reflects that."""
    settings.DEBUG = True


def seed(**kwargs):
    call_command("seed_dev_user", email=EMAIL, verbosity=0, **kwargs)
    return get_user_model().objects.get(username=EMAIL)


class TestItRefusesOutsideDevelopment:
    def test_debug_false_is_refused(self, settings):
        settings.DEBUG = False
        with pytest.raises(CommandError, match="DEBUG=False"):
            call_command("seed_dev_user", email=EMAIL, verbosity=0)

    def test_nothing_is_created_when_refused(self, settings):
        settings.DEBUG = False
        with pytest.raises(CommandError):
            call_command("seed_dev_user", email=EMAIL, verbosity=0)
        assert not get_user_model().objects.filter(username=EMAIL).exists()

    def test_the_refusal_explains_the_consequence(self, settings):
        """A refusal that does not say why gets worked around rather than heeded."""
        settings.DEBUG = False
        with pytest.raises(CommandError) as exc:
            call_command("seed_dev_user", email=EMAIL, verbosity=0)
        message = str(exc.value)
        assert "2FA" in message
        assert "health records" in message

    def test_force_overrides_it(self, settings):
        """There is no safe use of this, and it is still available - a guard with
        no override gets deleted by whoever next needs it at 2am.
        """
        settings.DEBUG = False
        call_command("seed_dev_user", email=EMAIL, force=True, verbosity=0)
        assert get_user_model().objects.filter(username=EMAIL).exists()


class TestTheAccount:
    def test_it_is_created(self):
        assert seed().username == EMAIL

    def test_the_username_is_the_email(self):
        """Django authenticates on username, so the address on the sign-in form
        has to be the username or it will not work.
        """
        user = seed()
        assert user.username == user.email == EMAIL

    def test_it_can_sign_in(self):
        seed(password="Probe@123")
        assert get_user_model().objects.get(username=EMAIL).check_password("Probe@123")

    def test_it_is_staff_so_the_admin_is_reachable(self):
        assert seed().is_staff is True

    def test_it_is_not_a_superuser(self):
        """AUDIT 8.8: do not hand out is_superuser. A superuser is unrestricted,
        so it bypasses the tenancy scoping most of this app is built around -
        testing with one means testing the path no real user takes.
        """
        assert seed().is_superuser is False

    def test_superuser_is_available_but_opt_in(self):
        assert seed(superuser=True).is_superuser is True

    def test_the_password_can_be_overridden(self):
        seed(password="Different@456")
        user = get_user_model().objects.get(username=EMAIL)
        assert user.check_password("Different@456")
        assert not user.check_password("Admin@123")

    def test_the_environment_can_override_the_password(self, monkeypatch):
        monkeypatch.setenv("DEV_USER_PASSWORD", "FromEnv@789")
        seed()
        assert get_user_model().objects.get(username=EMAIL).check_password("FromEnv@789")

    def test_an_explicit_password_beats_the_environment(self, monkeypatch):
        monkeypatch.setenv("DEV_USER_PASSWORD", "FromEnv@789")
        seed(password="Explicit@123")
        assert get_user_model().objects.get(username=EMAIL).check_password("Explicit@123")


class TestItCanActuallyUseTheApp:
    """A user with no clinic sees nothing, by design - tenancy fails closed. So
    an account without a membership would look broken rather than empty.
    """

    def test_a_clinic_is_created_and_attached(self):
        user = seed()
        membership = Membership.objects.get(user=user)
        assert membership.clinic is not None
        assert membership.is_active

    def test_the_default_role_is_owner(self):
        assert Membership.objects.get(user=seed()).role == Role.OWNER

    def test_the_role_grants_real_permissions(self):
        """Permissions come from the role group, not from is_staff. Without
        seed_roles this account reaches an empty admin.
        """
        user = seed()
        assert user.has_perm("patients.view_patient")
        assert user.has_perm("ai.change_intakedraft")

    def test_it_still_cannot_delete_a_patient(self):
        """Nobody gets delete_patient (AUDIT 8.8). A convenience account must
        not be the exception that proves the rule does not hold.
        """
        assert not seed().has_perm("patients.delete_patient")

    def test_the_tenant_resolves_and_is_not_unrestricted(self):
        from apps.accounts.services.tenancy_service import TenancyService

        context = TenancyService().resolve(seed())
        assert context.clinic is not None
        assert context.unrestricted is False

    def test_a_named_clinic_is_used(self):
        seed(clinic="Some Other Clinic")
        assert Clinic.objects.filter(name="Some Other Clinic").exists()

    def test_platform_ops_gets_no_clinic(self):
        """That role is deliberately not clinic-scoped, and MembershipService
        rejects a clinic for it - creating one anyway would leave an orphan.
        """
        user = seed(role=Role.PLATFORM_OPS)
        membership = Membership.objects.get(user=user)

        assert membership.clinic is None
        assert not Clinic.objects.exists()


class TestItIsIdempotent:
    def test_running_twice_makes_one_account(self):
        seed()
        seed()
        assert get_user_model().objects.filter(username=EMAIL).count() == 1

    def test_running_twice_makes_one_membership(self):
        seed()
        seed()
        assert Membership.objects.filter(user__username=EMAIL).count() == 1

    def test_running_twice_makes_one_clinic(self):
        seed()
        seed()
        assert Clinic.objects.count() == 1

    def test_it_resets_a_changed_password(self):
        """The point of a development account is that this command gets you back
        in, including after someone changed the password by hand.
        """
        user = seed()
        user.set_password("something-else")
        user.save()

        seed()
        assert get_user_model().objects.get(username=EMAIL).check_password("Admin@123")

    def test_it_reactivates_a_disabled_account(self):
        user = seed()
        user.is_active = False
        user.save()

        assert seed().is_active is True

    def test_an_existing_different_role_is_left_alone(self):
        """Silently changing someone's role is the kind of helpfulness that
        loses an afternoon. It says so and leaves it.
        """
        user = seed(role=Role.DOCTOR)
        seed(role=Role.OWNER)

        assert Membership.objects.get(user=user).role == Role.DOCTOR
