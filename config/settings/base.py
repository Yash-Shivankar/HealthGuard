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

from datetime import timedelta
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
    DJANGO_CORS_ALLOWED_ORIGINS=(list, []),
    ADMIN_IP_ALLOWLIST=(list, []),
    ADMIN_SESSION_TIMEOUT_SECONDS=(int, 900),
    AXES_FAILURE_LIMIT=(int, 5),
    AXES_COOLOFF_MINUTES=(int, 30),
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

# --- Admin hardening (AUDIT 8.10) -------------------------------------------
# Empty means no network restriction. AUDIT 11.7 (will ops be on a fixed IP?)
# is unanswered, so this cannot be enabled by default without risking locking
# the owner out of their own admin.
ADMIN_IP_ALLOWLIST = env("ADMIN_IP_ALLOWLIST")
# An admin session is a skeleton key to every patient record; 15 minutes of
# inactivity is deliberately shorter than an ordinary session.
ADMIN_SESSION_TIMEOUT_SECONDS = env("ADMIN_SESSION_TIMEOUT_SECONDS")

# --- Applications -----------------------------------------------------------
# Unfold must come *before* django.contrib.admin so its templates win
# (AUDIT 8.11, step 1). unfold.contrib.* add filter and form widgets.
#
# BasicAppConfig, not plain "unfold": Unfold's DefaultAppConfig.ready()
# unconditionally assigns `admin.site = UnfoldAdminSite()`, which silently
# clobbers the custom site set through AdminConfig.default_site - taking the
# HealthGuard branding and, more seriously, the 2FA gate in
# HealthGuardAdminSite.has_permission with it. BasicAppConfig loads the
# templates and widgets without replacing the site.
UNFOLD_APPS = [
    "unfold.apps.BasicAppConfig",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.simple_history",
]

DJANGO_APPS = [
    # config.apps.HealthGuardAdminConfig rather than django.contrib.admin, so
    # admin.site is the HealthGuard site and @admin.register targets it.
    "config.apps.HealthGuardAdminConfig",
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
    # The SPA runs on its own origin in development (Vite on 5173), so the API
    # has to opt in to those requests. In production the SPA is built and served
    # from the same origin, and CORS_ALLOWED_ORIGINS is empty.
    "corsheaders",
    # Full per-change snapshots on clinical models (AUDIT 8.9). The storage
    # cost against django-auditlog's leaner diffs is the trade-off; for
    # compliance work, showing an auditor the complete record as it stood on a
    # given date is worth the disk.
    "simple_history",
    # Login rate limiting and lockout (AUDIT 8.10).
    "axes",
    # 2FA for staff accounts (AUDIT 8.10).
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
]

# One app per bounded context (AUDIT 7.1).  The apps that do not exist yet -
# accounts, encounters, abdm - are deliberately absent rather than scaffolded
# empty: they belong to Phases 4-6 and an empty app is a liability, not a
# head start.
LOCAL_APPS = [
    # Tenancy first: patients and triage hold foreign keys into it.
    "apps.accounts",
    "apps.patients",
    "apps.consent",
    "apps.triage",
    # Read-access log. Django's LogEntry records changes only, and reading a
    # patient record is the event that matters under DPDP (AUDIT 8.9).
    "apps.audit",
    # DPDP obligations that have deadlines: retention notices, data-subject
    # requests, breach incidents (AUDIT 4.1, 8.5).
    "apps.compliance",
    # ABDM: ABHA linkage, HFR/HPR registration, Scan-&-Share (AUDIT 4.2 M1).
    "apps.abdm",
    # AI Tier 1: voice intake, symptom extraction, ambient summarisation - all
    # of it producing drafts that need a clinician (AUDIT 6, Tier 1).
    "apps.ai",
]

