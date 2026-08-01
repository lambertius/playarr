"""Request/operation correlation context shared by HTTP and durable jobs."""
from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4


_request_id: ContextVar[str | None] = ContextVar("playarr_request_id", default=None)


def new_request_id() -> str:
    return f"req_{uuid4().hex}"


def set_request_id(value: str):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()


def new_operation_id() -> str:
    return f"op_{uuid4().hex}"
