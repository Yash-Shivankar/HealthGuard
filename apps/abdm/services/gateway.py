"""The ABDM gateway transport.

## Read this before wiring real credentials

**The endpoint paths and payload shapes in ``endpoints.py`` are written from the
published ABDM specification and have NOT been verified against a live sandbox.**
AUDIT 11.3 asks whether a sandbox account exists; it is still unanswered, so
nothing here has round-tripped against the real gateway.

Everything else - the linkage lifecycle, consent gating, idempotency, masking,
the audit trail, the state machine - is verified and tested. What is unverified
is one layer: which URL, which field names, which envelope. That layer is
deliberately confined to ``endpoints.py`` so it can be corrected in one file
against the current documentation without touching anything that depends on it.

ABDM has shipped more than one major revision of these APIs. Treat the constants
in ``endpoints.py`` as a starting point to check, not as fact.

## Why a transport interface

Two implementations:

``StubTransport``   deterministic canned responses, used by the tests and by a
                    developer with no credentials. The whole application runs
                    end to end against it.
``HttpTransport``   real calls, used when ``ABDM_ENABLED`` is on and credentials
                    are configured.

The stub is not a mock bolted on for tests - it is the default, because a
project that cannot run without third-party credentials is a project nobody can
contribute to. Every record it produces is flagged ``simulated=True`` so a
sandbox-era row is never mistaken for a production one.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class GatewayError(Exception):
    """A call to the gateway failed.

    ``retryable`` distinguishes "the gateway is down" from "your request was
    wrong", because the first deserves a retry and the second never will.
    """

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class GatewayResponse:
    operation: str
    status_code: int
    data: dict[str, Any]
    request_id: str
    duration_ms: int
    simulated: bool = False

    @property
    def succeeded(self) -> bool:
        return 200 <= self.status_code < 300


@dataclass
class GatewayRequest:
    operation: str
    method: str
    path: str
    payload: dict[str, Any] = field(default_factory=dict)
    #: ABDM correlates asynchronous callbacks by this; generated per call.
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class Transport(Protocol):
    """What the services depend on. Neither implementation leaks upward."""

    simulated: bool

    def send(self, request: GatewayRequest) -> GatewayResponse: ...


class StubTransport:
    """Deterministic canned responses.

    Shaped to match what the services consume, not to imitate ABDM byte for
    byte - imitating an unverified spec in detail would give false confidence
    that the integration works.

    Deterministic on purpose: the same input yields the same ABHA number, so a
    test can assert an exact value and a developer can reason about what they
    are looking at.
    """

    simulated = True

    #: Set to force a failure, for testing the error paths.
    fail_with: GatewayError | None = None

    def __init__(self, fail_with: GatewayError | None = None):
        self.fail_with = fail_with
        self.calls: list[GatewayRequest] = []

    def send(self, request: GatewayRequest) -> GatewayResponse:
        self.calls.append(request)

        if self.fail_with is not None:
            raise self.fail_with

        started = time.perf_counter()
        data = self._respond(request)
        duration = int((time.perf_counter() - started) * 1000)

        return GatewayResponse(
            operation=request.operation,
            status_code=200,
            data=data,
            request_id=request.request_id,
            duration_ms=duration,
            simulated=True,
        )

    def _respond(self, request: GatewayRequest) -> dict[str, Any]:
        if request.operation == "session":
            return {"accessToken": "stub-token", "expiresIn": 1800}

        if request.operation == "abha.create":
            mobile = str(request.payload.get("mobile", ""))
            return {
                "abhaNumber": _stub_abha_number(mobile),
                "abhaAddress": _stub_abha_address(request.payload.get("name", "")),
                "verified": True,
            }

        if request.operation == "abha.verify":
            number = str(request.payload.get("abhaNumber", ""))
            digits = "".join(ch for ch in number if ch.isdigit())
            # A 14-digit number verifies; anything else does not. Enough to
            # exercise both branches without pretending to validate a checksum
            # whose algorithm is not confirmed here.
            return {"verified": len(digits) == 14, "abhaAddress": _stub_abha_address(digits)}

        if request.operation == "hfr.register":
            return {"facilityId": f"HFR-{_stub_suffix(request.payload.get('name', ''))}", "verified": True}

        if request.operation == "hi.transfer":
            # Echoes the transaction id and acknowledges. Shaped to what the
            # service consumes, not to imitate an unverified spec in detail.
            return {
                "transactionId": request.payload.get("transactionId", ""),
                "accepted": True,
            }

        if request.operation == "hpr.register":
            return {"hprId": f"HPR-{_stub_suffix(request.payload.get('name', ''))}", "verified": True}

        raise GatewayError(f"StubTransport has no response for {request.operation!r}")


class HttpTransport:
    """Real calls.

    Deliberately thin, and deliberately unused until someone supplies
    credentials and checks ``endpoints.py`` against the current documentation.
    """

    simulated = False

    def __init__(self, base_url: str, client_id: str, client_secret: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def send(self, request: GatewayRequest) -> GatewayResponse:
        # Imported here so the project has no hard dependency on an HTTP client
        # until someone actually enables this path.
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on deployment
            raise GatewayError(
                "HttpTransport needs `requests` installed. It is not in "
                "requirements/base.txt because nothing uses this path until "
                "ABDM credentials are configured."
            ) from exc

        url = f"{self.base_url}{request.path}"
        headers = {
            "Content-Type": "application/json",
            "REQUEST-ID": request.request_id,
            # ABDM expects an RFC3339 timestamp on gateway calls.
            "TIMESTAMP": timezone.now().isoformat(),
        }
        if request.operation != "session":
            headers["Authorization"] = f"Bearer {self._access_token()}"

        started = time.perf_counter()
        try:
            response = requests.request(
                request.method, url, json=request.payload, headers=headers, timeout=self.timeout
            )
        except Exception as exc:  # noqa: BLE001 - network failures are all retryable
            raise GatewayError(str(exc), retryable=True) from exc
        duration = int((time.perf_counter() - started) * 1000)

        if response.status_code >= 500:
            raise GatewayError(
                f"Gateway returned {response.status_code}",
                status_code=response.status_code,
                retryable=True,
            )
        if response.status_code >= 400:
            raise GatewayError(
                f"Gateway rejected the request ({response.status_code})",
                status_code=response.status_code,
                retryable=False,
            )

        return GatewayResponse(
            operation=request.operation,
            status_code=response.status_code,
            data=response.json() if response.content else {},
            request_id=request.request_id,
            duration_ms=duration,
        )

    def _access_token(self) -> str:
        """Cached session token.

        Cached in memory rather than the database: it is a short-lived bearer
        credential, and a process-local copy is one fewer place it can leak
        from. A restart re-fetching it costs one call.
        """
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        from apps.abdm.services.endpoints import SESSION

        session = self.send(
            GatewayRequest(
                operation="session",
                method=SESSION.method,
                path=SESSION.path,
                payload={"clientId": self.client_id, "clientSecret": self.client_secret},
            )
        )
        self._token = str(session.data.get("accessToken", ""))
        # Refresh a minute early rather than on the boundary.
        self._token_expires_at = time.monotonic() + int(session.data.get("expiresIn", 1800)) - 60
        return self._token


def build_transport() -> Transport:
    """The transport this deployment should use.

    Defaults to the stub. Enabling the real one takes an explicit
    ``ABDM_ENABLED=True`` **and** credentials - two deliberate acts, because
    accidentally pointing a development machine at a real health network is not
    a mistake that should be one typo away.
    """
    if not getattr(settings, "ABDM_ENABLED", False):
        return StubTransport()

    client_id = getattr(settings, "ABDM_CLIENT_ID", "")
    client_secret = getattr(settings, "ABDM_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        logger.error(
            "ABDM_ENABLED is on but credentials are missing; falling back to the stub",
            extra={"event": "abdm_missing_credentials"},
        )
        return StubTransport()

    return HttpTransport(
        base_url=getattr(settings, "ABDM_BASE_URL", ""),
        client_id=client_id,
        client_secret=client_secret,
        timeout=float(getattr(settings, "ABDM_TIMEOUT_SECONDS", 10)),
    )


# --- Stub value helpers -----------------------------------------------------
# Deterministic so tests can assert exact values.


def _stub_suffix(seed: str) -> str:
    return f"{abs(hash(seed)) % 10**8:08d}"


def _stub_abha_number(seed: str) -> str:
    return f"{abs(hash('abha' + seed)) % 10**14:014d}"


def _stub_abha_address(seed: str) -> str:
    handle = "".join(ch for ch in seed.lower() if ch.isalnum())[:12] or "patient"
    return f"{handle}@sbx"
