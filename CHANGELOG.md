# Changelog

## [1.9.14] - 2026-04-12

### Added
- **Audio Download** — new Music icon button on the video detail page extracts audio as a CBR MP3 file; FFmpeg detects source bitrate and channel count, snaps to nearest standard bitrate (64–320 kbps), and streams the result with a busy spinner; ID3 tags include artist, title, album, year, genre, poster artwork (APIC), and Windows Media Player–compliant POPM star rating via mutagen
- **Live Search on Facet Pages** — typing in the global search bar now live-filters the current facet page (Artists, Albums, Years, Genres, Ratings, Quality) with 250 ms debounce; the query syncs to the URL as `?search=` so results persist on refresh; a clear (×) button resets the filter; on non-facet routes, Enter still navigates to the Library page
- **New Videos: Preference-Based Recommendations** — "Songs You Might Like" now uses a multi-signal scoring engine: 5-star artists (weight 1.0), 4-star (0.6), 3-star (0.3 if fewer than 8 artists from higher tiers), plus a play-count engagement bonus from PlaybackHistory (0.05 per play, capped at 0.5); a new Phase 3 discovers videos in the user's top 3 genres via yt-dlp search
- **New Videos: Personalized Sections First** — "Songs You Might Like" and "Recommended By Artist" are now the first two sections on the New Videos page, ahead of Famous/Popular/New/Rising

### Fixed
- **URL Import Fails During Finalising** — adding a video by URL while other imports were in the Finalising stage could silently fail (spinner would spin then revert, URL stayed in the input field); root cause was the import endpoint's `db.commit()` contending with the write queue's `_apply_lock` at the SQLite level until `busy_timeout` (30 s) expired; job-creation commits in `import_by_url`, `_import_playlist`, `redownload_video`, and `rescan_metadata` now acquire `_apply_lock` before committing, serializing correctly with pipeline writes
- **New Videos: Junk Filtering** — videos longer than 15 minutes are now hard-blocked (trust score = 0.0) instead of receiving a trivial −0.10 penalty; videos 8–15 minutes receive a −0.25 penalty; new hard-block title patterns for "N hours of", "full album", "nonstop", and "megamix" compilations; "compilation" keyword penalty increased from −0.10 to −0.20
- **New Videos: Sparse Suggestion Lists** — each category now displays up to 20 suggestions (was 12); the "Recommended By Artist" section lowered its minimum-owned threshold from 2 to 1 video so it works with smaller libraries, and searches up to 8 artists (was 5); the taste engine searches up to 6 artists (was 3) with a generation limit of 20 (was 10)
- **Scan Metadata: Unicode Hyphen False Identity Change** — AI Source Resolution returning artist names with Unicode hyphens (en-dash U+2013, etc.) and AI Final Review normalising to ASCII hyphens was falsely detected as an artist identity change, triggering invalidation of all MusicBrainz IDs, IMDB URL, and Wikipedia sources; identity change set comparison now normalises Unicode hyphens to ASCII before comparing, matching the normalisation already used in search functions
- **Schema Upgrade: Missing crop_position Column** — `crop_position` on `media_assets` and `cached_assets` had an Alembic migration (017) but was not included in `_apply_schema_upgrades()`, causing silent failures on existing databases upgraded in-place by the bundled installer
- **Now Playing: Muted Background Stream** — MKV files used for the muted background artwork grid were being served with full audio tracks, wasting bandwidth; new `/stream-video-only` endpoint remuxes MKV to fragmented MP4 with audio stripped for muted playback contexts

## [1.9.13] - 2026-04-11

