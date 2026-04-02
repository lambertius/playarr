"""
Staged import pipeline for Playarr.

Architecture:
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
