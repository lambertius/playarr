"""
⚠️  LEGACY PIPELINE — Do not modify without a refactor plan.

This is the original staged import pipeline. It has been superseded by:
  - pipeline_url/  (URL-based imports — YouTube, Vimeo)
  - pipeline_lib/  (library file imports)

Kept for reference. Active import tasks use pipeline_url and pipeline_lib.
See docs/KNOWN_ISSUES.md for consolidation plans.

Original architecture:
  Stage A — Minimal DB registration (ProcessingJob exists, status set)
  Stage B — Parallel workspace build (all heavy I/O, no locks, no DB writes)
  Stage C — Serial apply (short DB transaction under _apply_lock)
  Stage D — Deferred enrichment tasks (preview, scene analysis, Kodi export)
"""
from app.pipeline.workspace import ImportWorkspace
from app.pipeline.stages import run_library_import_pipeline, run_url_import_pipeline

__all__ = [
    "ImportWorkspace",
    "run_library_import_pipeline",
    "run_url_import_pipeline",
]
