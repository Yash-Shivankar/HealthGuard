"""URL routes for the triage flow."""

from django.urls import path

from prediction_app import views

urlpatterns = [
    path("", views.home, name="home"),
    path("privacy/", views.privacy, name="privacy"),
    path("register/", views.register, name="register"),
    path("symptoms/", views.symptoms, name="symptoms"),
    path("predict/", views.predict, name="predict"),
    path("consent/withdraw/", views.withdraw_consent, name="withdraw_consent"),
]
