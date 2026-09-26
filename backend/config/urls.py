"""Root URL configuration.

Two surfaces over one set of services: server-rendered pages for the patient
flow, and /api/v1/ for anything that consumes JSON.  Both call the same
service layer, so they cannot drift (AUDIT 7.1).

The admin is deliberately not at /admin/.  The path comes from
DJANGO_ADMIN_URL so it differs per environment; obscurity is weak on its own
but it removes the login page from automated scanning traffic entirely
(AUDIT 8.10).
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/v1/", include("config.api")),
    # HTML surface last: its root pattern would otherwise shadow everything.
    path("", include("apps.patients.routes.urls")),
    path("", include("apps.triage.routes.urls")),
]
