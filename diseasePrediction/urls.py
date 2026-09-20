"""Root URL configuration.

The admin is mounted here for the first time.  It was in INSTALLED_APPS with
its migrations applied, but no route and no admin.py anywhere, so it was
carried as a dependency and delivered nothing (AUDIT 2.16).

It is deliberately not at /admin/.  The path comes from DJANGO_ADMIN_URL so it
can differ per environment; obscurity is weak on its own but it removes the
login page from automated scanning traffic entirely (AUDIT 8.10).
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("", include("prediction_app.urls")),
]
