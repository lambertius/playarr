# Changelog

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
