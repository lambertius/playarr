# General Issues Compliance Audit

> **Remediation completed 2 August 2026.** The findings below are retained as
> the pre-remediation evidence trail. RENAME-001, SIDE-001 through SIDE-007,
> FILE-001, PREF-001, and VIEW-001 have been closed in the implementation and
> acceptance suite. The normative portability scope is now documented in
> [SIDECAR_RECONSTRUCTION_CONTRACT.md](SIDECAR_RECONSTRUCTION_CONTRACT.md).
> UI collection controls now share one accessible view toggle; applicable
> library, facet, archive, playlist, review, and metadata-artwork views persist
> their layout in revisioned database preferences. Workflow-only screens remain
> intentionally exempt.

**Audit date:** 2 August 2026
**Repository revision:** `ac23a70` (`main`)
**Scope:** UI/UX consistency, cross-device setting persistence, grid/list navigation, reclassification renames, file-access deferral, and authoritative sidecar reconstruction.

## Executive conclusion

At the audited baseline revision, the requested compliance level was **not yet met**.

The repository contains sound foundations: a shared frontend design system, a typed database-backed preference registry, a previewed and journalled rename executor, stable identities, atomic sidecar writes, a sidecar outbox, portable archive manifests, and a two-pass rebuild service. The focused tests for those components pass.

However, end-to-end integration has material gaps. The most serious are:

1. metadata and review reclassification paths still call a retired rename endpoint that always returns HTTP 410;
2. an identity edit can commit to the database, fail that rename call, and then skip the sidecar write;
3. sidecar integrity hashes are required but never verified;
4. “full database reconstruction” excludes AI results, scene-analysis state, several provenance/attribution fields, history tables, and other non-derived state;
5. only Playarr-managed playback locks enter the deferred retry state—external file locks become reconciliation failures;
6. database-backed UI preferences and grid/list controls cover only part of the navigation surface.

These are correctness and recoverability issues, not merely polish items.

## Compliance summary

| Requirement | Status | Summary |
|---|---|---|
| Consistent UI/UX | Partial | Shared tokens and components exist, but page shells, headings, controls, and data-view implementations remain inconsistent. |
| Settings stored in the database where possible | Partial | Core preferences are database-backed, but several navigation and editor preferences remain component-local; open clients synchronize only at startup. |
| Grid/list switching in navigation panes | Partial | Library, Artists, Albums, Years, Genres, Ratings, Quality, and Archive support both. Several meaningful collection views do not. |
| Rename media, sidecars, and artwork after reclassification | Failing | The durable executor covers companions, but common reclassification call sites still use the retired endpoint. |
| Queue rename while a file is accessed | Partial | Internal streaming is deferred and retried; external OS locks are not classified or retried as “waiting for release.” |
| Rebuild the database from sidecars with provenance | Failing | Core media metadata and relationships restore, but the database cannot be recreated as-is and integrity is not verified. |
| Relink a moved archive to its source | Partial | Portable PVD identities and archive manifests work, but identity naming is inconsistent and the entity UUID is not written into v2 archive manifests. |

## Critical findings

### P0 — RENAME-001: internal reclassification paths invoke a permanently retired endpoint

