"""HTML routes for intake and consent."""

from django.urls import path

from apps.patients.controllers import web

urlpatterns = [
    path("register/", web.register, name="register"),
    path("privacy/", web.privacy, name="privacy"),
    path("consent/withdraw/", web.withdraw_consent, name="withdraw_consent"),
]
