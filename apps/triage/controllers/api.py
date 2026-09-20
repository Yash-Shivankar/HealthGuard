"""JSON API controllers for triage.

HTTP only: parse, authorise, delegate, respond. Every rule these endpoints
apply lives in apps.triage.services (AUDIT 7.1).
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.patients.services.patient_service import PatientService
from apps.triage.serializers import (
    SymptomSerializer,
    TriageRequestSerializer,
    TriageResponseSerializer,
)
from apps.triage.services import predictor
from apps.triage.services.assessment_service import AssessmentService
from utils.exceptions.domain import ServiceUnavailableError

INTAKE_SESSION_KEY = "patient_id"


class SymptomListView(APIView):
    """The symptom vocabulary the model accepts."""

    @extend_schema(
        responses=SymptomSerializer(many=True),
        summary="List the symptoms the triage model accepts",
    )
    def get(self, request):
        symptoms = AssessmentService().available_symptoms()
        return Response(SymptomSerializer(symptoms, many=True).data)


class TriageCheckView(APIView):
    """Run a symptom check."""

    throttle_classes = [ScopedRateThrottle]
    # Tighter than the default anon rate: an unthrottled triage endpoint is a
    # free inference service.
    throttle_scope = "triage"

    @extend_schema(
        request=TriageRequestSerializer,
        responses=TriageResponseSerializer,
        summary="Run a symptom check and return a ranked differential",
        description=(
            "Returns possible conditions ranked by symptom match, with an "
            "urgency assessment. Never a diagnosis, and never a medicine "
            "recommendation. When confidence falls below the model's abstain "
            "threshold, `abstained` is true and `candidates` is empty."
        ),
    )
    def post(self, request):
        serializer = TriageRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        patient = PatientService().find(request.session.get(INTAKE_SESSION_KEY))

        try:
            result = AssessmentService().assess(
                symptoms=serializer.validated_data["symptoms"],
                days=serializer.validated_data["days"],
                patient=patient,
            )
        except predictor.ModelUnavailable as exc:
            # Mapped to a domain error so the handler renders the standard
            # envelope rather than leaking a stack trace.
            raise ServiceUnavailableError(
                "The symptom checker is temporarily unavailable. If you need "
                "medical help now, contact a doctor - do not wait for this "
                "service."
            ) from exc

        payload = TriageResponseSerializer.from_result(result)
        return Response(
            TriageResponseSerializer(payload).data, status=status.HTTP_201_CREATED
        )