### Added
- **Artwork Manager** — new tab in Metadata Manager with pie chart breakdowns of poster art sources (source art vs thumbnail fallback), artist/album coverage stats, searchable entity browser with pagination, upload/delete/refresh artwork for artists, albums, and video posters, artwork crop position adjustment via focal point selector lightbox, and entity sources editor for MusicBrainz IDs and Wikipedia URLs
- **Artwork Crop Position** — clickable focal point selector on artwork lightbox sets CSS `object-position` for artist, album, and video poster art; persisted to `crop_position` column on `media_assets` and `cached_assets`
- **Review Queue: Artwork Categories** — two new review categories (`artwork_incomplete`, `missing_artwork`) with "Scan Artwork" button, filter pills, and "Scan Sources" bulk action to repair missing entity artwork
- **Safe Delete (Recycle Bin)** — deleted files are now sent to the OS recycle bin instead of permanent deletion; network/UNC paths where recycle bin is unavailable raise a confirmation prompt before falling back to permanent delete
- **Queue: History Sorting** — completed jobs in the Queue history tab can now be sorted by Date Added, Date Completed, Artist, or Title with ascending/descending toggle; sort preference persisted to localStorage
- **Party Mode: Pre-Rendered Fireworks** — fireworks celebration animation is pre-rendered to a WebM blob via `captureStream` + `MediaRecorder` on settings change, then played back as a video element for zero-CPU animation playback
- **Library Scan: Update Existing Mode** — new scan mode that re-reads sidecar XMLs for already-tracked items and syncs changed fields (metadata, ratings, quality/letterbox, sources, artwork, entity refs, processing state) back into the DB; designed for multi-install setups sharing the same library
- **Library Scan: Mode Selector** — Scan Library in Settings now has radio options (Import New / Update Existing / Both) matching the Export Library UI pattern
- **Startup: Zombie Record Cleanup** — on launch, DB records whose video files no longer exist on disk are automatically detected and removed, along with their child rows, cached assets, thumbnails, and orphaned entity folders
- **User Edit Provenance** — anonymous instance user ID (auto-generated UUID) silently tracks who made each edit; `field_provenance_users` JSON on VideoItem and entity models maps each field to the user who last set it; `last_edited_by` on VideoItem, `user_id` on MetadataSnapshot, and `user_id` in review history entries enable future per-user trust scoring for the musicvideo DB
- **Queue: Skipped Job Art Cards** — skipped duplicate jobs now show an art card with the matched library video's poster, parsed title, and a link to the existing entry instead of a plain text line

### Fixed
- **Video Editor: Scan/Scan All Merged** — consolidated two separate scan buttons into a single button with a popup dialog offering Scan (selected) and Scan All options
- **Video Editor: Scan Progress Bar** — fixed missing per-file progress; now shows "Scanning 47/854: Artist — Title" with percentage
- **Video Editor: Sidecar XML Persistence** — letterbox scan results are now written to `.playarr.xml` sidecar after scanning
- **Video Editor: Post-Encode Cleanup** — letterbox fields are cleared and sidecar XML updated after encoding completes
- **Video Editor: Manual Tag + Filtering** — manually-added tracks show a blue "Manual" badge; new dropdown filter for All/Letterboxed/Manual
- **Video Editor: Skip Already-Processed Filter** — new scan options to skip videos that have already been cropped or trimmed
- **Sidebar: Review Count Not Updating** — the Review badge count only fetched once on mount; added 15-second auto-polling to match the Queue badge behaviour
- **Duplicate Check: Zombie Records Blocking Imports** — duplicate detection now ignores DB records whose files are missing from disk, preventing ghost entries from blocking re-imports
- **Duplicate Skip: Job Not Linked to Match** — skipped duplicate jobs now link `video_id` to the existing matched video so the UI can show poster art and a direct link
- **Review Queue: Stale AI/Scene Flags Not Clearing** — scene analysis and AI enrichment deferred tasks in all three pipelines now call `clear_stale_enrichment_review()` after completing, so review items auto-clear without requiring a manual refresh
- **Review Queue: Removed Unused Import Error Category** — the `import_error` review category filter pill was removed since no code path generates this category
- **Delete Error Feedback** — delete operations across Library, Artists, Albums, Years, Queue, and ActionsPanel now show error toasts instead of failing silently
- **Playback: Video Source Cleanup on Unmount** — VideoPlayer and NowPlayingPage now release the video `src` attribute on unmount/track-change, causing the browser to drop the HTTP connection and terminate the backend FFmpeg streaming process
- **XML Sidecar: Canonical Tracking Parity** — `canonical_provenance` and `canonical_confidence` are now written at the identity level (not nested in `<version>`) and round-trip correctly through export/import; `editor_edit_type` also persisted
- **Settings: Export Mode Tooltips** — library export radio options now have tooltip descriptions matching the scan mode selector

