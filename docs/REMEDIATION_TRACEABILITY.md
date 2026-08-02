# Playarr v2.0 remediation traceability

This is the release-gate ledger for
`Playarr_Product_Completion_and_Remediation_Specification_v2.0.docx`.

The specification contains 115 requirements: 108 `MUST` and seven `SHOULD`.
An implementation is not accepted because a similarly named model, endpoint,
component, or unit test exists. `Accepted` means the requirement's stated
acceptance workflow passes, including persistence, recovery, portability,
concurrency, multi-device, and UI behaviour where applicable.

Status definitions:

- `Missing`: the required production capability or release gate does not exist.
- `Partial`: some contract or UI exists, but the acceptance criteria do not pass.
- `Accepted`: acceptance evidence is automated and linked here.

| Requirement IDs | Status | Current evidence and release-blocking gap |
| --- | --- | --- |
| BASE-001..004 | Accepted | Hash-locked backend/frontend verification and Windows CI are release-blocking; SDR/HDR/10-bit/crop/corrupt media fixtures feed pipeline/editor/review acceptance, and the frozen-module growth gate passes. |
| ARCH-001..003 | Accepted | Unsafe deployment combinations are rejected, writers share the serialized boundary, mutable aggregates use stable IDs/revisions, and health diagnostics expose measured write-transaction p50/p95/p99 with contention coverage. |
| PIPE-001..004 | Accepted | URL and disk imports now execute one canonical typed stage graph with shared policy, workspace checkpoints, mutation-plan builder, Stage-C actor, deferred dispatcher, parity coverage and resume cleanup. Former pipeline modules are compatibility re-exports only. |
| DB-001..005 | Accepted | All request and background writers share the serialized write boundary; priority commands cover interactive/import work, queues are bounded and durable, aggregates use stable revisions/idempotency, lock retry telemetry is exposed, and library/queue/review/archive/metadata families use bounded SQL paging. |
| SIDE-001..006 | Accepted | Stable portable relationships, v2 validation/hash/revision, atomic writes and the durable outbox are covered. Empty-database rebuild restores videos, parent/duplicate/version links, playlists, review graphs, artist/genre consolidations, provenance events and archive operations without relying on SQL row IDs. |
| FILE-001..005 | Accepted | `FileOperationService` plans, journals, verifies, executes and reconciles rename/archive/restore/editor replacement sets. Cross-volume verification, collision, in-use waiting, step-fault rollback and restart recovery are automated. |
| PREF-001..003 | Accepted | Typed group registry, revision-aware merge/409/422 handling, durable server job/editor state, and one-time legacy browser migration with delete-on-success and retry-on-failure are automated. |
| UI-001..005 | Accepted | Dense management surfaces share server paging/filter contracts and `DataView` conventions; sort aria state, responsive view preferences, URL restoration, focus/live-region feedback, lazy routes, error boundary and action-language regression tests pass. |
| API-001..004 | Accepted | Route-shape and global envelope tests cover conflicts, collision, lock, timeout, validation and unexpected failures; generated OpenAPI/TypeScript drift gates pass, and Kodi API/UI/assets were removed with migration evidence. |
| PLAY-001..008 | Accepted | Persistent media identity, occurrence/session guards, stale-event suppression, unified activity chrome, explicit party filters, 72px TV controls, codec/transcode startup and recoverable autoplay/error states have frontend and media-fixture tests. |
| LIB-001..005 | Accepted | Shared aligned columns, bounded enrichment state, URL-canonical server queries and grouped `DataView` parity/paging are implemented across artist/album/genre/year/quality/rating navigation. |
| PL-001..005 | Accepted | Revisioned batch commits, occurrence IDs, reducer-owned drafts, selected-position sorting, keyboard reorder/live announcements and stale-conflict reapply/reload workflows are covered. |
| NV-001..007 | Accepted | Durable refresh/import commands, feedback linkage, category completeness/replacement/diversity/freshness, snapshot counts and retry/restore failed-addition workflows have backend and on-page UI coverage. |
| QUEUE-001..006 | Accepted | Server-classified active/history categories, bounded SQL paging, durable cross-process cancellation, yt-dlp job lifecycle, exact retention actions and health/backlog diagnostics are covered by focused contracts. |
| REV-001..007 | Accepted | Durable cases/items/pairwise edges, structured category/orphan triggers, equal playable panels, staged consequence plans, bounded reclassification, revision parity and evidence-hash reopen/file-recovery coverage pass. |
| EDIT-001..009 | Accepted | Production profile/crop commands, journalled staging/probe/decode replacement, HDR/10-bit/audio/source comparison, golden media/manual correction and portable sidecar evidence tests pass. |
| META-001..006 | Accepted | Artist and genre consolidations are non-destructive stable-ID/revision aggregates with create/edit/delete APIs, conflict evidence, three/two-column accessible editors, resolved navigation, provenance-bearing members and portable manifest rebuild coverage. |
| ARC-001..003 | Accepted | SQL-backed archive paging/search/reason counts, list/grid parity, operation/checksum/path/eligibility detail, persisted restore preview/conflict choices, integrity/orphan maintenance and restart-safe recovery tests pass. |
| SCRAPE-001..004 | Accepted | Production and dry-run use one typed `ImportContext`/`ImportPolicy` metadata stage; all six pathways × six policies have matrix coverage, and structured redacted traces/diagnostic bundles are downloadable. |
| SET-001..005 | Accepted | Server, AI, discovery, playback, TV/Cast and Party fields share `SettingRowLayout`; the catalogue supplies complete typed metadata/consumer/orphan audits, dependencies are backend-enforced, secrets/tests are redacted, and Kodi removal/grouping is documented. |
| TMVDB-001..005 | Accepted | Eligibility previews gate unverified fields, submissions use an idempotent durable outbox, pulls create reviewed candidates, append-only per-field provenance survives sidecar rebuild, and the contribution UI exposes preview/history/pending/submitted/failed plus retry and cancel. All import/rescan settings now invoke the production workflow. |
| OBS-001..003 | Accepted | Request, operation and import correlation propagates through jobs/traces; health exposes queue/outbox/file-journal/transaction percentiles and slow samples, and authoritative file/sidecar failures are durable/retryable rather than swallowed. |
| MIG-001..003 | Accepted | Preflight, backup, Alembic upgrade, integrity/reconciliation and restore-on-failure run before recovery; reports persist exact discrepancy/repair and schema-read metrics plus the v1/v2 read/v2-write compatibility and documented v1-write exit policy. |

