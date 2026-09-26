"""JSON API controllers for patient intake."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import DjangoModelPermissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.patients.repositories.patient_repository import PatientRepository
from common.api.scoped import RequireClinicMixin, TenantScopedMixin

from apps.consent.serializers import ConsentArtefactSerializer
from apps.consent.services.consent_service import ConsentService
from apps.patients.serializers import PatientIntakeSerializer, PatientReadSerializer
from apps.patients.services.patient_service import PatientService
from common.middleware.tenancy import tenant_of

INTAKE_SESSION_KEY = "patient_id"


class PatientIntakeView(APIView):
    """Create a patient record, with consent.

    There is deliberately no list endpoint and no retrieve-by-id endpoint.
    Until the accounts app brings real authentication and per-clinic scoping
    in Phase 4, an endpoint that returns patient rows to an anonymous caller
    would be a cross-tenant leak with no tenants (AUDIT 8.7).
    """

    @extend_schema(
        request=PatientIntakeSerializer,
        responses=PatientReadSerializer,
        summary="Register intake details and record consent",
    )
    def post(self, request):
        serializer = PatientIntakeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # An intake taken by signed-in clinic staff belongs to their clinic;
        # a self-service intake from the public page belongs to none. Read from
        # the resolved tenant rather than accepting a clinic in the payload -
        # a client-supplied tenant id is a cross-tenant write waiting to happen.
        patient = PatientService().register(
            name=data["name"],
            contact=data["contact"],
            age=data["age"],
            height_cm=data["height_cm"],
            weight_kg=data["weight_kg"],
            consent_given=data["consent"],
            clinic=tenant_of(request).clinic,
        )

        # Only the row id goes into the session - never patient data.
        request.session[INTAKE_SESSION_KEY] = patient.pk

        return Response(
            PatientReadSerializer(patient).data, status=status.HTTP_201_CREATED
        )


class CurrentPatientView(APIView):
    """Read or erase the record belonging to this session.

    Scoped to the session rather than taking an id, so one caller can never
    address another person's record.
    """

    @extend_schema(
        responses=PatientReadSerializer,
        summary="The intake record held in this session",
    )
    def get(self, request):
        patient = PatientService().find(request.session.get(INTAKE_SESSION_KEY))
        if patient is None:
            return Response({"patient": None, "consent": []})

        return Response(
            {
                "patient": PatientReadSerializer(patient).data,
                "consent": ConsentArtefactSerializer(
                    ConsentService().artefacts_for(patient=patient), many=True
                ).data,
            }
        )

    @extend_schema(
        responses={204: None},
        summary="Withdraw consent and erase this session's intake record",
        description=(
            "Withdrawal must be as easy as giving consent (DPDP Act 2023). "
            "The patient record is erased; the consent artefact is retained, "
            "marked withdrawn, as evidence the erasure was requested."
        ),
    )
    def delete(self, request):
        service = PatientService()
        patient = service.find(request.session.get(INTAKE_SESSION_KEY))
        if patient is not None:
            service.forget(patient)
            request.session.pop(INTAKE_SESSION_KEY, None)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PatientListView(TenantScopedMixin, RequireClinicMixin, ListAPIView):
    """Patients at the caller's clinic.

    This endpoint deliberately did not exist until Phase 4. The Phase 2 note
    said so plainly: before tenants and authentication, a patient list would
    have been a cross-tenant leak with no tenants. Now it is scoped, and the
    scoping is asserted in apps/accounts/tests/integration/test_api_isolation.py.

    ``RequireClinicMixin`` rather than an empty list: a clinician shown zero
    patients would read that as "none registered", when it means "your account
    has no clinic".
    """

    serializer_class = PatientReadSerializer
    permission_classes = [DjangoModelPermissions]
    filter_backends = [SearchFilter, OrderingFilter]
    # Name only. Searching by contact number would make this a way to look
    # someone up from a number, which is the extraction path AUDIT 8.8 warns
    # about - and the serializer masks the number on the way out anyway.
    search_fields = ["name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def base_queryset(self):
        return PatientRepository.all_with_clinic()


class PatientDetailView(TenantScopedMixin, RequireClinicMixin, RetrieveAPIView):
    """One patient.

    A record at another clinic returns **404, not 403**: 403 would confirm that
    the record exists. The lookup runs against the scoped queryset, so this
    needs no per-view check.
    """

    serializer_class = PatientReadSerializer
    permission_classes = [DjangoModelPermissions]

    def base_queryset(self):
        return PatientRepository.all_with_clinic()
