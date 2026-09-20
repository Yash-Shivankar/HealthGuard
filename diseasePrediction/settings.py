"""
Django settings for the HealthGuard project.

All secrets and environment-specific values are read from the environment
(see .env.example).  Nothing in this file may contain a credential.

Audit references:
  §2.2  secrets to env, DEBUG off by default, explicit ALLOWED_HOSTS, HTTPS
        and cookie hardening
  §2.5  every filesystem path is derived from BASE_DIR, never from the CWD
  §2.13 real logging instead of print() and bare except
  §8.10 the admin is not served from /admin/
"""

from pathlib import Path

import environ

# --- Paths ------------------------------------------------------------------
# BASE_DIR is the repository root.  Every path below is built from it so the
# app behaves identically under runserver, gunicorn, Docker and management
# commands, regardless of the process working directory (§2.5).
BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
ML_ARTIFACT_DIR = BASE_DIR / "ml" / "artifacts"

# --- Environment ------------------------------------------------------------
env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    DJANGO_SECURE_SSL_REDIRECT=(bool, True),
    DJANGO_SECURE_HSTS_SECONDS=(int, 31536000),
    DJANGO_ADMIN_URL=(str, "internal-ops/"),
    TRIAGE_ABSTAIN_THRESHOLD=(float, 0.40),
)
environ.Env.read_env(BASE_DIR / ".env")

# Fails loudly if unset — a missing key must not silently fall back to a
# hardcoded default, which is how the previous key ended up in git.
SECRET_KEY = env("DJANGO_SECRET_KEY")

DEBUG = env("DJANGO_DEBUG")

ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# Path the Django admin is mounted at, e.g. "internal-ops/" (§8.10).
ADMIN_URL = env("DJANGO_ADMIN_URL")

# --- Applications -----------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # prediction_app is a real Django app as of the audit fixes.  It was
    # previously a bare folder, which is why UserInfo was never migrated and
    # patient data was written to a flat CSV instead (§1, §2.1).
    "prediction_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "diseasePrediction.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "diseasePrediction.wsgi.application"

# --- Database ---------------------------------------------------------------
# Postgres is the Phase 2 target (§11.1); DATABASE_URL makes that a config
# change rather than a code change.
DATABASES = {"default": env.db_url("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}

# --- Passwords --------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalisation ---------------------------------------------------
LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# --- Static files -----------------------------------------------------------
# prediction_app/static/ is picked up by AppDirectoriesFinder now that
# prediction_app is a real app, so no explicit STATICFILES_DIRS entry is
# needed (listing it twice raises a duplicate-path warning).
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Sessions ---------------------------------------------------------------
# The intake flow keeps only a record id in the session, never patient data.
SESSION_COOKIE_AGE = 60 * 60  # 1 hour; short by design for a health surface
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# --- Security ---------------------------------------------------------------
# Hardening is applied whenever DEBUG is off, so a production deploy is secure
# by default rather than by remembering to flip switches (§2.2, §8.10).
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

if not DEBUG:
    SECURE_SSL_REDIRECT = env("DJANGO_SECURE_SSL_REDIRECT")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env("DJANGO_SECURE_HSTS_SECONDS")
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    # Set this when running behind a TLS-terminating proxy (nginx, ALB, Render):
    # SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Triage -----------------------------------------------------------------
# Below this confidence the result abstains rather than naming a condition
# (§6 Tier 0.7).  Over-triage is the correct safety posture (§3).
TRIAGE_ABSTAIN_THRESHOLD = env("TRIAGE_ABSTAIN_THRESHOLD")

# Number of ranked possibilities shown to the user (§6 Tier 0.3).
TRIAGE_DIFFERENTIAL_SIZE = 3

# --- Logging ----------------------------------------------------------------
# Replaces print() and `except: pass` (§2.13).  Anything that fails during
# content loading or inference is recorded with a stack trace.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "prediction_app": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}