## [1.9.12] - 2026-04-10

### Fixed
- **Playback: Orphaned FFmpeg Streams** — stream generator `finally` blocks used `process.wait()` which waited for FFmpeg to finish naturally; rapid track changes accumulated zombie processes; changed to `process.kill()` for immediate cleanup on client disconnect
- **Playback: Background Animation Jitter** — artwork grid swapped 36-72 images with synchronous decoding on the main thread; added `decoding="async"` to all grid images and paused swap work when the tab is hidden
- **Playback: Artwork DB Query Overhead** — every poster/artwork/thumb request ran a fresh DB query even on cache hits; added in-memory artwork cache with 120s TTL to eliminate repeated queries from the background animation grid
- **Playback: Stale Overlay Metadata** — track change did not cancel in-flight metadata fetch requests; added abort guard to prevent stale API responses from updating state
- **Video Editor: Encode Jobs Stalling on Cancel** — `_run_encode_job` never checked `is_cancelled()`, so FFmpeg encoding continued after cancel; added cancellation check in progress callback and `process.kill()` on callback error
- **Video Editor: Retry Not Working** — `retry_job()` had no handler for `video_editor_encode` job type; added handler that reads params from `job.input_params` and spawns a new encode thread
- **Video Editor: Persistent Encoding Bar** — frontend only cleared encode state on "complete" or "failed", not "cancelled"; added cancelled status handling and stopped polling on terminal statuses
- **Video Editor: Missing Progress Logs** — `log_text` was only written on successful encode; now written for failed and cancelled jobs too
- **Video Editor: DB Write Throttle** — FFmpeg progress callback wrote to DB on every output line (~10/sec); throttled to 1-second intervals

### Added
- **Playback: Kill Streams Endpoint** — new `POST /api/playback/kill-streams` endpoint that terminates all active FFmpeg streaming processes; called by the frontend on track change to proactively kill orphaned processes
- **Startup: Zombie FFmpeg Cleanup** — on Windows, `taskkill /F /IM ffmpeg.exe` runs at startup and during installer setup/uninstall to clear any orphaned processes from a previous crash

## [1.9.11] - 2026-04-09

### Added
- **Genre Consolidation: Autofill Search** — typing in a genre consolidation tile input now shows autocomplete suggestions matching existing genres, with video counts and already-consolidated indicators; powered by new `/genre-search` endpoint with debounced (200ms) queries
- **Genre Consolidation: Add/Remove Genres from Tiles** — each active consolidation tile has a `+` button to add genres via autofill search and per-alias "Remove" buttons to unconsolidate individual genres
- **Genre Consolidation: Create New Tiles** — "New Tile" button with inline name input to create empty consolidation tiles from the Genre Consolidation tab
- **Genre Consolidation: Tile Blacklist/Whitelist** — eye toggle on each tile to blacklist or whitelist the entire tile (master + all aliases) in one operation; blacklisted tiles remain visible with reduced opacity, red background, and "Blacklisted" badge
- **Genre Manager: Consolidated Genre Display** — alias genres are hidden from the Genre Manager; master genres with aliases show a Layers icon with alias count badge

### Fixed
- **Genre Autofill: Manual Edit Input** — the manual edit input for suggested consolidations was a plain text field with no autocomplete; replaced with the autofill component in controlled mode
- **Genre Autofill: Dropdown Readability** — autofill dropdown had a semi-transparent background causing text overlap with content behind it; changed to solid opaque background

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
