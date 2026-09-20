"""Local development settings."""

from config.settings.base import *  # noqa: F403
from config.settings.base import ALLOWED_HOSTS, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]

# Browsable API is genuinely useful while the frontend decision (AUDIT 11.2)
# is still open, so it is on in development only.
REST_FRAMEWORK = {  # noqa: F405
    **globals()["REST_FRAMEWORK"],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}