The old `rename_to_expected` route now unconditionally raises HTTP 410 and directs callers to preview/commit ([library.py](../backend/app/routers/library.py#L2566)). Internal code still calls it:

- automatic rename after editing artist, title, or version ([library.py](../backend/app/routers/library.py#L1773));
- batch review renames ([resolve.py](../backend/app/routers/resolve.py#L1052));
- single review “apply rename” ([resolve.py](../backend/app/routers/resolve.py#L1570)).

The automatic edit path catches the 410, logs it, and continues. Because `_needs_rename` remains true, it also skips the fallback NFO and Playarr XML rewrite ([library.py](../backend/app/routers/library.py#L1790)). The resulting state can be:

- new classification in SQLite;
- old folder and filenames on disk;
- old NFO data;
- old `.playarr.xml` data;
- no durable rename command queued.

The review `set-version` path writes the new sidecar but never requests a rename ([resolve.py](../backend/app/routers/resolve.py#L1541)), so it creates a different inconsistency: database and sidecar agree while the managed names remain stale.

**Required remediation:** remove all in-process calls to the retired route. Every identity or naming-classification mutation must atomically enqueue the same previewed `FileOperation`/`MutationCommand` workflow or explicitly create a durable “rename required” operation. Add end-to-end tests for manual metadata edit, inline version edit, review reclassification, single review rename, and batch rename.

### P0 — SIDE-001: common mutations do not atomically create their sidecar outbox record

The outbox design correctly supports creating a sidecar record in the same transaction as a database mutation. Several older paths instead commit first and call `write_playarr_xml` afterward. Examples include manual video updates ([library.py](../backend/app/routers/library.py#L1767)), review approval/dismissal/version changes ([resolve.py](../backend/app/routers/resolve.py#L1500)), and other router/AI call sites.

A crash between the commit and the later outbox scheduling leaves a committed database state with no repairable sidecar intent. `SidecarOutbox` cannot recover work it was never told about.

The RENAME-001 branch is worse: it can skip scheduling altogether.

**Required remediation:** place `schedule_sidecar_write()` inside every authoritative mutation transaction. Treat direct `write_playarr_xml()` calls from mutation paths as prohibited by a static check. The reconciler should only materialize already-committed outbox entries.

### P0 — SIDE-002: sidecar `contentHash` and media checksum are not validated

Sidecar v2 writes a SHA-256 `contentHash` ([playarr_xml.py](../backend/app/services/playarr_xml.py#L529)). Validation only checks that the attribute exists; it does not recompute or compare it ([sidecar_store.py](../backend/app/services/sidecar_store.py#L27)). Existing tests deliberately accept placeholder values such as `sha256:test`.

Likewise, rebuild imports the stored media `file_checksum` but does not verify the discovered media file, its existence, or its size. A well-formed but corrupted or mismatched sidecar can therefore be treated as authoritative.

**Required remediation:** define a canonical hash procedure, verify it before any restore mutation, verify the media checksum/size when present, and quarantine failures. Add tamper, truncation, wrong-media, missing-media, and stale-sidecar acceptance tests.

### P0 — SIDE-003: the sidecar representation cannot recreate the database as-is

The rebuild covers `VideoItem` core metadata, sources, quality, artwork, portable relationships, selected global manifests, and append-only `FieldProvenanceEvent` records. It deliberately excludes scene analysis ([sidecar_restore.py](../backend/app/services/sidecar_restore.py#L54)) and does not serialize/restore several authoritative tables and fields.

Notable losses include:

- `AIMetadataResult`: provider/model, run status, requested fields, confidence, original input, accepted fields, raw response, prompt, token use, errors, and timestamps;
- `AISceneAnalysis`: status, scene boundaries, configuration, errors, and timestamps; thumbnail XML is written but then excluded from rebuild;
- per-field user attribution and timestamps: `field_provenance_users`, `field_provenance_at`, `field_verifications`, and `last_edited_by`;
- rating attribution: `song_rating_by/at` and `video_rating_by/at`;
- metadata snapshots and normalization history;
- pinned match/match decision state and review action plans;
- editor queue state and durable job/event history where it represents unfinished or user-visible work;
- contribution state/history needed to reproduce submission status.

`processing_state` can indicate that an AI step completed, but it cannot distinguish all attempted, failed, dismissed, accepted, or partially applied runs. It does not satisfy the requirement to determine fully whether and how AI was run.

**Required remediation:** publish a reconstruction contract that classifies every table/column as `portable-authoritative`, `portable-derived`, `instance-only`, or `ephemeral`. Every authoritative field needs a versioned sidecar/manifest representation and restore test. “Full rebuild” should compare a logical projection of every authoritative aggregate, not a small selected tuple.

## High-priority findings

### P1 — FILE-001: external file access is not deferred

The journalled executor handles Playarr playback by requesting stream release, waiting one second, and changing the operation to `waiting_for_release` if the internal stream remains active ([file_operations.py](../backend/app/services/file_operations.py#L307)). The background reconciler retries that state ([reconciliation_runtime.py](../backend/app/services/reconciliation_runtime.py#L44)). This is a good internal pathway.

An external process holding the file is only discovered when `os.replace`, copy, or delete raises an OS error. That falls into the generic `reconciliation_required` branch rather than `waiting_for_release`; it is not retried during normal runtime. Startup reconciliation rolls partial moves back, but does not preserve the requested rename as pending work.

**Required remediation:** classify sharing/permission violations that indicate a transient open handle, retain the immutable plan, use bounded exponential backoff with `next_attempt_at`, expose cancel/retry state, and distinguish permanent access denial from transient sharing violations. Add Windows external-handle tests and restart-during-wait tests.

### P1 — SIDE-004: terminal sidecar failures have no repair workflow

The sidecar reconciler retries five times and then marks the entry `failed` ([sidecar_outbox.py](../backend/app/services/sidecar_outbox.py#L118)). Health statistics expose the count, but there is no user-facing retry/cancel/inspect operation comparable to the TMVDB contribution outbox.

**Required remediation:** retain retry metadata and next-attempt time, expose failed entries and diagnostics, provide retry and supersede controls, and add a periodic reconciliation scan comparing entity revision with sidecar revision.

### P1 — SIDE-005: moved-library discovery trusts stored layout over adjacent media

If a sidecar contains `relative_path`, restore resolves it under the selected library root ([sidecar_restore.py](../backend/app/services/sidecar_restore.py#L97)). It does not fall back to the media next to that sidecar when the root depth or folder layout changed, and it does not verify that the resolved path exists.

**Required remediation:** discover candidates in this order: stable manifest mapping, adjacent stem match, stored relative path, checksum search. Require a unique checksum/identity match or report ambiguity. Never commit a `file_path` that was not observed or explicitly marked missing.

### P1 — SIDE-006: archive identity contracts are inconsistent

Per-video sidecars carry both an entity UUID (`entityId`/`stable_id`) and a deterministic content identity (`playarrVideoId`). Archive v2 manifests write only a field named `playarr_video_id`. File-operation and library-manifest code sometimes place `VideoItem.stable_id` into fields labelled `playarr_video_id`.

This currently works through compatibility fallbacks, but it makes collision handling, reclassification, and future migrations ambiguous.

**Required remediation:** use explicit fields everywhere: `entity_id` (immutable UUID), `playarr_video_id` (content/version identity), and `playarr_track_id`. Archive linkage should prefer immutable `entity_id`, corroborate with content ID/checksum, and retain both old and new paths as non-authoritative hints.

### P1 — SIDE-007: global state depends on one library manifest

Playlists, review cases, consolidations, and archive-operation projections live in a singleton `.playarr-library-manifest.json`, not the per-video sidecars ([consolidations.py](../backend/app/services/consolidations.py#L166)). Without that file, “rebuild from sidecars” silently produces only a subset of the database. Restore does not automatically fall back to its `.bak` copy.

**Required remediation:** make this dependency explicit in the compliance contract, validate its content hash, support backup recovery, and report missing global state as an incomplete rebuild rather than success. Consider redundant per-aggregate manifests if sidecars must be independently sufficient.

## UI and preference findings

### P1 — PREF-001: database-backed preferences cover only a subset of persistent UI choices

The `pref.*` namespace in `AppSetting`, typed registry, optimistic revisions, startup hydration, and legacy migration are strong foundations ([preferences.py](../backend/app/routers/preferences.py#L1), [preference_registry.py](../backend/app/services/preference_registry.py#L85)). Registered groups cover library, queue, review, archive, shared panels, party mode, and artwork.

Persistent-looking choices still held only in component state include:

- playlist list sort and playlist-entry sort ([PlaylistsPage.tsx](../frontend/src/pages/PlaylistsPage.tsx#L28));
- video-editor tag filter, sort, direction, page size, and CRF default ([VideoEditorPage.tsx](../frontend/src/pages/VideoEditorPage.tsx#L198));
- metadata-manager entity type, sort order, and page size ([MetadataManagerPage.tsx](../frontend/src/pages/MetadataManagerPage.tsx#L1285));
- archive reason filter (while archive view and page size are persisted);
- log tail-line preference.

Open devices do not subscribe to preference changes. Hydration occurs once before the React tree loads ([main.tsx](../frontend/src/main.tsx#L7)); another device’s changes appear only after reload. Offline debounced writes can also remain local until another user change occurs.

**Required remediation:** inventory every UI state and classify it as URL/shareable state, server preference, workflow draft, or ephemeral state. Add the server-preference fields above, and add polling/SSE revision invalidation for active-device convergence.

### P1 — VIEW-001: grid/list coverage is incomplete and implemented three ways

Current coverage:

| Navigation view | Grid/list | Persistence implementation |
|---|---|---|
| Library | Yes | Custom `library` preference and URL state |
| Artists, Albums, Years, Genres, Ratings, Quality | Yes | Shared `DataView` and `panels` maps |
| Archive | Yes | Custom `archive` preference |
| Playlists | No | Fixed master/detail layout |
| New Videos | No | Fixed carousels plus search grid |
| Review | No | Fixed review cards |
| Video Editor queue | No | Fixed table/list |
| Metadata artwork browser | No | Fixed grid |
| Queue, Settings, Import, Scraper Tester | Not generally applicable | Workflow/tool screens rather than alternate collection presentations |

The controls that do exist are duplicated across `LibraryPage`, `ArchivePage`, and `DataView`, so styling, pagination semantics, URL names, page-size options, and accessibility can drift.

**Required remediation:** extend a single collection-view primitive to Playlists, New Videos results, Review, Video Editor queue, and Metadata artwork browsing where both presentations add value. Keep non-collection workflow screens exempt. Store each view by a stable page key in the preference registry.

### P2 — UI-001: page shells and interaction language remain inconsistent

The design tokens and utility classes in `index.css`, plus shared feedback, tooltip, popup, and setting-row components, provide a coherent base. Remaining inconsistencies include:

- mixed `text-xl` and `text-2xl` page headings;
- centered versus left-aligned content containers and varying maximum widths without a shared page-shell policy;
- bespoke inputs/buttons alongside `input-field` and button utilities;
- separate empty/loading/error approaches on large pages;
- large monolithic pages (`SettingsPage`, `ReviewQueuePage`, `VideoDetailPage`, `MetadataManagerPage`) that duplicate local UI patterns.

**Required remediation:** introduce `PageShell`, `PageHeader`, `CollectionToolbar`, `ViewToggle`, and standardized async-state wrappers. Document intentional density variants for browse, workflow, detail, and fullscreen screens. Add visual regression/a11y tests for one representative page of each type.

## What is already working

- The durable rename planner inventories video, NFO, Playarr XML, Kodi artwork suffixes, and otherwise unknown companion files recursively; it computes checksums and collision previews.
- File transitions are journalled step-by-step, cross-volume copies are verified, database paths update only after file installation, and interrupted partial moves can be rolled back.
- The new HTTP rename preview/commit flow and operation polling are implemented.
- Sidecar writes use temporary files, fsync, validation, atomic replacement, and a backup copy.
- Sidecars contain portable entity/content IDs, sources, relative paths, processing state, core provenance events, quality data, artwork provenance, and stable relationship references.
- Two-pass rebuild avoids dependence on SQLite row IDs and restores selected portable global aggregates.
- Archive manifests contain portable PVD identity, operation ID, relative original path, SHA-256, reason, and timestamps; the archive catalog can be rebuilt from disk.
- The preference API validates typed patches and uses optimistic revisions to avoid silent whole-group overwrites.

These components should be retained; the remediation is primarily about making every mutation use them and expanding the reconstruction contract.

## Verification performed

The following checks passed against this revision:

- 27 focused backend tests covering preference/settings registries, file operations, sidecar storage/outbox/rebuild, and archive catalog/restore;
- 7 focused frontend tests covering `DataView`, preference migration, and rename preview;
- TypeScript compilation and Vite production build.

Passing component tests do **not** cover the failed legacy call sites or prove full reconstruction. Missing acceptance coverage includes:

- identity edit → durable rename → all companions renamed → DB paths updated → sidecar revision converged;
- review version reclassification and batch rename through the new command workflow;
- external file handle → deferred retry → eventual success across restart;
- tampered sidecar/media checksum rejection;
- complete logical database projection before and after rebuild;
- missing/corrupt global manifest recovery;
- live cross-device preference convergence;
- grid/list behavior and persistence on every applicable navigation view.

## Recommended delivery order

1. **Stop divergence:** fix RENAME-001 and SIDE-001, then add the end-to-end reclassification tests.
2. **Establish trust:** validate sidecar and media hashes; quarantine invalid inputs.
3. **Define portability:** classify every model/field and version the complete sidecar/manifest schema.
4. **Complete rebuild:** add AI/provenance/history/archive representations and full logical-projection tests.
5. **Harden deferral:** treat external locks as durable retryable operations with observable control.
6. **Finish consistency:** consolidate page/view primitives, expand database preferences, and add applicable grid/list modes.

## Release gate

Do not describe sidecars as capable of fully recreating a Playarr database until all P0 findings are closed and an empty-database acceptance test proves equality of the complete **portable-authoritative logical projection**, including AI execution state, provenance attribution, edits, redownload/archive relationships, playlists, review state, and stable identities after moving both library and archive roots.
