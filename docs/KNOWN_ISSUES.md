# Known Issues & Technical Debt

This document tracks known limitations, technical debt, and areas for improvement.

---

## Pipeline Duplication

**Severity:** High — largest source of technical debt

Three parallel pipeline implementations exist:

| Directory | Status | Purpose |
|-----------|--------|---------|
| `backend/app/pipeline_url/` | Active | URL-based imports (YouTube, Vimeo) |
| `backend/app/pipeline_lib/` | Active | Library file imports (existing files on disk) |
| `backend/app/pipeline/` | Legacy | Original implementation, superseded |

`pipeline_url/` and `pipeline_lib/` share nearly identical structure (stages, workspace, mutation_plan, db_apply, deferred, services/) but diverge in download vs. file-scan logic. The legacy `pipeline/` is no longer actively used.

**Impact:** Bug fixes and improvements must be applied to both active pipelines. Divergence risk increases over time.

**Recommended fix:** Consolidate into a single pipeline with a strategy pattern for the source-specific stages (download vs. file scan). See `docs/Scraper_Consolidation_Plan.md` for prior analysis.

---

## Test Coverage

**Severity:** Medium

Test coverage is incomplete. The `backend/tests/` directory contains ~7 test files covering basic functionality, but many subsystems (AI, entity resolution, deferred tasks, matching) lack tests.

**Impact:** Regressions may go undetected.

**Recommended fix:** Prioritise integration tests for the import pipeline and unit tests for the metadata resolver.

---

## SQLite Concurrency

**Severity:** Low (mitigated)

SQLite in WAL mode with dual connection pools (main=20, cosmetic=10) handles typical concurrency well. However, under very high load (many simultaneous imports + UI operations), lock contention can occur.

**Mitigation in place:** WAL mode, separate pools for heavy vs. lightweight writes, busy_timeout, retry logic.

**Long-term fix:** PostgreSQL support (config.py already accepts a `DATABASE_URL` connection string).

---

## Service Module Duplication

**Severity:** Medium

Some service modules exist in both `pipeline_url/services/` and `pipeline_lib/services/` with near-identical implementations. Additionally, `backend/app/services/` contains shared services that are sometimes duplicated in pipeline-specific directories.

Key duplicated services:
- `media_analyzer.py` — quality signature extraction
- `file_organizer.py` — file naming and organisation
- `normalizer.py` — audio normalisation
- `nfo_parser.py` — NFO reading/writing
- `metadata_resolver.py` — metadata scraping

**Recommended fix:** Extract shared logic to `backend/app/services/` and import from pipeline modules.

---

## Outdated Internal Documentation

**Severity:** Low

Some docs in `docs/` were written during development and may reference outdated implementations or planned features that were implemented differently:
- `artwork_pipeline.md` — may not reflect current artwork flow
- `concurrency_diagnostic.md` — historical debugging notes
- `URL_Pathway_Scraping_Report.md` — audit from an earlier refactor

These are preserved for historical context but should not be treated as current specifications.

---

## Frontend Build Size

**Severity:** Low

The frontend includes all page components in a single bundle. Code splitting by route would reduce initial load time.

---

## Preview Cache Cleanup

**Severity:** Low

Preview clips are cleaned on startup for deleted videos, but there's no scheduled cleanup during runtime. Long-running servers accumulate orphaned previews until restart.

---

## AI Provider Error Handling

**Severity:** Low

AI provider errors (rate limits, timeouts, invalid responses) are handled but could surface more actionable messages in the UI. JSON parse failures from AI responses are retried once with a simplified prompt.
