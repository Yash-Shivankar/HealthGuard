"""Auth and tenancy endpoints for the SPA.

Session authentication rather than tokens. For a browser SPA served from the
same site that is the safer default: the session cookie is HttpOnly, so a
cross-site script cannot read it, whereas a JWT in ``localStorage`` is readable
by any script that gets onto the page. The cost is that the SPA must send a CSRF
token, which is what ``/auth/csrf/`` is for.

Staff sign-in here deliberately does **not** satisfy the admin's 2FA gate. The
gate lives in ``HealthGuardAdminSite.has_permission`` and checks for a verified
OTP device; a session created through this endpoint has no verified device, so it
grants the clinic app and not the admin. Those are different surfaces with
different risk, and conflating them would let an API login walk into the admin.
"""

from __future__ import annotations

import logging

from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.services.tenancy_service import TenancyService
from common.middleware.tenancy import tenant_of
from utils.exceptions.domain import DomainError

logger = logging.getLogger(__name__)


class CredentialsSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"}, trim_whitespace=False)


class ClinicSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)


class SwitchClinicSerializer(serializers.Serializer):
    clinic_id = serializers.IntegerField()


class SessionSerializer(serializers.Serializer):
    """Who the caller is and what they may see."""

    username = serializers.CharField(read_only=True)
    is_staff = serializers.BooleanField(read_only=True)
    role = serializers.CharField(read_only=True, allow_null=True)
    unrestricted = serializers.BooleanField(read_only=True)
    clinic = ClinicSerializer(read_only=True, allow_null=True)
    available_clinics = ClinicSerializer(many=True, read_only=True)
    permissions = serializers.ListField(child=serializers.CharField(), read_only=True)


def _session_payload(request) -> dict:
    user = request.user
    context = tenant_of(request)
    membership = context.membership

    return {
        "username": user.get_username(),
        "is_staff": user.is_staff,
        "role": membership.role if membership else None,
        "unrestricted": context.unrestricted,
        "clinic": context.clinic,
        "available_clinics": TenancyService().available_clinics(user),
        # The SPA hides what the caller cannot do. This is presentation only -
        # the server still enforces every one of them.
        "permissions": sorted(user.get_all_permissions()),
    }


@method_decorator(ensure_csrf_cookie, name="get")
class CSRFView(APIView):
    """Hand the SPA a CSRF cookie before its first unsafe request."""

    @extend_schema(responses={200: None}, summary="Set the CSRF cookie")
    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class LoginView(APIView):
    """Sign in to the clinic app.

    Throttled on its own scope: an unthrottled login endpoint undoes the
    django-axes lockout, because axes counts attempts per view and a second
    door is a second budget.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(
        request=CredentialsSerializer,
        responses=SessionSerializer,
        summary="Sign in and start a session",
    )
    def post(self, request):
        serializer = CredentialsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # request= is required for django-axes to see the attempt at all.
        user = authenticate(
            request=request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )

        if user is None:
            logger.info(
                "Failed API sign-in",
                extra={
                    "event": "api_login_failed",
                    "username": serializer.validated_data["username"],
                },
            )
            # One message for both wrong-username and wrong-password: telling
            # them apart is an account enumeration oracle.
            return Response(
                {"error": {"code": "invalid_credentials", "message": "Incorrect username or password."}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        login(request, user)
        # The tenancy middleware ran before authentication, so the context on
        # this request is stale; resolve it again for the response.
        request.tenant = TenancyService().resolve(user, request.session)
        request.user.clinic_id = request.tenant.clinic_id
        request.user.tenant_unrestricted = request.tenant.unrestricted

        logger.info("API sign-in", extra={"event": "api_login", "username": user.get_username()})
        return Response(SessionSerializer(_session_payload(request)).data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    # request=None is explicit: this is a POST with no body, and without it
    # drf-spectacular cannot infer a serializer and warns during schema
    # generation - which surfaces as a `check --deploy` failure.
    @extend_schema(request=None, responses={204: None}, summary="End the session")
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionView(APIView):
    """What the SPA calls on load to find out who it is talking to."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=SessionSerializer, summary="The current session")
    def get(self, request):
        return Response(SessionSerializer(_session_payload(request)).data)


class SwitchClinicView(APIView):
    """Change the active clinic for a caller who belongs to more than one.

    A locum doctor working at two clinics is the case this exists for. The
    service refuses a clinic the caller is not a member of, so the id in the
    request cannot become an authorisation.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=SwitchClinicSerializer,
        responses=SessionSerializer,
        summary="Switch the active clinic",
    )
    def post(self, request):
        serializer = SwitchClinicSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            context = TenancyService().switch(
                request.user, request.session, serializer.validated_data["clinic_id"]
            )
        except DomainError as exc:
            return Response(
                {"error": {"code": exc.code, "message": exc.message}},
                status=exc.status_code,
            )

        request.tenant = context
        request.user.clinic_id = context.clinic_id
        request.user.tenant_unrestricted = context.unrestricted
        return Response(SessionSerializer(_session_payload(request)).data)
