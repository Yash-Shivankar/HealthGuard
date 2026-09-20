"""DRF exception handler.

Turns domain exceptions into consistent JSON, and makes sure an unexpected
exception is logged with a stack trace rather than vanishing (AUDIT 2.13).
"""

import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from utils.exceptions.domain import DomainError

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    if isinstance(exc, DomainError):
        logger.info(
            "Domain error in %s: %s",
            context.get("view").__class__.__name__ if context.get("view") else "?",
            exc.message,
            extra={"error_code": exc.code},
        )
        return Response(
            {"error": {"code": exc.code, "message": exc.message}},
            status=exc.status_code,
        )

    response = drf_exception_handler(exc, context)

    if response is None:
        # Nothing DRF recognises: it will become a 500.  Log it here with the
        # stack, because after this point the traceback is gone.
        logger.exception("Unhandled exception in API view", exc_info=exc)
        return None

    # Normalise DRF's own errors into the same envelope so a client has one
    # shape to parse.
    if isinstance(response.data, dict) and "error" not in response.data:
        response.data = {
            "error": {
                "code": getattr(exc, "default_code", "error"),
                "message": "Request could not be processed.",
                "detail": response.data,
            }
        }
    return response
