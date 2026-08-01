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
| BASE-001..004 | Partial | BASE-001/002 have hash-locked one-command verification and Windows CI; BASE-004 has a frozen module-growth gate. The sanitised SDR/HDR/aspect/VFR/live/cover/duplicate fixture set exists, but it is not yet consumed by every pipeline/review/archive/editor acceptance workflow required by BASE-003. |
| ARCH-001..003 | Partial | Startup rejects unsafe deployment combinations, single-process and Redis mutation workers are explicit, and mutable models expose more revisions. Playlist/consolidation/review/settings conflict coverage and the instrumented p99/no-external-I/O transaction gate remain incomplete. |
| PIPE-001..004 | Partial | Active URL and disk paths now share `ImportWorkspace`, `ImportPolicy`, one mutation-plan builder, one Stage-C apply actor and one deferred dispatcher. Checkpoint resume, parity and structured stage-event tests pass. Stage-B metadata/artwork implementations are still copied rather than one typed stage graph, and the full fixture fault matrix is incomplete. |
| DB-001..005 | Partial | Rating, New Videos, rename and canonical import apply use the durable priority actor. The 4-import/500-backlog admission test, bounded cosmetic queue, idempotency and lock-retry telemetry pass. Playlist/review/consolidation mutations and all background domain writes have not yet crossed the boundary; every large result family is not yet SQL-paginated. |
| SIDE-001..006 | Partial | Stable portable relationships, v2 validation/hash/revision, atomic backup writes and commit/outbox/restart repair have focused gates. A two-pass empty-database fixture rebuild now proves logical parent/duplicate/version equality under changed row numbering and rejects unclassified parser fields. Playlist/review/archive manifest projection and removal of every legacy path-specific mapper are still incomplete. |
| FILE-001..005 | Partial | `FileOperationService` now plans, journals, executes and reconciles rename transitions with collision and restart coverage; the UI previews and polls operation IDs. Archive/editor move and delete paths do not all use the service, and cross-volume/in-use/fault matrices remain incomplete. |
| PREF-001..003 | Partial | A typed backend registry and revision-aware patch API exist. Scope migration, multi-device behaviour, and removal of unmanaged browser persistence are incomplete. |
| UI-001..005 | Partial | Shared `DataView`, aligned sortable definitions, URL state, persisted view preferences, lazy routes and an error boundary exist; an action-language regression gate now covers permanent metadata/editor/review actions. Grouped navigation still sorts/pages some already-fetched aggregates client-side and responsive/focus coverage is incomplete. |
| API-001..004 | Partial | Static/dynamic route-shape tests pass, global failures use one structured envelope, and OpenAPI/generated TypeScript are drift-checked. Conflict, collision, lock, timeout, validation and unexpected-error envelope tests pass. Kodi settings/pages/routes/imports, exporter source and bundled assets are removed with a migration note. Exhaustive shape-valid invocation of every OpenAPI route remains incomplete. |
| PLAY-001..008 | Partial | One persistent media node, occurrence IDs, session-token event guards, unified chrome activity, explicit start filters/playlist precedence, three 72px TV controls and recoverable autoplay/error UI are implemented. Browser-level identity/stale-event and codec/transcode fixture acceptance tests remain incomplete. |
| LIB-001..005 | Partial | Artist/title/year/quality/version/AI/added columns align through a shared grid template; grouped navigation uses `DataView`, and library query state is server-backed and URL-canonical. Group aggregate paging remains partly client-side and enrichment lifecycle/tooltips do not yet expose every required provider/attempt/stale field. |
| PL-001..005 | Partial | A revisioned batch endpoint and occurrence IDs exist. The complete draft reducer, selected-position sorting UI, accessible drag/keyboard reorder, and failure workflows are not accepted. |
| NV-001..007 | Partial | Backend acceptance/dismissal, completeness, replacement, and diversity helpers have focused tests. Durable command linkage and complete on-page frontend replacement/fresh-snapshot workflows are not accepted. |
| QUEUE-001..006 | Partial | Backend paging/classification and some tab UI exist. The reference layout, durable cancellation state machine, yt-dlp lifecycle, exact retention UX, and system-health acceptance remain incomplete. |
| REV-001..007 | Partial | Case/item/edge models and focused case tests exist. Category generation, equal playable comparison panels, staged multi-action UI, bounded reclassify control, parity, and file-failure recovery are incomplete. |
| EDIT-001..009 | Partial | Profile and crop helper tests exist. Production encode command coverage, staging/probe/decode replacement safety, source/HDR/audio metadata comparison, golden media, manual correction, and sidecar evidence gates are incomplete. |
| META-001..006 | Partial | Artist consolidation persistence and conflict helpers exist. The specified three/two-column editors, complete genre aggregate, resolved manager queries, portable definitions, and rebuild gate are incomplete. |
| ARC-001..003 | Partial | Restore-plan preview has one focused test. Queue-consistent DataView, full commit consequences, archive integrity maintenance, orphan reporting, and recovery acceptance are incomplete. |
| SCRAPE-001..004 | Partial | Typed policy and structured trace helpers exist. Scraper Tester still invokes pipeline-specific services directly; the production-path matrix and complete diagnostic workflow are not accepted. |
| SET-001..005 | Partial | Dynamic settings use the specified responsive three-column `SettingRow`, secrets are masked, and settings are regrouped by runtime domain; Kodi UI/API/bundling was removed. Manual subsection layouts, generated consumer audit, all dependency migrations and redacted connection tests remain incomplete. |
| TMVDB-001..005 | Partial | Eligibility and outbox helpers have focused backend tests. Complete field provenance, reviewed pull workflow, contribution-state UI, cancel/retry, and provider reconciliation are incomplete. |
| OBS-001..003 | Partial | Request/operation fields and diagnostics endpoints exist. Correlation is not propagated through every work path and authoritative file/sidecar failures are still broadly swallowed in legacy code. |
| MIG-001..003 | Partial | Startup now runs preflight, online backup, Alembic upgrade, integrity check, reconciliation and restore-on-failure before recovery queries; the report exposes exact discrepancy/repair actions and has legacy-schema tests. v1/v2 dual-read plus v2/v1-compatible write exists, but persisted migration metrics and the documented v1-write deprecation exit are incomplete. |

## Immediate dependency order

1. BASE-001..004: make verification reproducible and release-blocking.
2. ARCH/DB/PIPE: enforce one mutation boundary and one import engine.
3. SIDE/FILE/MIG: make portable state and filesystem changes recoverable.
4. Complete user-facing families only on the accepted contracts above.
5. Run the Definition of Done and rebuild the installer only after all remaining
   `MUST` rows have acceptance evidence.
