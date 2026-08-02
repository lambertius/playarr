"""Compatibility exports for the canonical deferred dispatcher."""
from app.pipeline.deferred import active_coordinator_count, dispatch_deferred

__all__ = ["active_coordinator_count", "dispatch_deferred"]