INSTALLED_APPS = UNFOLD_APPS + DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Must precede CommonMiddleware so a preflight gets its headers even when
    # the request would otherwise be redirected.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Must follow AuthenticationMiddleware: it upgrades request.user with the
    # verified-device state that admin 2FA checks (AUDIT 8.10).
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Resolves which clinic a request may see, and sets user.clinic_id for the
    # admin scoping in common/admin (AUDIT 8.7). Must follow
    # AuthenticationMiddleware.
    "common.middleware.tenancy.TenancyMiddleware",
    # Optional network restriction in front of the admin. Off unless
    # ADMIN_IP_ALLOWLIST is set - AUDIT 11.7 (will ops be on a fixed IP?) is
    # still unanswered, so this cannot be switched on by default.
    "common.middleware.admin_ip_allowlist.AdminIPAllowlistMiddleware",
    # Session age limit for staff sessions, shorter than for patients.
    "common.middleware.admin_session.AdminSessionTimeoutMiddleware",
    # Attaches a request id to every log line emitted while handling a request,
    # so a report of "it broke at 14:32" can be traced (AUDIT 2.13).
    "common.middleware.request_id.RequestIDMiddleware",
    # Must be last: it wraps authentication to count and block failures.
    "axes.middleware.AxesMiddleware",
]

# AxesStandaloneBackend must come first so a locked-out account is refused
# before any credential check runs.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
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
        # Its own scope. An unthrottled API login undoes the django-axes
        # lockout, because axes counts attempts per view and a second door is a
        # second budget.
        "login": env.str("DJANGO_THROTTLE_LOGIN", default="20/hour"),
        # Tighter than triage. Extraction is more expensive than a forest
        # predict and, once a vendor is configured, it costs money per call -
        # so an unthrottled endpoint is someone else's bill.
        "ai_intake": env.str("DJANGO_THROTTLE_AI_INTAKE", default="20/hour"),
        # Tighter still: an audio upload is two orders of magnitude more bytes
        # and, with a vendor, the most expensive call in the system.
        "ai_voice": env.str("DJANGO_THROTTLE_AI_VOICE", default="10/hour"),
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


# --- Login lockout: django-axes (AUDIT 8.10) --------------------------------
AXES_FAILURE_LIMIT = env("AXES_FAILURE_LIMIT")
AXES_COOLOFF_TIME = timedelta(minutes=env("AXES_COOLOFF_MINUTES"))
# Lock on the username *and* the IP together. Locking on IP alone punishes
# everyone behind a clinic's shared connection when one person mistypes;
# locking on username alone lets an attacker lock a known account out
# deliberately.
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ACCESS_FAILURE_LOG = True
# Never write the attempted password, not even hashed, into the attempt log.
AXES_SENSITIVE_PARAMETERS = ["password", "otp_token"]
AXES_VERBOSE = True

# --- 2FA: django-otp (AUDIT 8.10) -------------------------------------------
# "Mandatory 2FA for every staff account. No exceptions for 'just the ops
# account.'" Enforcement lives in HealthGuardAdminSite.has_permission, so it
# covers every admin view rather than relying on each one to remember.
OTP_TOTP_ISSUER = "HealthGuard"
# Off in development only, so a fresh clone can reach the admin before anyone
# has enrolled a device. Production must leave this True.
ADMIN_REQUIRE_2FA = env.bool("ADMIN_REQUIRE_2FA", default=True)

# --- Simple history (AUDIT 8.9) ---------------------------------------------
# Record which user made each change. Without this the history table shows
# what changed but not who changed it, which is half an audit trail.
SIMPLE_HISTORY_HISTORY_CHANGE_REASON_USE_TEXT_FIELD = True

# --- Unfold admin theme (AUDIT 8.2-8.4) -------------------------------------
from config.settings.unfold import UNFOLD  # noqa: E402,F401


