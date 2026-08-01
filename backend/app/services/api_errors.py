"""One structured error contract for every HTTP endpoint."""
from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


STATUS_CODES = {
    400: "invalid_request", 401: "unauthorized", 403: "forbidden",
    404: "not_found", 409: "conflict", 410: "gone", 422: "validation_error",
    423: "file_locked", 429: "backpressure", 502: "provider_failure",
    503: "service_unavailable",
}
logger = logging.getLogger(__name__)


def error_envelope(
    request: Request,
    *,
    code: str,
    message: str,
    operation_id: str | None = None,
    retryable: bool = False,
    field_errors: dict[str, list[str]] | None = None,
    diagnostics_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_id = getattr(request.state, "request_id", None)
    return {
        "code": code,
        "message": message,
        "operation_id": operation_id,
        "retryable": retryable,
        "field_errors": field_errors or {},
        "diagnostics_id": diagnostics_id,
        "request_id": request_id,
        **(extra or {}),
    }


def _normalise_detail(request: Request, status_code: int, detail: Any) -> dict:
    if isinstance(detail, dict):
        known = {
            key: detail.get(key) for key in (
                "code", "message", "operation_id", "retryable",
                "field_errors", "diagnostics_id",
            )
        }
        message = known["message"] or detail.get("detail") or "Request failed"
        extras = {key: value for key, value in detail.items() if key not in known}
        return error_envelope(
            request,
            code=known["code"] or STATUS_CODES.get(status_code, "request_failed"),
            message=str(message),
            operation_id=known["operation_id"],
            retryable=bool(known["retryable"]),
            field_errors=known["field_errors"] or {},
            diagnostics_id=known["diagnostics_id"],
            extra=extras,
        )
    return error_envelope(
        request,
        code=STATUS_CODES.get(status_code, "request_failed"),
        message=str(detail),
        retryable=status_code in {423, 429, 502, 503},
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        _normalise_detail(request, exc.status_code, exc.detail),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError):
    fields: dict[str, list[str]] = {}
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        fields.setdefault(location or "request", []).append(error.get("msg", "Invalid value"))
    return JSONResponse(
        error_envelope(
            request, code="validation_error", message="Request validation failed",
            field_errors=fields,
        ),
        status_code=422,
    )


async def stale_revision_handler(request: Request, exc: Exception):
    return JSONResponse(
        error_envelope(
            request, code="stale_revision", message=str(exc), retryable=False,
            extra={
                "current_revision": getattr(exc, "current", None),
                "expected_revision": getattr(exc, "expected", None),
            },
        ),
        status_code=409,
    )


async def file_collision_handler(request: Request, exc: Exception):
    return JSONResponse(
        error_envelope(
            request, code="file_collision", message=str(exc), retryable=False,
            extra={"collisions": getattr(exc, "collisions", [])},
        ),
        status_code=409,
    )


async def file_locked_handler(request: Request, exc: Exception):
    return JSONResponse(
        error_envelope(
            request, code="file_locked", message=str(exc), retryable=True,
        ),
        status_code=423,
    )


async def provider_timeout_handler(request: Request, exc: Exception):
    return JSONResponse(
        error_envelope(
            request, code="provider_timeout", message=str(exc), retryable=True,
        ),
        status_code=503,
    )


async def unexpected_error_handler(request: Request, exc: Exception):
    diagnostics_id = getattr(request.state, "request_id", None) or str(uuid4())
    logger.exception("Unhandled API failure diagnostics_id=%s", diagnostics_id, exc_info=exc)
    return JSONResponse(
        error_envelope(
            request, code="internal_error", message="An unexpected error occurred",
            retryable=False, diagnostics_id=diagnostics_id,
        ),
        status_code=500,
    )


def install_structured_error_handlers(app) -> None:
    from app.services.file_operations import FilePlanCollision, FileWaitingForRelease
    from app.services.mutation_coordinator import StaleRevisionError

    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StaleRevisionError, stale_revision_handler)
    app.add_exception_handler(FilePlanCollision, file_collision_handler)
    app.add_exception_handler(FileWaitingForRelease, file_locked_handler)
    app.add_exception_handler(TimeoutError, provider_timeout_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
