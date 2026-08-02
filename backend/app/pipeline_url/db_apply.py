"""Compatibility exports for canonical Stage-C apply."""
from app.pipeline.db_apply import TocTouDuplicateError, _execute_plan, apply_mutation_plan

__all__ = ["TocTouDuplicateError", "_execute_plan", "apply_mutation_plan"]
