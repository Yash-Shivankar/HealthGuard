"""JSON API controllers for AI assistance.

HTTP only: parse, authorise, delegate, respond. Every rule these endpoints apply
lives in ``apps.ai.services`` (AUDIT 7.1).

Two things about the shape of this surface.

**Describing a problem is a patient action; confirming a draft is a clinician
action.** So the two halves have different permissions: the describe and voice
endpoints are reachable by whoever is running the intake, and the review queue,
confirmation and sign-off endpoints require a clinic and model permissions. A
single endpoint that did both would have to take the weaker of the two.

**The review endpoints are tenant-scoped.** The admin's scoping does not protect
DRF, and an intake draft carries a patient's own words - which makes an unscoped
list of them worse than an unscoped list of assessments.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.filters import OrderingFilter
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import DjangoModelPermissions
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.ai.models import DraftState
from apps.ai.repositories.ai_repository import (
    ConsultationNoteRepository,
    IntakeDraftRepository,
)
from apps.ai.serializers import (
    ConfirmRequestSerializer,
    ConsultationNoteSerializer,
    DescribeRequestSerializer,
    DiscardRequestSerializer,
    IntakeDraftSerializer,
    SignOffRequestSerializer,
    VoiceRequestSerializer,
)
from apps.ai.services.extraction_service import ExtractionService
from apps.ai.services.intake_service import IntakeService
from apps.ai.services.summary_service import SummaryService
from apps.ai.services.voice_service import VoiceIntakeService
from apps.patients.services.patient_service import PatientService
from apps.triage.serializers import TriageResponseSerializer
from common.api.permissions import RequiresPermissions
from common.api.scoped import RequireClinicMixin, TenantScopedMixin
from common.middleware.tenancy import tenant_of

INTAKE_SESSION_KEY = "patient_id"


class DescribeView(APIView):
    """Turn a typed description into a draft symptom list."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai_intake"

    @extend_schema(
        request=DescribeRequestSerializer,
        responses=IntakeDraftSerializer,
        summary="Extract canonical symptoms from a free-text description",
        description=(
            "Reads a description in English, Hindi or Marathi and returns the "
            "symptoms it maps to, with the phrase that produced each one. This "
            "does **not** run the symptom checker: the result is a draft that a "
            "clinician must confirm first. Nothing here names a condition."
        ),
    )
    def post(self, request):
        serializer = DescribeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        patient = PatientService().find(request.session.get(INTAKE_SESSION_KEY))

        draft = ExtractionService().extract(
            text=serializer.validated_data["text"],
            language=serializer.validated_data["language"],
            patient=patient,
            clinic=tenant_of(request).clinic,
        )
        return Response(
            IntakeDraftSerializer(draft).data, status=status.HTTP_201_CREATED
        )


