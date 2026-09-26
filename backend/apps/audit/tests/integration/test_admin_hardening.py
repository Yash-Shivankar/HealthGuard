"""Admin hardening tests (AUDIT 8.10).

The 2FA tests here exist because of a real trap. The gate that refuses staff
without a verified device is easy to write; what is easy to miss is that
Django's default admin login form has no token field, and Unfold's login
template renders only username and password. With either missing, the gate
becomes unsatisfiable - the admin is simply shut, and the first thing anyone
does is switch the gate off.

So these assert the whole flow, not just the refusal:

  * refused without a device
  * the login form actually solicits a token
  * the device choice is offered after the first step
  * sign-in succeeds with device + token
  * a backup token is single-use
"""

from __future__ import annotations

import ipaddress

import pytest
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

pytestmark = pytest.mark.django_db

ADMIN_LOGIN = "/internal-ops/login/"


@pytest.fixture
def root(django_user_model):
    return django_user_model.objects.create_superuser(
        username="root", password="pw-correct-horse", email="root@example.com"
    )


@pytest.fixture
def backup_token(root) -> tuple[StaticDevice, str]:
    """The out-of-band bootstrap: `manage.py addstatictoken <username>`."""
    device = StaticDevice.objects.create(user=root, name="Backup Code")
    StaticToken.objects.create(device=device, token="bootstrap1")
    return device, "bootstrap1"


class TestTwoFactorFlow:
    # pytest classes are not SimpleTestCase subclasses, so override_settings
    # cannot decorate them; the settings fixture is the pytest-django way.
    @pytest.fixture(autouse=True)
    def _require_2fa(self, settings):
        settings.ADMIN_REQUIRE_2FA = True

    def test_the_login_form_solicits_a_token(self, client):
        """Without this field the gate below can never be satisfied."""
        body = client.get(ADMIN_LOGIN).content.decode()
        assert 'name="otp_token"' in body

    def test_the_device_select_is_hidden_until_the_first_step_succeeds(self, client):
        """On a fresh page there is no authenticated user, so there are no
        devices to choose between. Rendering an empty select would invite
        someone to pick nothing and be told to pick something.
        """
        body = client.get(ADMIN_LOGIN).content.decode()
        assert "Second factor" not in body

    def test_password_alone_does_not_grant_access(self, client, root, backup_token):
        client.post(
            ADMIN_LOGIN, {"username": "root", "password": "pw-correct-horse"}
        )
        assert client.get(reverse("admin:index")).status_code == 302

    def test_the_first_step_offers_the_device_choice(self, client, root, backup_token):
        """django-otp 1.7 requires an explicit device choice - trying every
        device would record a failed attempt against each non-matching one and
        throttle devices the person never used. So sign-in is two steps.
        """
        response = client.post(
            ADMIN_LOGIN, {"username": "root", "password": "pw-correct-horse"}
        )
        device, _ = backup_token
        assert device.persistent_id in response.content.decode()

    def test_device_plus_token_grants_access(self, client, root, backup_token):
        device, token = backup_token
        response = client.post(
            ADMIN_LOGIN,
            {
                "username": "root",
                "password": "pw-correct-horse",
                "otp_device": device.persistent_id,
                "otp_token": token,
            },
        )
        assert response.status_code == 302
        assert client.get(reverse("admin:index")).status_code == 200

    def test_a_wrong_token_is_refused(self, client, root, backup_token):
        device, _ = backup_token
        client.post(
            ADMIN_LOGIN,
            {
                "username": "root",
                "password": "pw-correct-horse",
                "otp_device": device.persistent_id,
                "otp_token": "000000",
            },
        )
        assert client.get(reverse("admin:index")).status_code == 302

    def test_a_backup_token_is_single_use(self, client, root, backup_token):
        device, token = backup_token
        credentials = {
            "username": "root",
            "password": "pw-correct-horse",
            "otp_device": device.persistent_id,
            "otp_token": token,
        }
        assert client.post(ADMIN_LOGIN, credentials).status_code == 302

        client.logout()
        client.post(ADMIN_LOGIN, credentials)
        assert client.get(reverse("admin:index")).status_code == 302

    def test_a_totp_device_can_be_enrolled_once_signed_in(
        self, client, root, backup_token
    ):
        """The bootstrap path has to end somewhere real: sign in with a backup
        token, then add an authenticator app.
        """
        device, token = backup_token
        client.post(
            ADMIN_LOGIN,
            {
                "username": "root",
                "password": "pw-correct-horse",
                "otp_device": device.persistent_id,
                "otp_token": token,
            },
        )
        assert client.get("/internal-ops/otp_totp/totpdevice/add/").status_code == 200


