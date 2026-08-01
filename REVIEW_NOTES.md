# Playarr external review snapshot

- Snapshot date: 2026-08-01
- Application version: `1.11.0`
- Database schema: `025_tmvdb_outbox`

This package is a compact source-and-runtime snapshot prepared from the current
working tree after a preliminary remediation pass against:

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

It is **not** a completed implementation of the v2.0 specification. Earlier
wording claimed completion without tracing the 115 requirement IDs or passing
their acceptance gates. That claim was incorrect. See
`docs/REMEDIATION_TRACEABILITY.md` for the current audit.

## Preliminary remediation scaffolding present

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

- Backend: `471 passed`. These are primarily unit and focused API tests; they
  do not cover the complete specification acceptance matrix.
- Frontend production build: passed.
- OpenAPI: generated successfully with 265 paths; no public Kodi routes.
- Packaged database: SQLite integrity check `ok`; Alembic head
  `025_tmvdb_outbox`.
- Full frontend lint still reports inherited cleanup debt. This is a failed
  BASE-002 release gate and must be corrected; a production bundle compiling
  successfully is not sufficient verification.

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

## Known release-blocking gaps

- Active URL and library imports still use separate stage graphs and copied
  pipeline modules.
- The mutation coordinator is not the enforced application write boundary;
  direct commits remain widespread.
- A file-operation plan/journal/executor service and its fault-injection tests
  do not exist.
- Sidecar v2 lacks the required empty-database reconstruction gate.
- Frontend requirement tests and clean-environment CI verification do not
  exist.
- Several backend and frontend features are partial contracts or UI shells and
  have not passed the document's workflow, recovery, concurrency, portability,
  accessibility, or performance acceptance criteria.