## Immediate dependency order

1. BASE-001..004: make verification reproducible and release-blocking.
2. ARCH/DB/PIPE: enforce one mutation boundary and one import engine.
3. SIDE/FILE/MIG: make portable state and filesystem changes recoverable.
4. Complete user-facing families only on the accepted contracts above.
5. Run the Definition of Done and rebuild the installer only after all remaining
   `MUST` rows have acceptance evidence.

## Definition of Done evidence

Recorded on 2026-08-02 after every requirement family above reached
`Accepted`:

- `scripts/verify_backend.py`: passed the frozen-module growth gate, OpenAPI
  drift gate, single Alembic-head gate, all 557 backend tests, and `compileall`.
- `npm run verify`: passed generated-client drift, lint with zero errors, all 16
  frontend tests, and the production Vite build.
- `git diff --check`: passed with no whitespace errors.
- PyInstaller 6.19.0: built and validated the standalone bundle in the isolated
  `build/release-stage/dist/Playarr` directory, including the production
  frontend and Alembic migrations 027 and 028.
- The frozen executable passed an isolated-AppData startup smoke test and
  returned HTTP 200 from `/api/health`.
- Inno Setup 6.7.1: compiled
  `Output/PlayarrSetup-1.11.0.exe` (77,074,205 bytes). SHA-256:
  `1E5213653A92FD379D30EB29BEC288D1894667131E8133761C47E6F43EEB7EB4`.
