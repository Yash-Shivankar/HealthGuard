"""API v1 routes for ABDM."""

from django.urls import path

from apps.abdm.controllers import api

app_name = "abdm"

urlpatterns = [
    path("patients/<int:pk>/abha/", api.AbhaView.as_view(), name="abha"),
    path("patients/<int:pk>/abha/verify/", api.AbhaVerifyView.as_view(), name="abha-verify"),
    path("scan-share/", api.ScanShareView.as_view(), name="scan-share"),
]
