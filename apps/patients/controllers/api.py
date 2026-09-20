"""JSON API controllers for patient intake."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.consent.serializers import ConsentArtefactSerializer
from apps.consent.services.consent_service import ConsentService
from apps.patients.serializers import PatientIntakeSerializer, PatientReadSerializer
from apps.patients.services.patient_service import PatientService

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

        patient = PatientService().register(
            name=data["name"],
            contact=data["contact"],
            age=data["age"],
            height_cm=data["height_cm"],
            weight_kg=data["weight_kg"],
            consent_given=data["consent"],
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