class TestTheDevelopmentEscapeHatchActuallyWorks:
    """ADMIN_REQUIRE_2FA=False must let a password-only sign-in through.

    This is a regression suite for a real bug. ``login_form`` was set to
    django-otp's form unconditionally, and ``clean_otp`` raises
    ``token_required`` whenever no token is supplied - *regardless of whether
    the user has a device*. So with the gate off, nobody could sign in at all.

    That failure mode matters more than it looks: an escape hatch that lets
    nobody in is how a 2FA requirement gets ripped out rather than configured.
    """

    @pytest.fixture(autouse=True)
    def _no_2fa(self, settings):
        settings.ADMIN_REQUIRE_2FA = False

    def test_password_only_sign_in_succeeds(self, client, root):
        response = client.post(
            ADMIN_LOGIN, {"username": "root", "password": "pw-correct-horse"}
        )
        assert response.status_code == 302
        assert client.get(reverse("admin:index")).status_code == 200

    def test_the_login_page_does_not_ask_for_a_token(self, client):
        body = client.get(ADMIN_LOGIN).content.decode()
        assert 'name="otp_token"' not in body

    def test_turning_the_gate_back_on_restores_the_token_field(self, client, settings):
        settings.ADMIN_REQUIRE_2FA = True
        body = client.get(ADMIN_LOGIN).content.decode()
        assert 'name="otp_token"' in body

    def test_the_site_still_starts_when_unfold_assigns_login_form(self):
        """Unfold's __init__ does ``self.login_form = AuthenticationForm``.

        A read-only property turned that into an AttributeError at startup and
        the whole application failed to boot, so the setter is load-bearing.
        """
        from django.contrib.auth.forms import AuthenticationForm

        from config.admin import HealthGuardAdminSite

        site = HealthGuardAdminSite()
        site.login_form = AuthenticationForm
        assert site.login_form is AuthenticationForm


