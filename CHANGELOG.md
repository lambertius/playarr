# Changelog

## [1.9.8] - 2026-04-08

### Fixed
- **Queue Display: Tracks Vanishing During Finalizing** — the frontend `isFinalizing()` check used a 2-minute `updated_at` window to detect active deferred processing; with the serialised write queue from v1.9.7, cosmetic DB updates queue up and `updated_at` goes stale, causing tracks to drop off the active tab; removed the time window — jobs now show as "Finalizing" until the step reaches "Import complete"
- **Deferred Timeout Too Aggressive** — `_DEFERRED_TIMEOUT` was 300s (5 min) across all three pipelines; with the serialised write queue and limited semaphore slots, large batches easily exceed this; raised to 1800s (30 min) in `pipeline_url`, `pipeline`, and `pipeline_lib`
- **Watchdog Force-Unsticking Queued Jobs** — the finalizing watchdog (`_FINALIZING_WATCHDOG_MAX_AGE = 600s`) would force-mark jobs as complete while they were still legitimately waiting for the write queue; raised to 2400s (40 min) and the watchdog now checks `active_coordinator_count()` and write queue `pending()` — skips its cycle entirely while the system is actively processing
- **Deferred Semaphore Too Restrictive** — `GLOBAL_DEFERRED_SLOTS` was 3, causing excessive queueing now that DB serialisation is handled by the write queue; raised to 6 since the semaphore only needs to limit I/O load (ffmpeg, network), not DB contention

### Added
- **Deferred Coordinator Tracking** — thread-safe `_active_coordinators` set in `pipeline_url/deferred.py` with `active_coordinator_count()` API; the watchdog uses this to distinguish genuinely stuck jobs from jobs waiting in a busy queue

## [1.9.7] - 2026-04-08

### Fixed
- **Unified DB Write Lock** — three pipelines (`pipeline_url`, `pipeline`, `pipeline_lib`) each had their own `threading.Lock()` for DB writes; when running concurrently (e.g. batch import + rescan + scrape), they could deadlock against each other on the same SQLite file; created a single shared `_apply_lock` in `app/db_lock.py` that all pipelines and the write queue now use
- **Pipeline URL Deferred: Undefined `_apply_lock`** — `pipeline_url/deferred.py` referenced an undefined `_apply_lock` at the Wikipedia poster fallback code path, which would have caused a `NameError`; replaced with `db_write()` wrappers
- **Raw DB Commits Not Serialised** — `pipeline/deferred.py` and `pipeline_lib/deferred.py` had multiple raw `db.commit()` calls outside any lock; all raw commits across both files now wrapped with `with _apply_lock:` to guarantee serialization

## [1.9.6] - 2026-04-08

### Fixed
- **Deferred AI Enrichment Silently Failing** — `_deferred_ai_enrichment` in `pipeline_url/deferred.py` had a `from app.models import VideoItem` re-import inside its error-handling `except` block, which Python treated as a local variable assignment, shadowing the outer-scope import and causing `UnboundLocalError: local variable 'VideoItem' referenced before assignment` on every invocation; the error was caught silently and the task logged as "completed" despite never running AI enrichment or setting the `ai_enriched` processing flag — preventing review queue auto-clear
- **Review Auto-Clear Killed by DB Lock on XML Sidecar Write** — the deferred coordinator's `finally` block ran both the XML sidecar rewrite and the review auto-clear inside a single `try` block; when batch-scraping many items, parallel coordinator threads caused `database is locked` errors on `_final_write_xml`, which aborted the entire block including the auto-clear; the `pipeline_url` variant now routes both operations through `db_write` (the serialized write queue), and the `pipeline`/`pipeline_lib` variants use a retry loop with exponential backoff; in all three, the XML write failure is now caught independently so auto-clear always proceeds

## [1.9.5] - 2026-04-08

