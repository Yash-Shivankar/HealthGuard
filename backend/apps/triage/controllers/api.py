"""JSON API controllers for triage.

HTTP only: parse, authorise, delegate, respond. Every rule these endpoints
apply lives in apps.triage.services (AUDIT 7.1).
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.filters import OrderingFilter
from rest_framework.generics import ListAPIView
from rest_framework.permissions import DjangoModelPermissions
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.audit.models import AuditAction
from apps.audit.services.audit_service import AuditService
from apps.triage.models import ReviewState, TriageAssessment
from apps.triage.repositories.assessment_repository import AssessmentRepository
from common.api.scoped import RequireClinicMixin, TenantScopedMixin

from apps.patients.services.patient_service import PatientService
from apps.triage.serializers import (
    SymptomSerializer,
    TriageRequestSerializer,
    TriageResponseSerializer,
)
from apps.triage.services import predictor
from apps.triage.services.assessment_service import AssessmentService
from common.middleware.tenancy import tenant_of
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
                clinic=tenant_of(request).clinic,
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


class AssessmentListSerializer(serializers.ModelSerializer):
    """The review queue row.

    Shows the masked patient name and the top suggestion, not the full
    differential - a queue is for deciding what to open next.
    """

    patient_name = serializers.SerializerMethodField()
    top_disease = serializers.CharField(read_only=True, allow_null=True)
    top_confidence = serializers.FloatField(read_only=True, allow_null=True)
    needs_review = serializers.BooleanField(read_only=True)
    urgency_label = serializers.CharField(
        source="get_urgency_level_display", read_only=True
    )

    class Meta:
        model = TriageAssessment
        fields = (
            "id",
            "patient",
            "patient_name",
            "symptoms",
            "days",
            "abstained",
            "top_disease",
            "top_confidence",
            "urgency_level",
            "urgency_label",
            "review_state",
            "needs_review",
            "created_at",
        )
        read_only_fields = fields

    def get_patient_name(self, obj) -> str:
        return obj.patient.name if obj.patient_id else "Anonymous"


class AssessmentListView(TenantScopedMixin, RequireClinicMixin, ListAPIView):
    """The clinician review queue for this clinic.

    Oldest first by default, because the thing that matters is what has been
    waiting longest - AUDIT 8.5's ageing indicator, as a list.
    """

    serializer_class = AssessmentListSerializer
    permission_classes = [DjangoModelPermissions]
    filter_backends = [OrderingFilter]
    ordering_fields = ["created_at", "urgency_level", "review_state"]
    ordering = ["created_at"]

    def base_queryset(self):
        return AssessmentRepository.all_for_queue()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("unreviewed") == "true":
            queryset = queryset.filter(review_state=ReviewState.UNREVIEWED)
        return queryset


class AssessmentReviewView(TenantScopedMixin, RequireClinicMixin, APIView):
    """Clinician sign-off (AUDIT 4.3: TPG 2020 requires it before a record).

    A PATCH rather than a PUT: this changes one field and must not be a way to
    rewrite the clinical content, which is append-only.
    """

    permission_classes = [DjangoModelPermissions]
    # DjangoModelPermissions resolves the model from get_queryset(), which the
    # mixin scopes - so a cross-clinic id 404s without a per-view check.
    serializer_class = AssessmentListSerializer

    def base_queryset(self):
        return AssessmentRepository.all_for_queue()

    @extend_schema(
        request=None,
        responses=AssessmentListSerializer,
        summary="Mark an assessment as reviewed by a clinician",
    )
    def patch(self, request, pk: int):
        assessment = self.get_queryset().filter(pk=pk).first()
        if assessment is None:
            return Response(
                {"error": {"code": "not_found", "message": "No such assessment."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        if assessment.review_state != ReviewState.UNREVIEWED:
            return Response(
                {
                    "error": {
                        "code": "already_reviewed",
                        "message": "That assessment has already been reviewed.",
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        # save() rather than update(): the save is what writes the history row,
        # and a sign-off with no trail of who signed it is not a sign-off.
        assessment.review_state = ReviewState.REVIEWED
        assessment.save(update_fields=["review_state"])

        AuditService().record(
            action=AuditAction.UPDATE,
            actor=request.user,
            obj=assessment,
            request=request,
            reason="clinician sign-off",
        )
        return Response(AssessmentListSerializer(assessment).data)
