"""Custom admin site (AUDIT 8.11, step 3).

Two things this file exists for.

The small one: "HealthGuard Operations", not "Django administration". It is a
five-minute change and it is the first thing a client sees in a demo (8.4).

The larger one: the admin is a **staff tool, not a product surface** (8.1).
The doctor's daily consultation screen is React, in Phase 4. Keeping that
boundary is what stops this slowly becoming a bad EMR, and the place to state
it is the site class everyone reads first.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django_otp.admin import OTPAdminAuthenticationForm
from unfold.sites import UnfoldAdminSite

logger = logging.getLogger(__name__)


class HealthGuardAdminSite(UnfoldAdminSite):
    site_title = "HealthGuard Operations"
    site_header = "HealthGuard Operations"
    index_title = "Operations overview"
    enable_nav_sidebar = True

    # Solicits the OTP token at login. Without this the gate in
    # has_permission below would be unsatisfiable: Django's default admin
    # login form has no token field, so nobody could ever become verified and
    # the admin would simply be shut. templates/admin/login.html renders the
    # extra field, because Unfold's own login template does not.
    login_form = OTPAdminAuthenticationForm

    def has_permission(self, request) -> bool:
        """Staff access requires a verified second factor (AUDIT 8.10).

        Enforced here rather than per-view, so a new admin class cannot forget
        it. The audit's wording is "mandatory 2FA for every staff account, no
        exceptions for 'just the ops account'" - and an exception that has to
        be remembered in twelve places is not mandatory.

        ADMIN_REQUIRE_2FA can be turned off in development so a fresh clone
        can reach the admin before anyone has enrolled a device. Production
        leaves it on, and the first device is enrolled out of band:

            python manage.py addstatictoken <username>

        which prints a single-use backup token. Sign in with it, then add a
        TOTP device from the admin. This is the standard django-otp bootstrap
        and it is the answer to "how does the first person get in".
        """
        if not super().has_permission(request):
            return False

        if not getattr(settings, "ADMIN_REQUIRE_2FA", True):
            return True

        if request.user.is_verified():
            return True

        logger.warning(
            "Admin access refused: no verified second factor",
            extra={
                "event": "admin_2fa_required",
                "username": request.user.get_username(),
            },
        )
        return False


@receiver(user_logged_in)
def alert_on_staff_login(sender, request, user, **kwargs):
    """Log every staff and superuser login (AUDIT 8.10).

    "Alert on every superuser login to a channel a human reads" - this is the
    logging half. Routing it to a channel is a deployment concern (Phase 8):
    point an alert rule at ``event=admin_superuser_login``.

    An admin login is a skeleton key to every patient record in the system, so
    a superuser login is recorded at WARNING even though nothing is wrong yet.
    """
    if not (user.is_staff or user.is_superuser):
        return

    from apps.audit.models import AuditAction
    from apps.audit.services.audit_service import AuditService

    level = logging.WARNING if user.is_superuser else logging.INFO
    logger.log(
        level,
        "Admin login: %s",
        user.get_username(),
        extra={
            "event": "admin_superuser_login" if user.is_superuser else "admin_login",
            "username": user.get_username(),
            "is_superuser": user.is_superuser,
            "ip": request.META.get("REMOTE_ADDR"),
        },
    )
    AuditService().record(
        action=AuditAction.ADMIN_LOGIN,
        actor=user,
        object_type=user._meta.label,
        object_id=str(user.pk),
        request=request,
    )