### Fixed
- **Review Queue: Items Not Clearing After Scrape** — `scrape_metadata_task` set `ai_enriched` processing flag but never cleared the review status; added auto-clear logic to the Finalise section for `ai_pending`, `ai_partial`, and `scanned` categories
- **Review Queue: Items Not Clearing After Rescan** — `rescan_metadata_task` wrote processing flags (`metadata_scraped`, `metadata_resolved`) but had no review auto-clear in its write phase; added matching auto-clear logic before deferred task dispatch
- **Review Queue: AI Only Mode Skipped Deferred AI Enrichment** — selecting "AI Only" in the scrape modal ran AI in the main pipeline but did not dispatch the `ai_enrichment` deferred task, preventing the deferred auto-clear from firing; now dispatches AI enrichment for both `ai_auto` and `ai_only` modes
- **Review Queue: Scan AI Enrichment Re-Flagged Approved Items** — the `scan-enrichment` endpoint targeted items with `review_status` of both `"none"` and `"reviewed"`, causing items a user explicitly approved to be re-flagged on the next scan; now only targets `"none"` status
- **Review Queue: Deferred Auto-Clear Too Strict** — the auto-clear in all three deferred pipeline coordinators (`pipeline_lib`, `pipeline`, `pipeline_url`) required both `ai_enriched` AND `scenes_analyzed` unconditionally; now parses `review_reason` to check only the specific flags that were missing (e.g. "Missing scene analysis" only requires `scenes_analyzed`)

### Added
- **Review Queue: Scene Analysis in Scrape Modal** — added "Run scene analysis" checkbox (default: on) to the batch scrape options modal; the `scene_analysis` parameter flows through the API to `rescan_metadata_task` which conditionally includes it in the deferred task list
- **Review Queue: Scanned Category Auto-Clear** — items with `review_category = "scanned"` now auto-clear when `metadata_scraped` or `metadata_resolved` processing flags are set, across all three deferred coordinators and both scrape/rescan task finalisers

## [1.9.4] - 2026-04-08

### Fixed
- **Multi-Artist Display** — tracks with multiple artists (e.g. "Zedd; Hayley Williams") now display each artist as a separate clickable link in the Metadata panel instead of a single combined link; the Edit Track IDs modal shows per-artist MusicBrainz ID fields instead of one flat field
- **XML Sidecar Persistence on Library Rescan** — clearing the library and rescanning could lose scene analysis data and entity artwork because the XML sidecar was written before deferred tasks (scene analysis, entity artwork) completed; all three pipeline variants now rewrite the XML sidecar after deferred tasks finish, and scene analysis thumbnails are copied to the video folder for portability
- **Review Queue: Items Not Clearing After Batch Scrape** — review queue items flagged for missing enrichment (scene analysis, AI metadata) were not auto-cleared when a batch scrape resolved the underlying issue; deferred task coordinators now check processing flags on completion and clear the review flag when the issue is resolved
- **Review Queue: Misleading Enrichment Message** — the review reason "Partial AI Enrichment — missing: scene analysis" incorrectly implied AI was required for scene analysis; messages now use a simpler format (e.g. "Missing scene analysis", "Missing AI metadata, scene analysis")

## [1.9.3] - 2026-04-07

### Fixed
- **Library Scan: NULL Loudness & Lost Metadata** — three bugs in library scan import: `autoflush=False` caused loudness/quality writes to silently vanish; rescan destroyed existing source links; partial XML data could overwrite richer DB values. Added `_merge_existing_xml_quality()` helper for safe XML quality merging
- **Feat Artist Normalization: Band Name False Positives** — `parse_multi_artist` incorrectly split band names like "Mumford & Sons", "Coheed and Cambria", "Earth, Wind & Fire", "Iron & Wine" etc. into separate artists; added `_PROTECTED_NAMES` set and `"and the"` pattern guard alongside existing `"& The"` protection

### Added
- **Review Queue: AI Enrichment Categories** — two new review categories ("No AI Enrichment", "Partial AI Enrichment") with scan endpoint to flag tracks missing AI metadata; includes filter pills, help dialog rows, and Scan AI Enrichment button in the review queue
- **Feat → Semicolon Normalization** — artist strings like "DJ Snake feat. Lil Jon" are now normalized to "DJ Snake; Lil Jon" across all pipeline stages (import, rescan, scrape, AI); retroactively corrected 101 existing tracks with updated DB fields, artist_ids, and XML sidecars

### Changed
- **Scraper Tester: Download Log Redesign** — removed per-field comment inputs; enlarged Download Log button; added two-step download dialog with optional feedback for bug reports