class TestIPAllowlist:
    def test_empty_allowlist_restricts_nothing(self, settings):
        """AUDIT 11.7 - will ops be on a fixed IP? - is unanswered, so this
        cannot be enabled by default without risking locking the owner out.
        """
        from common.middleware.admin_ip_allowlist import AdminIPAllowlistMiddleware

        middleware = AdminIPAllowlistMiddleware(lambda r: None)
        assert middleware.networks == []

    def test_a_non_allowlisted_address_is_refused(self, rf):
        """Exercised through the middleware directly.

        Going through the test client would be a weaker test: the middleware
        chain is built once per process from the settings as they were at
        startup, so changing ADMIN_IP_ALLOWLIST mid-test does not rebuild it,
        and the assertion would have to accept any refusal for any reason.
        """
        from django.conf import settings as django_settings

        from common.middleware.admin_ip_allowlist import AdminIPAllowlistMiddleware

        middleware = AdminIPAllowlistMiddleware(lambda r: "reached the admin")
        middleware.networks = [ipaddress.ip_network("10.8.0.0/24")]

        request = rf.get(f"/{django_settings.ADMIN_URL}")
        request.META["REMOTE_ADDR"] = "203.0.113.9"
        assert middleware(request).status_code == 403

        request.META["REMOTE_ADDR"] = "10.8.0.5"
        assert middleware(request) == "reached the admin"

    def test_the_patient_app_is_never_restricted(self, rf):
        """The allowlist guards the admin path only. Blocking patients from
        their own symptom checker because the ops VPN changed would be a far
        worse failure than the one it prevents.
        """
        from common.middleware.admin_ip_allowlist import AdminIPAllowlistMiddleware

        middleware = AdminIPAllowlistMiddleware(lambda r: "passed through")
        middleware.networks = [ipaddress.ip_network("10.8.0.0/24")]

        for path in ("/", "/symptoms/", "/api/v1/triage/symptoms/"):
            request = rf.get(path)
            request.META["REMOTE_ADDR"] = "203.0.113.9"
            assert middleware(request) == "passed through", path

    def test_it_fails_closed_on_an_unidentifiable_caller(self):
        """An allowlist that cannot identify the caller must not wave them
        through.
        """
        from common.middleware.admin_ip_allowlist import AdminIPAllowlistMiddleware

        middleware = AdminIPAllowlistMiddleware(lambda r: None)
        middleware.networks = [ipaddress.ip_network("10.8.0.0/24")]
        assert middleware._allowed(None) is False
        assert middleware._allowed("not-an-ip") is False
        assert middleware._allowed("10.8.0.5") is True

    def test_an_invalid_entry_is_ignored_rather_than_crashing_boot(self):
        from common.middleware.admin_ip_allowlist import _parse

        assert _parse(["not-a-network", "10.8.0.0/24"]) == [
            ipaddress.ip_network("10.8.0.0/24")
        ]


class TestLoginLockout:
    def test_axes_is_configured_to_lock_on_username_and_ip_together(self, settings):
        """Locking on IP alone punishes everyone behind a clinic's shared
        connection; locking on username alone lets an attacker lock a known
        account out deliberately.
        """
        assert settings.AXES_LOCKOUT_PARAMETERS == [["username", "ip_address"]]
        assert settings.AXES_FAILURE_LIMIT >= 3

    def test_the_attempted_password_is_never_recorded(self, settings):
        assert "password" in settings.AXES_SENSITIVE_PARAMETERS

    def test_the_axes_backend_runs_before_the_model_backend(self, settings):
        """So a locked-out account is refused before any credential check."""
        backends = settings.AUTHENTICATION_BACKENDS
        assert "axes" in backends[0].lower()


class TestSessionTimeout:
    def test_staff_sessions_expire_sooner_than_patient_sessions(self, settings):
        """An admin session is a skeleton key to every patient record."""
        assert settings.ADMIN_SESSION_TIMEOUT_SECONDS < settings.SESSION_COOKIE_AGE

    def test_activity_refreshes_the_window(self, client, root, settings):
        from common.middleware.admin_session import LAST_ACTIVITY_KEY

        settings.ADMIN_REQUIRE_2FA = False
        client.force_login(root)
        client.get("/internal-ops/")
        assert LAST_ACTIVITY_KEY in client.session

    def test_an_idle_staff_session_is_logged_out(self, client, root, settings):
        import time

        from common.middleware.admin_session import LAST_ACTIVITY_KEY

        settings.ADMIN_REQUIRE_2FA = False
        settings.ADMIN_SESSION_TIMEOUT_SECONDS = 1
        client.force_login(root)
        client.get("/internal-ops/")

        session = client.session
        session[LAST_ACTIVITY_KEY] = time.time() - 60
        session.save()

        # Bounced to login: the session was terminated mid-request.
        assert client.get("/internal-ops/").status_code == 302


class TestAdminPath:
    def test_the_default_path_is_not_served(self, client):
        assert client.get("/admin/").status_code == 404

    def test_the_path_comes_from_the_environment(self, settings):
        assert settings.ADMIN_URL
        assert settings.ADMIN_URL != "admin/"
