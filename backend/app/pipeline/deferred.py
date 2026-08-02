"""Canonical deferred-stage exports."""
from app.pipeline_url.deferred import (  # noqa: F401
    active_coordinator_count,
    dispatch_deferred,
)

__all__ = ["active_coordinator_count", "dispatch_deferred"]