## [1.9.2] - 2026-04-06

### Fixed
- **Review Queue: Tab Layout Overflow** — category stat cards could overflow their cells on narrower viewports; grid now scales to 12 columns at xl breakpoints and labels truncate cleanly instead of overflowing
- **Duplicate Review: Orphaned Partner After Deletion** — deleting one video from a duplicate pair correctly cleared the deleted item but left the surviving partner flagged as a duplicate; the survivor now has its review flags cleared automatically when no undismissed partners remain

### Added
- **Sidebar: GitHub Sponsors Link** — unobtrusive "Support the project" link added to the sidebar footer

## [1.9.1] - 2026-04-06

### Fixed
- **AI/Scrape Metadata: Stale Platform Data After Source Correction** — when a user corrected a video's source URL (e.g. from a live version to the studio version) and ran Scrape Metadata, AI Auto, or AI Only, the pipeline used cached platform metadata (title, description, tags, channel) from the original import URL instead of the corrected one; all scrape metadata operations now force-refresh platform metadata from yt-dlp on every run, ensuring the AI and scrapers receive context from the correct video
- **Source URL Edit: Stale Metadata Not Cleared** — editing a source's URL via the Sources panel did not clear the cached `platform_title`, `platform_description`, `platform_tags`, `channel_name`, and `upload_date` fields, leaving stale data from the old video; these fields are now cleared when the URL changes so the next scrape/AI operation fetches fresh data
- **Description: Unable to Clear** — clearing the description textarea and saving had no effect because the frontend converted the empty string to `null` (which the backend interprets as "don't change"); empty strings are now sent correctly, allowing the description to be cleared

## [1.9.0] - 2026-04-06

### Added
- **Canonical Track Linking System** — comprehensive hierarchical version relationship system: videos can be linked to canonical tracks (shared identity across versions), with parent-child version chains, confidence scoring, and provenance tracking (auto/user)
- **Canonical Track Panel Overhaul** — the canonical track card on the video detail page now supports inline editing of track metadata, scanning for matching canonical tracks, creating new canonical tracks, linking/unlinking, and displays parent video relationships and provenance badges
- **Canonical Track API** — new endpoints for scanning library for canonical matches (MBID → fingerprint → fuzzy fallback), linking/unlinking canonical tracks, creating/editing canonical tracks manually, setting parent video relationships, and library-wide canonical issue scanning
- **Review Queue: Canonical Categories** — three new review categories: "No Canonical Track" for unlinked videos, "Canonical Conflict" for metadata mismatches, and "Low Canonical Confidence" for uncertain auto-links
- **Version Types: Remix & Acoustic** — `remix` and `acoustic` are now first-class version types across the entire stack: version detector classifies them independently (previously grouped under "alternate"), badges render with distinct colours (cyan/amber), all dropdowns and filters include them, and they are preserved in XML export/import
- **Version Type Consistency** — all VERSION_TYPE_OPTIONS across the frontend (MetadataEditorForm, ReviewQueuePage, SettingsPage, Badges, ImportLibraryPage) are now consistent, including `uncensored` and `18+` in all applicable locations

## [1.8.1] - 2026-04-06

### Fixed
- **Library View: Database Schema Upgrade** — library view and track-level views failed to load because two new columns (`rename_dismissed`, `exclude_from_editor_scan`) were added to the VideoItem model but not registered in the startup schema upgrade function; existing databases now have these columns added automatically on startup

## [1.8.0] - 2026-04-06

### Added
- **Review Queue: Rename Dismiss & Scan Modes** — rename review items can now be dismissed so they don't re-flag on future scans; a "New / All" toggle on the Scan Renames button lets you choose between scanning only new mismatches (default) or re-scanning all files including previously dismissed items
- **Startup Rename Scan** — optional setting to automatically scan for naming convention mismatches when the server starts, populating the Review Queue; previously dismissed items are skipped
- **Settings: Rename Scan on Startup Toggle** — new toggle in Server settings to enable/disable automatic rename scanning at launch, with tooltip explaining behaviour

