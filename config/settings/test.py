"""Test settings.

Deliberately not a thin wrapper over dev: tests should run without a Postgres
server, without network, and fast.
"""

from config.settings.base import *  # noqa: F403
from config.settings.base import env

DEBUG = False
ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]

# In-memory SQLite by default, so the suite runs fast and needs no server,
# and so a developer with DATABASE_URL pointing at a real database does not
# accidentally run tests against it.
#
# TEST_DATABASE_URL opts in to running the same suite on Postgres, which is
# what CI should do before a release: SQLite and Postgres disagree about
# enough - constraint timing, JSON operators, transaction behaviour - that
# passing on one is not evidence for the other.
#
#   TEST_DATABASE_URL=postgres://user:pass@127.0.0.1:5432/healthguard_test pytest
DATABASES = {
    "default": env.db_url(
        "TEST_DATABASE_URL", default="sqlite:///:memory:"
    )
}

# Hashing passwords properly is the single slowest thing in a Django test run
# and nothing here tests the hasher.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Throttling makes tests order-dependent and flaky, so every scope is
# unlimited. The scopes must still be *present*: a view that sets
# `throttle_scope` as a class attribute resolves it regardless of the default
# throttle classes, and a missing key raises ImproperlyConfigured. A rate of
# None is what DRF reads as "no limit".
REST_FRAMEWORK = {  # noqa: F405
    **globals()["REST_FRAMEWORK"],
    "DEFAULT_THROTTLE_CLASSES": [],
    # Every scope a view declares must appear here, including new ones - see
    # the comment above. Missing keys surface as ImproperlyConfigured at request
    # time, not at startup, so they look like a broken endpoint.
    "DEFAULT_THROTTLE_RATES": {
        "anon": None,
        "user": None,
        "triage": None,
        "login": None,
    },
}

LOGGING["root"]["level"] = "WARNING"  # noqa: F405
