# Changelog

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
