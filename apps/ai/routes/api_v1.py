"""API v1 routes for AI assistance.

Two groups, deliberately separated by URL as well as by permission: ``intake/``
is what a patient's session reaches, and ``drafts/`` and ``notes/`` are the
clinician's side.
"""

from django.urls import path

from apps.ai.controllers import api

app_name = "ai"

urlpatterns = [
    # Patient-side capture.
    path("intake/describe/", api.DescribeView.as_view(), name="describe"),
    path("intake/voice/", api.VoiceIntakeView.as_view(), name="voice"),
    # Clinician-side review.
    path("drafts/", api.IntakeDraftListView.as_view(), name="drafts"),
    path(
        "drafts/<int:pk>/confirm/",
        api.IntakeDraftConfirmView.as_view(),
        name="draft-confirm",
    ),
    path(
        "drafts/<int:pk>/discard/",
        api.IntakeDraftDiscardView.as_view(),
        name="draft-discard",
    ),
    # Ambient documentation.
    path("notes/", api.ConsultationNoteListView.as_view(), name="notes"),
    path(
        "notes/for-assessment/<int:pk>/",
        api.ConsultationNoteDraftView.as_view(),
        name="note-draft",
    ),
    path(
        "notes/<int:pk>/sign-off/",
        api.ConsultationNoteSignOffView.as_view(),
        name="note-sign-off",
    ),
]
