"""API v1 routes for patients."""

from django.urls import path

from apps.patients.controllers import api

app_name = "patients"

urlpatterns = [
    path("intake/", api.PatientIntakeView.as_view(), name="intake"),
    path("me/", api.CurrentPatientView.as_view(), name="me"),
]
