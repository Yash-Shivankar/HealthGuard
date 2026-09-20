"""HTML routes for triage."""

from django.urls import path

from apps.triage.controllers import web

urlpatterns = [
    path("", web.home, name="home"),
    path("symptoms/", web.symptoms, name="symptoms"),
    path("predict/", web.predict, name="predict"),
]
