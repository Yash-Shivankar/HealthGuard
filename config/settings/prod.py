"""Deployed settings.

Hardening is applied here unconditionally rather than behind `if not DEBUG`,
so a production deploy cannot be insecure by forgetting to flip a switch
(AUDIT 2.2, 8.10).
"""

from config.settings.base import *  # noqa: F403
from config.settings.base import env

DEBUG = False

# No default: a production box must be told which hosts it answers for, and
# should refuse to boot rather than accept any Host header.
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Set when running behind a TLS-terminating proxy (nginx, ALB, Render).
# Only enable this if the proxy actually strips a client-supplied header,
# otherwise a request can claim to be HTTPS when it is not.
if env.bool("DJANGO_TRUST_PROXY_SSL_HEADER", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# JSON by default in production: these lines go to an aggregator, not a person.
LOG_FORMAT = env.str("DJANGO_LOG_FORMAT", default="json")
LOGGING["handlers"]["console"]["formatter"] = LOG_FORMAT  # noqa: F405

# The browsable API renders patient data as HTML and is not a production
# surface.
REST_FRAMEWORK = {  # noqa: F405
    **globals()["REST_FRAMEWORK"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
