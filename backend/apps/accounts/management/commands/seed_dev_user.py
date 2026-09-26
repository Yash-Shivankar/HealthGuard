"""Create a development sign-in account, with a clinic it can actually use.

    python manage.py seed_dev_user

Idempotent: run it again after resetting the database and you get the same
account back.

## This refuses to run outside development

The account it creates has a **known password**, and this is a system that holds
health records. A known-credential account reaching a real deployment would
undo the admin's 2FA gate, its IP allowlist and its lockout policy in one step,
so the command checks ``DEBUG`` and stops. ``--force`` overrides it, loudly,
because there is no way to make that safe and pretending otherwise is worse.

Override the password per-machine with ``DEV_USER_PASSWORD`` in your ``.env``
(which is gitignored) or with ``--password``. The default below is committed
deliberately, on the same basis as the local Postgres password in
``docker-compose.yml``: a development convenience that is useless without the
``DEBUG`` guard above.

## Why it is not a superuser

AUDIT 8.8 says do not hand out ``is_superuser``, and the project takes that
seriously enough that ``PLATFORM_OPS`` exists as a role rather than a flag. Two
practical reasons here too:

* a superuser is ``unrestricted``, so it bypasses the tenancy scoping that most
  of this application is built around - you would be testing the one path real
  users never take
* every screen the account can reach is then reachable because of a flag, which
  tells you nothing about whether the permissions are right

So it gets ``is_staff`` (to reach the admin) plus a real ``Membership`` at a
development clinic. ``--superuser`` is available when you genuinely need it.
"""

from __future__ import annotations

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Clinic, Role
from apps.accounts.repositories.membership_repository import MembershipRepository
from apps.accounts.services.membership_service import MembershipService

#: The account the project uses for local sign-in. Username is the email
#: address: Django authenticates on ``username``, so making them the same means
#: the address on the form is the one that works.
DEFAULT_EMAIL = "yashshivankar@outlook.com"

#: Development only - see the module docstring on why this is committed.
DEFAULT_PASSWORD = "Admin@123"

DEFAULT_CLINIC_NAME = "Development Clinic"


class Command(BaseCommand):
    help = "Create or refresh the development sign-in account and its clinic."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=DEFAULT_EMAIL)
        parser.add_argument(
            "--password",
            default=None,
            help="Overrides DEV_USER_PASSWORD and the built-in default.",
        )
        parser.add_argument(
            "--role",
            default=Role.OWNER,
            choices=[choice for choice, _ in Role.choices],
            help="Role at the development clinic. Default: owner.",
        )
        parser.add_argument(
            "--clinic",
            default=DEFAULT_CLINIC_NAME,
            help="Clinic to attach the account to; created if absent.",
        )
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Also set is_superuser. Bypasses every scoping check - see the "
            "module docstring before using it.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even when DEBUG is False. There is no safe use of this.",
        )

    def handle(self, *args, **options):
        self._check_environment(force=options["force"])

        email = options["email"].strip()
        password = (
            options["password"]
            or os.environ.get("DEV_USER_PASSWORD")
            or DEFAULT_PASSWORD
        )
        role = options["role"]

        with transaction.atomic():
            user, created = self._upsert_user(
                email=email, password=password, superuser=options["superuser"]
            )
            clinic = self._clinic_for(role, options["clinic"])
            membership = self._ensure_membership(user=user, clinic=clinic, role=role)

        self._report(
            user=user, created=created, clinic=clinic, membership=membership, role=role
        )

    # --- The guard ----------------------------------------------------------
    def _check_environment(self, *, force: bool) -> None:
        if settings.DEBUG:
            return

        if not force:
            raise CommandError(
                "Refusing to create a known-password account with DEBUG=False.\n"
                "This is a health records system: an account with a published "
                "password defeats the admin's 2FA gate, IP allowlist and lockout "
                "policy at once.\n"
                "If this really is a development machine, set DJANGO_DEBUG=True. "
                "Use --force only if you have decided the consequences are "
                "acceptable."
            )

        self.stdout.write(
            self.style.ERROR(
                "WARNING: creating a known-password account with DEBUG=False "
                "because --force was passed. Delete this account before anyone "
                "else can reach this deployment."
            )
        )

    # --- The account --------------------------------------------------------
    def _upsert_user(self, *, email: str, password: str, superuser: bool):
        User = get_user_model()

        user, created = User.objects.get_or_create(
            username=email, defaults={"email": email}
        )

        # Reset every time rather than only on create: the whole point of a
        # development account is that running this command gets you back in,
        # including after someone has changed the password by hand.
        user.email = email
        user.set_password(password)
        user.is_active = True
        # Staff, so the admin is reachable. Permissions still come from the
        # role group - is_staff alone shows an empty admin.
        user.is_staff = True
        if superuser:
            user.is_superuser = True
        user.save()

        return user, created

    def _clinic_for(self, role: str, name: str) -> Clinic | None:
        # PLATFORM_OPS is deliberately not clinic-scoped, and MembershipService
        # rejects a clinic for it. Creating one anyway would leave an orphan.
        if role == Role.PLATFORM_OPS:
            return None

        clinic, created = Clinic.objects.get_or_create(name=name)
        if created:
            self.stdout.write(f"  Created clinic: {clinic.name}")
        return clinic

    def _ensure_membership(self, *, user, clinic, role: str):
        """Attach the role, tolerating one that is already there.

        ``MembershipService.grant`` refuses a duplicate, which is correct for a
        human granting access and wrong for an idempotent seed - so the existing
        one is looked up first rather than the error being swallowed.
        """
        existing = MembershipRepository.get_active(user, clinic.pk if clinic else None)
        if existing is not None:
            if existing.role != role:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Account already has role '{existing.role}' here; "
                        f"leaving it rather than silently changing it to '{role}'. "
                        "Change it in the admin, or pass --clinic to use another."
                    )
                )
            return existing

        return MembershipService().grant(user=user, clinic=clinic, role=role)

    # --- Output -------------------------------------------------------------
    def _report(self, *, user, created, clinic, membership, role: str) -> None:
        verb = "Created" if created else "Refreshed"
        self.stdout.write(self.style.SUCCESS(f"{verb} development account {user.username}"))
        self.stdout.write(f"  Role: {membership.get_role_display()}")
        if clinic is not None:
            self.stdout.write(f"  Clinic: {clinic.name}")
        if user.is_superuser:
            self.stdout.write(
                self.style.WARNING(
                    "  is_superuser is set: this account is unrestricted and "
                    "bypasses tenancy scoping, so it does not exercise the "
                    "permissions the app actually enforces."
                )
            )

        self.stdout.write("")
        self.stdout.write("Password: unchanged from --password, DEV_USER_PASSWORD, or the")
        self.stdout.write("          documented default in this command.")
        self.stdout.write("")

        admin_url = getattr(settings, "ADMIN_URL", "admin/")
        self.stdout.write(f"Admin:    /{str(admin_url).lstrip('/')}")
        self.stdout.write("SPA:      /clinic/sign-in  (frontend dev server on :5173)")

        if getattr(settings, "ADMIN_REQUIRE_2FA", True):
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "ADMIN_REQUIRE_2FA is on, so the admin needs a verified device "
                    "as well as this password. Either:\n"
                    f"  python manage.py addstatictoken {user.username}\n"
                    "or set ADMIN_REQUIRE_2FA=False in your .env for local work."
                )
            )

        if not created:
            self.stdout.write("")
            self.stdout.write(
                "Permissions come from the role group, so run seed_roles too if "
                "you have not: python manage.py seed_roles"
            )
