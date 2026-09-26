"""API v1 routes for triage."""

from django.urls import path

from apps.triage.controllers import api

app_name = "triage"

urlpatterns = [
    path("symptoms/", api.SymptomListView.as_view(), name="symptoms"),
    path("check/", api.TriageCheckView.as_view(), name="check"),
    # The clinician review queue.
    path("assessments/", api.AssessmentListView.as_view(), name="assessments"),
    path(
        "assessments/<int:pk>/review/",
        api.AssessmentReviewView.as_view(),
        name="assessment-review",
    ),
]
