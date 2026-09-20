"""Per-request context shared with the logging layer.

A ContextVar rather than thread-local storage: it behaves correctly under ASGI
and anything async, where one thread serves many requests.
"""

from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(value: str) -> object:
    return _request_id.set(value)


def get_request_id() -> str:
    return _request_id.get()


def reset_request_id(token: object) -> None:
    _request_id.reset(token)