# --- CORS (the SPA in development) ------------------------------------------
# Empty in production: the built SPA is served from the same origin as the API,
# so no cross-origin allowance is needed and granting one would be a gift.
CORS_ALLOWED_ORIGINS = env("DJANGO_CORS_ALLOWED_ORIGINS")
# Required for session auth: without it the browser sends no session cookie and
# every request looks anonymous.
CORS_ALLOW_CREDENTIALS = True
# Django needs the SPA origin trusted for unsafe methods as well, or the CSRF
# check rejects the request even with a valid token.
CSRF_TRUSTED_ORIGINS = list(
    dict.fromkeys(env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[]) + CORS_ALLOWED_ORIGINS)
)


# --- ABDM (AUDIT 4.2) --------------------------------------------------------
# Off by default, and it takes two deliberate acts to turn on: ABDM_ENABLED
# **and** credentials. Accidentally pointing a development machine at a real
# health network should not be one typo away.
#
# While this is off, apps/abdm/services/gateway.py uses StubTransport and every
# record it produces is flagged simulated=True, so a sandbox-era row is never
# mistaken for a production one.
#
# BEFORE ENABLING: the endpoint paths and payload shapes in
# apps/abdm/services/endpoints.py are written from the published specification
# and have NOT been verified against a live sandbox (AUDIT 11.3 is unanswered).
# Check every one against the current NHA documentation first.
ABDM_ENABLED = env.bool("ABDM_ENABLED", default=False)
ABDM_BASE_URL = env.str("ABDM_BASE_URL", default="")
ABDM_CLIENT_ID = env.str("ABDM_CLIENT_ID", default="")
ABDM_CLIENT_SECRET = env.str("ABDM_CLIENT_SECRET", default="")
ABDM_TIMEOUT_SECONDS = env.float("ABDM_TIMEOUT_SECONDS", default=10.0)


# --- AI Tier 1 (AUDIT 6) -----------------------------------------------------
# AUDIT 6 ends with a guardrail that is not negotiable:
#
#   No patient-identifiable data to third-party LLM APIs without a consent
#   basis and a processor agreement. Plan for on-prem or India-region inference.
#
# Four conditions must ALL hold before a character leaves this machine, and
# three of them are here (the fourth is the patient's own consent, checked per
# call against ConsentPurpose.AI_ASSISTANCE):
#
#   1. AI_ALLOW_EXTERNAL_PROCESSING is on
#   2. the provider has both a URL and a key
#   3. AI_PROCESSOR_AGREEMENT_REF names a signed agreement - an empty string
#      refuses, which is what stops "we have a DPA" being a claim in a README
#
# With any of them missing the app uses its local providers and keeps working:
# the lexicon extractor and the template summariser are real implementations,
# not stubs, so an unconfigured deployment loses quality and not function. The
# transcriber is the exception - speech recognition cannot be done locally here,
# so its stub returns canned text and every draft built from it is marked
# simulated, which the review gate refuses to let anyone sign off.
#
# BEFORE ENABLING: no vendor is contracted (AUDIT 11.5 is unanswered), so the
# request shapes in apps/ai/services/providers.py are placeholders. Check them
# against the chosen provider's documentation first - grep for
# "AI-VENDOR-SPECIFIC: verify".
AI_ALLOW_EXTERNAL_PROCESSING = env.bool("AI_ALLOW_EXTERNAL_PROCESSING", default=False)
AI_PROCESSOR_AGREEMENT_REF = env.str("AI_PROCESSOR_AGREEMENT_REF", default="")

# Speech to text.
AI_ASR_BASE_URL = env.str("AI_ASR_BASE_URL", default="")
AI_ASR_API_KEY = env.str("AI_ASR_API_KEY", default="")

# LLM symptom normalisation. Prefer on-prem or India-region inference.
AI_LLM_BASE_URL = env.str("AI_LLM_BASE_URL", default="")
AI_LLM_API_KEY = env.str("AI_LLM_API_KEY", default="")
AI_LLM_MODEL = env.str("AI_LLM_MODEL", default="")

AI_TIMEOUT_SECONDS = env.float("AI_TIMEOUT_SECONDS", default=20.0)