### Fixed
- **Queue: False "Stuck" Status on Completed Jobs** — completed jobs (redownloads, normalizations, metadata scrapes, exports) were incorrectly showing a red "Stuck" badge due to case-sensitive terminal step matching and overly broad finalizing detection; the "Stuck" status has been removed entirely and replaced with clear "Complete" / "Finalising" states scoped only to import/rescan pipelines that have genuine deferred post-processing tasks

### Removed
- **Settings: Bulk Rename Section** — removed the duplicate bulk rename UI from the Settings page; this functionality is better placed in the Review Queue where it already exists with single and batch actions

## [1.7.0] - 2026-04-06

### Added
- **Review Queue: Redownload Action** — individual and bulk "Redownload" buttons for review items with audio normalization failures, with confirmation warning about source link accuracy
- **New Videos: Dynamic Discovery** — all category generators now use yt-dlp YouTube search as a fallback when hardcoded seed entries are exhausted, giving access to YouTube's full catalogue; implemented New and Rising categories for trending and recently-released music video discovery

### Fixed
- **Review Queue: False Duplicate Groups** — items flagged because a duplicate import was skipped (the incoming file was rejected) no longer appear as "Duplicate Group — 1 items"; they are correctly categorised as Library Import Alerts instead
- **Scraper: Self-Titled Album Search** — `search_wikipedia_album` no longer penalises disambiguation pages when the album is self-titled (e.g. Weezer's *Weezer (Teal Album)*); similarity scoring now compares against disambiguation text for self-titled albums instead of the bare artist name
- **Scraper: AI Album Fallback in Cross-Fallback** — when the Wikipedia album search returns the artist page instead of the album page (common for self-titled albums), the cross-fallback path now retries with the AI-provided album name before giving up
- **Scraper: Two-Tier Cross-Link Validation** — cross-link artist validation now checks both the parsed artist name and the infobox artist field, reducing false rejections for compilations and featured-artist tracks
- **Scraper: MB→Wikidata→Wikipedia Artist Fallback** — when direct Wikipedia search fails for an artist, the scraper now falls back to MusicBrainz → Wikidata → Wikipedia URL resolution
- **Scraper: Tracklist Remixer Validation** — tracklist-based Wikipedia search now validates that remixer credits in parenthetical suffixes match the expected artist before accepting a track URL
- **Scraper: Slash-Delimited Track Splitting** — tracklist parser now correctly handles slash-delimited track titles (e.g. "Track A / Track B") without splitting on the slash
- **Scraper: EP-as-Album Type Discard** — Wikipedia album search no longer accepts EP-type releases when searching for a full album

## [1.6.0] - 2026-04-05

### Added
- **Update Checker** — app now checks GitHub for new releases at startup and displays a dismissible banner when an update is available, with a direct link to the release page

### Fixed
- **Batch Job Timeout** — replaced fixed 1-hour wall-clock deadline with a 30-minute inactivity timeout; large batch imports (1000+ videos) no longer time out while sub-jobs are still completing successfully

## [1.5.0] - 2026-04-04

### Fixed
- **Video Player: Native Fullscreen** — fullscreen playback modes (theatre and video-only) now use the native browser Fullscreen API (`requestFullscreen` / `exitFullscreen`) instead of CSS-only viewport fill, restoring true OS-level fullscreen that hides the taskbar and browser chrome. Exiting native fullscreen via Escape correctly syncs the playback store back to normal mode

## [1.4.1] - 2026-04-04

### Fixed
- **Video Player: Duration Display** — replaced native browser video controls with custom controls that use the stored `duration_seconds` from the database, fixing the bug where track length rendered incorrectly and adjusted as the video progressed
- **Duration Backfill** — added a one-shot startup task that populates `duration_seconds` via ffprobe for any existing tracks missing the value; subsequent startups skip it automatically

## [1.4.0] - 2026-04-04

### Fixed
- **Queue: 200-Job Cap** — backend API hard-capped job list at 200 items; large imports (1200+) showed no progress, no completed jobs, and maxed at 200 active. Raised limit to 10,000 and added server-side `offset` parameter for pagination
- **Album Artwork: Single Art Mislabeled as Album Art** — `search_album_musicbrainz()` accepted Single-type releases when the album name matched the single name (e.g. self-titled "Hero"), returning the single's CoverArtArchive art as album art. Now filters out Single-type releases while preserving EPs as valid album types
- **Album Artwork: Wikipedia Album Art Ignored** — pipeline-discovered Wikipedia album art (`source="wikipedia_album"`) was missing from `ALBUM_PRIORITY`, giving it priority 999 and always losing to any other source. Added to priority list

## [1.2.0] - 2026-04-04

### Added
- **Open Folder Buttons** — "Open in file explorer" buttons added to Default Directory, Source Directories, Archive Directory settings, and the Log Viewer toolbar
- **Archive Manifest System** — when a video is archived after editing, a `.playarr-archive.json` manifest is written alongside it containing MD5 checksum, original path, video ID, artist/title, and archive timestamp
- **Manifest-Based Archive Re-Linking** — `find_archive_file()` now performs a manifest-based search as a fallback, enabling archive re-linking even when the archive directory changes or files are reorganised
- **Generic Open Directory API** — new `POST /api/settings/open-directory` endpoint for opening any directory in the OS file manager
- **Log Directory API** — new `GET /api/jobs/logs/directory` endpoint returning the absolute log directory path

### Fixed
- **URL Pipeline Naming Convention** — videos downloaded via URL pipeline now obey the configured folder structure and file naming pattern settings (previously ignored, using hardcoded `Artist - Title [Quality]` flat structure)
- **Post-AI File Re-Organization** — `_re_organize_file()` in all three pipelines now uses `build_library_subpath()` with the actual library_dir setting, correctly producing nested folder structures (e.g. `Artist/VideoFolder`) instead of computing from `os.path.dirname(old_folder)`
- **Empty Parent Cleanup** — after re-organizing a file to a new nested path, empty parent directories left behind are cleaned up
- **Archive Restore Cleanup** — restoring from archive now removes the manifest file and empty archive subfolders

### Changed
- **pipeline_url/services/file_organizer.py** — replaced with a thin re-export shim delegating to `app.services.file_organizer`, eliminating code drift between pipelines

## [1.1.0] - 2026-04-04

### Added
- **Log Viewer** — new "Logs" tab in Settings with full log viewing, search/filter, syntax highlighting, download, and selection export
- **Clean Library: Redundant File Detection** — health check now detects mismatched/orphaned sidecar files (XML, NFO, posters, thumbnails) with one-click cleanup
- **New Videos: Per-Category Counts** — each discovery category can now have its own result count setting
- **New Videos: Expanded Seeds** — significantly expanded FAMOUS_SEEDS and POPULAR_SEEDS across all genre categories; removed stub categories
- **Library Scan: Poster Disk Discovery** — scan now discovers poster artwork from disk when not present in XML sidecars
- **Rescan from Disk** — added to bulk actions modal for batch re-scanning from existing files
- **Archive Folder Exclusion** — archive folders are now excluded from library scans
- **Star Ratings & Archive Restore** — star ratings preserved through pipeline; archive restore functionality

### Fixed
- **XML Sidecar Selection** — `find_playarr_xml()` now prefers XML matching the video file stem when multiple XMLs exist in a folder
- **NFO-Only Tracks** — library scan now restores poster artwork for tracks with only NFO files (no XML)
- **Entity Re-Linking on Scan** — scan now correctly re-links artist/album/genre entities from XML sidecar data
- **Tile Swap Rate** — Now Playing background grid tile swapping now correctly batches swaps to achieve the configured tiles-per-interval rate (previously clamped to ~5/sec)
- **CMD Popup Suppression** — all subprocess calls (ffmpeg, ffprobe, yt-dlp) use CREATE_NO_WINDOW to prevent console flashes
- **XML Export/Import Parity** — complete field coverage between export and re-import ensuring no metadata loss on rescan-from-disk
- **Entity Resolution Imports** — corrected import paths for entity resolution during rescan-from-disk operations
- **Rescan Finalization** — fixed stuck "Finalizing" state during rescan operations
- **New Videos Repopulation** — fixed suggestions not repopulating after downloads
- **Directory Management** — improved runtime directory creation and validation

### Changed
- Version bumped to 1.1.0

## [1.0.0] - 2026-03-15

Initial public release.
