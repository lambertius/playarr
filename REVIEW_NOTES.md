# Playarr external review snapshot

- Snapshot date: 2026-08-01
- Application version: `1.10.0`
- Database schema: `025_tmvdb_outbox`

This package is a compact source-and-runtime snapshot prepared from the current
working tree after implementing the requirements in:

- `docs/Playarr Improvements and bugs.docx`
- `docs/Playarr_Product_Completion_and_Remediation_Specification_v2.0.docx`

## What is included

- Current backend and frontend source, tests, migrations, and project docs.
- The production frontend build in `frontend/dist`.
- A consistent snapshot of the active `backend/playarr.db` database. Secret
  setting values are blanked in the packaged copy; the live database is not
  modified.
- Five representative files in `sample-sidecars`: three existing v1 sidecars
  demonstrating migration compatibility and two v2 exports generated from
  active records using the current writer.

## Main remediation areas implemented

- Crash-safe sidecar v2 writes, stable portable identities, revisions, content
  hashes, reconciliation, and a durable outbox.
- Durable mutations, operation correlation, deployment profiles, health and
  migration diagnostics.
- Playlist revisions and occurrence-aware batch editing.
- New Videos acceptance, replacement, diversity, and completeness rules.
- Canonical queue paging/categories, durable downloads and editor jobs.
- Review cases, dependency edges, remediation plans, crop evidence, and source
  fidelity checks.
- Persistent playback surfaces, party-start gating, shared data views, route
  boundaries, artist consolidation, genre management, and preference registry.
- Archive restore plans with integrity/conflict reporting.
- Structured AI lifecycle state, settings constraints and secret masking.
- TMVDB eligibility, previews, durable contribution outbox, retry/cancel, and
  reconciliation.
- One import policy across URL/library/rescan entry points, consolidated leaf
  services, structured/redacted scraper traces, and diagnostic bundles.
- API/OpenAPI contract corrections. The retired public Kodi add-on/export
  surfaces are no longer mounted; generic internal NFO interoperability remains.

## Verification at packaging time

- Backend: `469 passed`.
- Frontend production build: passed.
- OpenAPI: generated successfully with 265 paths; no public Kodi routes.
- Packaged database: SQLite integrity check `ok`; Alembic head
  `025_tmvdb_outbox`.
- Full frontend lint still reports inherited cleanup debt (65 errors and 9
  warnings, improved from the initial 67 errors and 11 warnings). It does not
  block the production build.

## Deliberate exclusions

- `ffmpeg`, `ffprobe`, `yt-dlp` executables and installer/build output.
- `node_modules`, virtual environments, caches, logs, temporary workspaces, and
  Python bytecode.
- `.env`, database backups/WAL files, generated media, artwork caches, and the
  full sidecar library.
- The retired `kodi` add-on directory.

This is an external-review sample, not a media bundle or signed installer. The
active library preflight also found 37 database media paths unavailable from the
packaging environment and one unavailable/unwritable sidecar location; those
environment-specific media files are intentionally not copied here.

## Remaining architectural note

The URL and library pipelines now share import-policy semantics and canonical
leaf services. Their legacy stage adapters remain during the parity and
fault-injection phase; deleting those adapters is intentionally deferred until
the final pipeline-convergence gate is complete.
