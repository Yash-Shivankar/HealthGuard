"""ABDM endpoints for the clinic app.

All clinic-facing and all tenant-scoped: a patient never links their own ABHA
through this API, because ABDM linkage happens at a registered facility by
registered staff. That is what M1 describes and it is also the safer shape - a
public endpoint that creates national health identifiers is not something to
build without a very good reason.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.abdm.services.abha_service import AbhaService
from apps.abdm.services.scan_share_service import ScanShareService
from apps.patients.repositories.patient_repository import PatientRepository
from common.api.scoped import RequireClinicMixin, TenantScopedMixin
from common.middleware.tenancy import tenant_of
from utils.exceptions.domain import DomainError, NotFoundError


class AbhaLinkSerializer(serializers.Serializer):
    """Outbound. The number is **masked**, like the contact number.

    An API that returns full ABHA numbers is a bulk-extraction endpoint for a
    national identifier (AUDIT 8.8, applied to the more sensitive field).
    """

    abha_number = serializers.CharField(source="masked_number", read_only=True)
    abha_address = serializers.CharField(read_only=True)
    state = serializers.CharField(read_only=True)
    verified_at = serializers.DateTimeField(read_only=True)
    last_error = serializers.CharField(read_only=True)


class VerifyAbhaSerializer(serializers.Serializer):
    abha_number = serializers.CharField(max_length=32)


class ScanShareSerializer(serializers.Serializer):
    payload = serializers.CharField(read_only=True)
    token = serializers.CharField(read_only=True)
    clinic_name = serializers.CharField(read_only=True)
    hfr_id = serializers.CharField(read_only=True)
    generated_at = serializers.CharField(read_only=True)
    svg = serializers.CharField(read_only=True)
    data_uri = serializers.CharField(read_only=True)


class _PatientScopedView(TenantScopedMixin, RequireClinicMixin, APIView):
    """Resolves a patient inside the caller's clinic, or 404s.

    404 rather than 403 for a patient at another clinic: a 403 confirms the
    record exists.
    """

    permission_classes = [IsAuthenticated]

    def base_queryset(self):
        return PatientRepository.all_with_clinic()

    def patient_or_404(self, pk: int):
        patient = self.get_queryset().filter(pk=pk).first()
        if patient is None:
            raise NotFoundError("No such patient.")
        return patient


class AbhaView(_PatientScopedView):
    """Read, create or remove a patient's ABHA link."""

    @extend_schema(responses=AbhaLinkSerializer, summary="The patient's ABHA link")
    def get(self, request, pk: int):
        link = AbhaService().for_patient(self.patient_or_404(pk))
        if link is None:
            return Response({"abha_link": None})
        return Response(AbhaLinkSerializer(link).data)

    @extend_schema(
        request=None,
        responses=AbhaLinkSerializer,
        summary="Create a new ABHA for this patient",
        description=(
            "Requires consent for the `abdm_linkage` purpose specifically. "
            "Agreeing to a symptom check is not agreeing to link a national "
            "health identifier. Idempotent: a patient who already has a "
            "verified link gets that link back."
        ),
    )
    def post(self, request, pk: int):
        link = AbhaService().create(
            patient=self.patient_or_404(pk), actor=request.user, request=request
        )
        return Response(AbhaLinkSerializer(link).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        responses={204: None},
        summary="Remove the local ABHA link",
        description=(
            "Removes **our** record of the association. It does not delete the "
            "person's ABHA, which is theirs and lives in the national registry."
        ),
    )
    def delete(self, request, pk: int):
        AbhaService().unlink(
            patient=self.patient_or_404(pk), actor=request.user, request=request
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class AbhaVerifyView(_PatientScopedView):
    """Link an ABHA the person already has."""

    @extend_schema(
        request=VerifyAbhaSerializer,
        responses=AbhaLinkSerializer,
        summary="Verify and link an existing ABHA",
    )
    def post(self, request, pk: int):
        serializer = VerifyAbhaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        link = AbhaService().verify(
            patient=self.patient_or_404(pk),
            abha_number=serializer.validated_data["abha_number"],
            actor=request.user,
            request=request,
        )
        return Response(AbhaLinkSerializer(link).data)


class ScanShareView(RequireClinicMixin, APIView):
    """Generate a Scan-&-Share code for the caller's clinic.

    Not tenant-scoped by a queryset because there is nothing to look up - the
    clinic is whichever one the caller is working at, which is the only clinic
    they could legitimately print a code for.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=ScanShareSerializer,
        summary="A Scan-and-Share QR for this clinic",
        description=(
            "The QR encodes facility identity only - HFR id, clinic id and a "
            "rotating token. It carries no patient data: a QR is a broadcast "
            "and cannot be un-shared."
        ),
    )
    def get(self, request):
        clinic = tenant_of(request).clinic
        if clinic is None:
            raise DomainError(
                "Switch to a clinic before generating a code.", code="clinic_required"
            )

        code = ScanShareService().generate(clinic=clinic)
        return Response(
            ScanShareSerializer(
                {
                    "payload": code.payload,
                    "token": code.token,
                    "clinic_name": code.clinic_name,
                    "hfr_id": code.hfr_id,
                    "generated_at": code.generated_at,
                    "svg": code.svg,
                    "data_uri": code.data_uri,
                }
            ).data
        )
