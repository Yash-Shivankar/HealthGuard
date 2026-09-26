"""API v1 router.

One place that says what the public API surface is.  Versioned in the path
from the start: changing a response shape later without a version is how you
break every client at once.
"""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("auth/", include("apps.accounts.routes.api_v1")),
    path("patients/", include("apps.patients.routes.api_v1")),
    path("triage/", include("apps.triage.routes.api_v1")),
    path("abdm/", include("apps.abdm.routes.api_v1")),
    # Schema and docs. SERVE_INCLUDE_SCHEMA is off, so these describe the API
    # without exposing themselves as endpoints in it.
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="api-docs",
    ),
]
