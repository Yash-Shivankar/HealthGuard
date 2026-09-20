"""Attach a request id to every request and to every log line it produces."""

import logging
import time
import uuid

from utils.logging.context import reset_request_id, set_request_id

logger = logging.getLogger(__name__)

HEADER = "HTTP_X_REQUEST_ID"
RESPONSE_HEADER = "X-Request-ID"

# An upstream proxy may supply one so a trace spans services. Length-capped
# and sanitised, because it is attacker-controlled and ends up in log lines.
MAX_INBOUND_LENGTH = 64


class RequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = self._inbound(request) or uuid.uuid4().hex[:16]
        request.request_id = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()

        try:
            response = self.get_response(request)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            reset_request_id(token)

        response[RESPONSE_HEADER] = request_id
        logger.info(
            "%s %s -> %s",
            request.method,
            request.path,
            response.status_code,
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "request_id": request_id,
            },
        )
        return response

    @staticmethod
    def _inbound(request) -> str | None:
        value = request.META.get(HEADER, "")[:MAX_INBOUND_LENGTH].strip()
        # Alphanumerics, dash and underscore only: keeps log injection out.
        cleaned = "".join(c for c in value if c.isalnum() or c in "-_")
        return cleaned or None
