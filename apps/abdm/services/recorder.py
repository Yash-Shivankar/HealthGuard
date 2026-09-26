"""Records every outbound gateway call.

One wrapper, so no service can make an unrecorded call by forgetting to. The
audit only names audit trails under M3, but this exists from M1: adding it later
would leave a gap over exactly the period when the integration is least stable,
and NHA's production exit audit asks what was called and when.

**No bodies are stored.** Requests and responses carry ABHA numbers, OTPs and,
from M2, clinical payloads. Keeping them would build a second copy of every
record outside the erasure path - the same mistake the access log deliberately
avoids. What is kept is the shape of the call: operation, status, timing,
correlation id, and whether it went to a real gateway or the stub.

Recording is best-effort, like the access log, and for the same reason: a
patient must not be denied a linkage because a log table was full. The honest
statement of the trade-off is that this record is authoritative for what it
contains and not guaranteed to be complete.
"""

from __future__ import annotations

import logging
import time

from apps.abdm.repositories.abdm_repository import GatewayCallRepository
from apps.abdm.services.gateway import GatewayError, GatewayRequest, GatewayResponse, Transport
from utils.logging.context import get_request_id

logger = logging.getLogger(__name__)


class GatewayRecorder:
    def __init__(self, repository: GatewayCallRepository | None = None):
        self.repository = repository or GatewayCallRepository()

    def send(
        self, transport: Transport, request: GatewayRequest, *, clinic=None
    ) -> GatewayResponse:
        started = time.perf_counter()

        try:
            response = transport.send(request)
        except GatewayError as exc:
            # Recorded *before* re-raising, so a timeout still leaves a trace.
            self._record(
                request,
                clinic=clinic,
                status_code=exc.status_code,
                succeeded=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_message=exc.message,
                simulated=getattr(transport, "simulated", False),
            )
            logger.warning(
                "ABDM gateway call failed",
                extra={
                    "event": "abdm_call_failed",
                    "operation": request.operation,
                    "retryable": exc.retryable,
                    "status": exc.status_code,
                },
            )
            raise

        self._record(
            request,
            clinic=clinic,
            status_code=response.status_code,
            succeeded=response.succeeded,
            duration_ms=response.duration_ms,
            error_message="",
            simulated=response.simulated,
        )
        logger.info(
            "ABDM gateway call",
            extra={
                "event": "abdm_call",
                "operation": request.operation,
                "status": response.status_code,
                "duration_ms": response.duration_ms,
                "simulated": response.simulated,
            },
        )
        return response

    def _record(self, request: GatewayRequest, **fields) -> None:
        try:
            self.repository.create(
                operation=request.operation,
                request_id=request.request_id,
                correlation_id=get_request_id()[:64],
                **fields,
            )
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.exception(
                "Could not record an ABDM gateway call",
                extra={"operation": request.operation},
            )
