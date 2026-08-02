"""Canonical Stage-C mutation apply exports.

The implementation currently lives in the compatibility module while the
large historical pipeline is removed. All production callers import here.
"""
from app.pipeline_lib.db_apply import (  # noqa: F401
    TocTouDuplicateError,
    _execute_plan,
    _get_or_create_genre,
    _upsert_source,
    apply_mutation_plan,
)

__all__ = [
    "TocTouDuplicateError",
    "apply_mutation_plan",
    "_execute_plan",
    "_get_or_create_genre",
    "_upsert_source",
]