class VoiceIntakeView(APIView):
    """Transcribe a recording and turn it into a draft symptom list."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai_voice"
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        request=VoiceRequestSerializer,
        responses=IntakeDraftSerializer,
        summary="Transcribe a recording and extract canonical symptoms",
        description=(
            "The recording is transcribed, the transcript is normalised into "
            "canonical symptoms, and the audio is dropped - it is never stored. "
            "With no speech provider configured the transcriber returns canned "
            "text and the draft is marked `simulated`, which cannot be confirmed "
            "into a record."
        ),
    )
    def post(self, request):
        serializer = VoiceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        patient = PatientService().find(request.session.get(INTAKE_SESSION_KEY))

        draft = VoiceIntakeService().intake(
            audio=serializer.validated_data["audio"].read(),
            language=serializer.validated_data["language"],
            patient=patient,
            clinic=tenant_of(request).clinic,
        )
        return Response(
            IntakeDraftSerializer(draft).data, status=status.HTTP_201_CREATED
        )


class IntakeDraftListView(TenantScopedMixin, RequireClinicMixin, ListAPIView):
    """The intake drafts waiting for a clinician in this clinic.

    Oldest first, like the assessment queue: what matters is what has been
    waiting longest.
    """

    serializer_class = IntakeDraftSerializer
    permission_classes = [DjangoModelPermissions]
    filter_backends = [OrderingFilter]
    ordering_fields = ["created_at", "state"]
    ordering = ["created_at"]

    def base_queryset(self):
        return IntakeDraftRepository.all_for_queue()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("pending") == "true":
            queryset = queryset.filter(state=DraftState.DRAFT)
        return queryset


class IntakeDraftConfirmView(TenantScopedMixin, RequireClinicMixin, APIView):
    """Clinician sign-off on a draft, which runs the confirmed symptoms.

    Returns the resulting assessment rather than the draft, because the caller's
    next screen is the result - and because the response makes the two-sign-off
    rule visible: the assessment comes back `unreviewed`, needing its own review.
    """

    # A change to the draft, not an addition of one - see
    # common/api/permissions.py for why the verb map is wrong here.
    permission_classes = [RequiresPermissions]
    required_permissions = ("ai.change_intakedraft",)
    serializer_class = IntakeDraftSerializer

    def base_queryset(self):
        return IntakeDraftRepository.all_for_queue()

    @extend_schema(
        request=ConfirmRequestSerializer,
        responses=TriageResponseSerializer,
        summary="Confirm an intake draft and run the symptom check",
        description=(
            "Confirms that these are the patient's symptoms and runs them "
            "through the deterministic model. The resulting assessment is "
            "returned **unreviewed**: confirming the symptoms is not the same "
            "judgement as agreeing with the differential, and each needs its own "
            "sign-off. A draft from a simulated provider is refused."
        ),
    )
    def post(self, request, pk: int):
        draft = self.get_queryset().filter(pk=pk).first()
        if draft is None:
            return _not_found("No such intake draft.")

        serializer = ConfirmRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = IntakeService().confirm(
            draft=draft,
            symptoms=serializer.validated_data.get("symptoms"),
            days=serializer.validated_data.get("days"),
            actor=request.user,
            request=request,
        )
        payload = TriageResponseSerializer.from_result(result)
        return Response(
            TriageResponseSerializer(payload).data, status=status.HTTP_201_CREATED
        )


class IntakeDraftDiscardView(TenantScopedMixin, RequireClinicMixin, APIView):
    """Reject a draft."""

    permission_classes = [RequiresPermissions]
    required_permissions = ("ai.change_intakedraft",)
    serializer_class = IntakeDraftSerializer

    def base_queryset(self):
        return IntakeDraftRepository.all_for_queue()

    @extend_schema(
        request=DiscardRequestSerializer,
        responses=IntakeDraftSerializer,
        summary="Discard an intake draft",
    )
    def post(self, request, pk: int):
        draft = self.get_queryset().filter(pk=pk).first()
        if draft is None:
            return _not_found("No such intake draft.")

        serializer = DiscardRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        draft = IntakeService().discard(
            draft=draft,
            actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
            request=request,
        )
        return Response(IntakeDraftSerializer(draft).data)


class ConsultationNoteListView(TenantScopedMixin, RequireClinicMixin, ListAPIView):
    """Consultation note drafts for this clinic."""

    serializer_class = ConsultationNoteSerializer
    permission_classes = [DjangoModelPermissions]
    filter_backends = [OrderingFilter]
    ordering_fields = ["created_at", "state"]
    ordering = ["-created_at"]

    def base_queryset(self):
        return ConsultationNoteRepository.all_for_queue()


class ConsultationNoteDraftView(TenantScopedMixin, RequireClinicMixin, APIView):
    """Draft a consultation note for an assessment.

    Scoped on the **assessment**, not the note: the note does not exist yet, and
    the thing that must belong to the caller's clinic is the record being
    summarised.
    """

    permission_classes = [RequiresPermissions]
    # The queryset is assessments, so the verb map would ask for
    # triage.add_triageassessment to draft a note. What is actually being created
    # is a ConsultationNote.
    required_permissions = ("ai.add_consultationnote", "triage.view_triageassessment")
    serializer_class = ConsultationNoteSerializer
    tenant_field = "clinic_id"

    def base_queryset(self):
        from apps.triage.repositories.assessment_repository import AssessmentRepository

        return AssessmentRepository.all_for_queue()

    @extend_schema(
        request=None,
        responses=ConsultationNoteSerializer,
        summary="Draft a consultation note for an assessment",
        description=(
            "Builds a note from the recorded encounter. It carries no treatment "
            "or medication advice - a draft that mentioned either is refused "
            "rather than stored, because TPG 2020 forbids this system "
            "prescribing."
        ),
    )
    def post(self, request, pk: int):
        assessment = self.get_queryset().filter(pk=pk).first()
        if assessment is None:
            return _not_found("No such assessment.")

        intake_draft = assessment.intake_drafts.first()
        note = SummaryService().draft_for(
            assessment=assessment, intake_draft=intake_draft
        )
        return Response(
            ConsultationNoteSerializer(note).data, status=status.HTTP_201_CREATED
        )


class ConsultationNoteSignOffView(TenantScopedMixin, RequireClinicMixin, APIView):
    """Clinician sign-off on a consultation note."""

    permission_classes = [RequiresPermissions]
    required_permissions = ("ai.change_consultationnote",)
    serializer_class = ConsultationNoteSerializer

    def base_queryset(self):
        return ConsultationNoteRepository.all_for_queue()

    @extend_schema(
        request=SignOffRequestSerializer,
        responses=ConsultationNoteSerializer,
        summary="Sign off a consultation note",
    )
    def post(self, request, pk: int):
        note = self.get_queryset().filter(pk=pk).first()
        if note is None:
            return _not_found("No such consultation note.")

        serializer = SignOffRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        note = SummaryService().sign_off(
            note=note,
            actor=request.user,
            clinician_text=serializer.validated_data.get("clinician_text", ""),
            request=request,
        )
        return Response(ConsultationNoteSerializer(note).data)


def _not_found(message: str) -> Response:
    return Response(
        {"error": {"code": "not_found", "message": message}},
        status=status.HTTP_404_NOT_FOUND,
    )
