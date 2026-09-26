"""API v1 routes for auth and tenancy."""

from django.urls import path

from apps.accounts.controllers import api

app_name = "accounts"

urlpatterns = [
    path("csrf/", api.CSRFView.as_view(), name="csrf"),
    path("login/", api.LoginView.as_view(), name="login"),
    path("logout/", api.LogoutView.as_view(), name="logout"),
    path("session/", api.SessionView.as_view(), name="session"),
    path("clinic/", api.SwitchClinicView.as_view(), name="switch-clinic"),
]
