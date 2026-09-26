"""API v1 routes for patients."""

from django.urls import path

from apps.patients.controllers import api

app_name = "patients"

urlpatterns = [
    path("intake/", api.PatientIntakeView.as_view(), name="intake"),
    path("me/", api.CurrentPatientView.as_view(), name="me"),
    # Clinic-facing, tenant-scoped. "me/" comes first so the literal path is
    # not swallowed by the <int:pk> pattern.
    path("", api.PatientListView.as_view(), name="list"),
    path("<int:pk>/", api.PatientDetailView.as_view(), name="detail"),
]
