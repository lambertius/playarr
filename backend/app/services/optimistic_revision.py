"""ARCH-002 optimistic concurrency helpers for mutable aggregates."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException


def require_expected_revision(
    aggregate: Any,
    expected_revision: int,
    *,
    current_state: Callable[[], Any],
) -> None:
    """Reject a stale mutation with the shared structured 409 envelope."""
    current_revision = int(getattr(aggregate, "revision", 0) or 0)
    if expected_revision == current_revision:
        return
    current = current_state()
    if hasattr(current, "model_dump"):
        current = current.model_dump(mode="json")
    raise HTTPException(status_code=409, detail={
        "code": "stale_revision",
        "message": "This item changed in another browser. Reload before saving.",
        "operation_id": None,
        "retryable": True,
        "field_errors": {
            "expected_revision": f"current revision is {current_revision}",
        },
        "diagnostics_id": None,
        "current_revision": current_revision,
        "current": current,
    })
