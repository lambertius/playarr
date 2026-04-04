# Changelog

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
