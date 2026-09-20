"""Settings shared by every environment.

Split into base/dev/prod/test per AUDIT 7.1.  ``base`` holds what is true
everywhere; the environment modules below it hold only what differs.  Nothing
in any of them may contain a credential - everything sensitive comes from the
environment (see ``.env.example``).

Audit references:
  2.2   secrets from env, DEBUG off by default, explicit ALLOWED_HOSTS
  2.5   every path derived from BASE_DIR, never the process CWD
  2.13  real structured logging instead of print() and bare except
  7.1   settings split, apps/ layout, DRF
  8.10  the admin is not served from /admin/
  11.1  Postgres is the target; DATABASE_URL makes it a config change
"""

from pathlib import Path

import environ

# --- Paths ------------------------------------------------------------------
# config/settings/base.py -> config/settings -> config -> repository root.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

DATA_DIR = BASE_DIR / "data"
ML_ARTIFACT_DIR = BASE_DIR / "ml" / "artifacts"

# --- Environment ------------------------------------------------------------
env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    DJANGO_SECURE_SSL_REDIRECT=(bool, True),
    DJANGO_SECURE_HSTS_SECONDS=(int, 31536000),
    DJANGO_ADMIN_URL=(str, "internal-ops/"),
    DJANGO_LOG_FORMAT=(str, "console"),
    DJANGO_LOG_LEVEL=(str, "INFO"),
    TRIAGE_ABSTAIN_THRESHOLD=(float, 0.40),
)
environ.Env.read_env(BASE_DIR / ".env")

# Fails loudly if unset.  A missing key must not fall back to a hardcoded
# default, which is how the previous one ended up in git.
SECRET_KEY = env("DJANGO_SECRET_KEY")

DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
ADMIN_URL = env("DJANGO_ADMIN_URL")

# --- Applications -----------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "django_filters",
    "drf_spectacular",
]

# One app per bounded context (AUDIT 7.1).  The apps that do not exist yet -
# accounts, encounters, abdm - are deliberately absent rather than scaffolded
# empty: they belong to Phases 4-6 and an empty app is a liability, not a
# head start.
LOCAL_APPS = [
    "apps.patients",
    "apps.consent",
    "apps.triage",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Attaches a request id to every log line emitted while handling a request,
    # so a report of "it broke at 14:32" can be traced (AUDIT 2.13).
    "common.middleware.request_id.RequestIDMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database ---------------------------------------------------------------
# Postgres is the target (AUDIT 11.1): multi-tenancy, concurrent writes and
# JSONB for FHIR all need it.  DATABASE_URL keeps that a config change rather
# than a code change, and lets tests run on SQLite without a server.
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    )
}
# Reuse connections rather than opening one per request.  Harmless on SQLite,
# significant on Postgres.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DJANGO_CONN_MAX_AGE", default=60)

# --- Passwords --------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalisation ---------------------------------------------------
# Multilingual is the documented pain point and a retrofit is painful
# (AUDIT 7.3), so i18n stays on from the start.
LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# --- Static files -----------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Sessions ---------------------------------------------------------------
# The intake flow keeps only record ids in the session, never patient data.
SESSION_COOKIE_AGE = 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# --- Security ---------------------------------------------------------------
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# --- Django REST Framework --------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_PAGINATION_CLASS": "utils.pagination.DefaultPagination",
    "PAGE_SIZE": 25,
    # Anonymous by default because the patient-facing intake has no login yet.
    # Real authentication and per-clinic authorisation arrive with the accounts
    # app in Phase 4; until then every endpoint is explicit about who may call
    # it, and nothing reads another person's record.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    # Throttling matters here: an unthrottled triage endpoint is a free
    # inference service, and an unthrottled intake endpoint is a way to fill
    # the patient table with junk.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env.str("DJANGO_THROTTLE_ANON", default="60/hour"),
        "user": env.str("DJANGO_THROTTLE_USER", default="600/hour"),
        "triage": env.str("DJANGO_THROTTLE_TRIAGE", default="30/hour"),
    },
    "EXCEPTION_HANDLER": "utils.exceptions.handlers.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "HealthGuard API",
    "DESCRIPTION": (
        "Pre-consultation triage and intake. The triage endpoint returns ranked "
        "possibilities with an urgency assessment - never a diagnosis, and never "
        "a medicine recommendation."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# --- Triage -----------------------------------------------------------------
TRIAGE_ABSTAIN_THRESHOLD = env("TRIAGE_ABSTAIN_THRESHOLD")
TRIAGE_DIFFERENTIAL_SIZE = 3

# --- Logging ----------------------------------------------------------------
# Structured logging (AUDIT Phase 2).  DJANGO_LOG_FORMAT=json emits one JSON
# object per line for a log aggregator; the console formatter stays readable
# for local work.  Both carry the request id from the middleware.
LOG_LEVEL = env("DJANGO_LOG_LEVEL")
LOG_FORMAT = env("DJANGO_LOG_FORMAT")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "utils.logging.filters.RequestIDFilter"},
    },
    "formatters": {
        "console": {
            "format": "{levelname} {asctime} [{request_id}] {name} {message}",
            "style": "{",
        },
        "json": {
            "()": "utils.logging.formatters.JSONFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": LOG_FORMAT,
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django.db.backends": {
            "handlers": ["console"],
            # SQL logging is opt-in; it is far too noisy by default and, on a
            # health app, it puts patient data in the log stream.
            "level": env.str("DJANGO_SQL_LOG_LEVEL", default="WARNING"),
            "propagate": False,
        },
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
