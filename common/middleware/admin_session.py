"""Shorter sessions for staff than for patients (AUDIT 8.10).

    Short session timeout with re-authentication for sensitive actions.

An admin session is a skeleton key to every patient record, so it expires on
inactivity well before an ordinary session would. The re-authentication half
of that recommendation needs a per-action flow and is noted in the README as
outstanding.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.contrib.auth import logout

logger = logging.getLogger(__name__)

LAST_ACTIVITY_KEY = "_admin_last_activity"


class AdminSessionTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout = int(getattr(settings, "ADMIN_SESSION_TIMEOUT_SECONDS", 900))
        self.admin_prefix = "/" + settings.ADMIN_URL.lstrip("/")

    def __call__(self, request):
        user = getattr(request, "user", None)
        is_staff_session = (
            user is not None
            and user.is_authenticated
            and (user.is_staff or user.is_superuser)
        )

        if is_staff_session and request.path.startswith(self.admin_prefix):
            now = time.time()
            last = request.session.get(LAST_ACTIVITY_KEY)
            if last is not None and now - last > self.timeout:
                logger.info(
                    "Staff session expired on inactivity",
                    extra={"event": "admin_session_timeout", "username": user.get_username()},
                )
                logout(request)
            else:
                request.session[LAST_ACTIVITY_KEY] = now

        return self.get_response(request)
