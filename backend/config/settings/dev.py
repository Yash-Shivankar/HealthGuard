"""Local development settings."""

from config.settings.base import *  # noqa: F403
from config.settings.base import ALLOWED_HOSTS, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]

# Vite's dev server, so the SPA can talk to the API without a proxy. Appended
# rather than assigned, so an explicit DJANGO_CORS_ALLOWED_ORIGINS still wins.
_VITE_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
CORS_ALLOWED_ORIGINS = list(  # noqa: F405
    dict.fromkeys(globals()["CORS_ALLOWED_ORIGINS"] + _VITE_ORIGINS)
)
CSRF_TRUSTED_ORIGINS = list(  # noqa: F405
    dict.fromkeys(globals()["CSRF_TRUSTED_ORIGINS"] + _VITE_ORIGINS)
)

# Browsable API is genuinely useful alongside the SPA, so it stays on in
# development only.
REST_FRAMEWORK = {  # noqa: F405
    **globals()["REST_FRAMEWORK"],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}
