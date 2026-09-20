"""Logging filter that stamps every record with the current request id."""

import logging

from utils.logging.context import get_request_id


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Always set the attribute: a formatter referencing a missing field
        # raises while trying to report an error, which is the worst possible
        # time to raise.
        record.request_id = getattr(record, "request_id", None) or get_request_id()
        return True
