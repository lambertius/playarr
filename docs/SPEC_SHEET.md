# Playarr — Complete Specification Sheet

> **Purpose:** This document is a self-contained specification for rebuilding Playarr from scratch. It describes every feature, endpoint, data model, UI screen, pipeline stage, and integration in enough detail that an AI or developer could recreate the entire system.

---

## 1. Product Overview

**Playarr** is a self-hosted music video manager inspired by Sonarr/Radarr/Lidarr. It downloads music videos from YouTube/Vimeo, resolves metadata from multiple providers (MusicBrainz, Wikipedia, IMDB, TMVDB, AI), normalises audio loudness, organises files on disk, writes Kodi-compatible NFO files, and serves everything through a dark-themed web UI with browser-native playback and hover previews.

**Core value proposition:** One-click URL import → fully tagged, normalised, organised music video with artwork, metadata, entity linking, and Kodi export.

---

## 2. Technology Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| **API** | FastAPI + Uvicorn | FastAPI ≥ 0.104 | REST endpoints, video streaming, CORS, OpenAPI docs |
| **ORM** | SQLAlchemy 2.0 + Alembic | SQLAlchemy ≥ 2.0.23 | Typed ORM, migrations |
| **Database** | SQLite (dev) / PostgreSQL (prod) | — | Data persistence, WAL mode for concurrency |
| **Task Queue** | Celery + Redis | Celery ≥ 5.3.6 | Background pipeline execution |
| **Downloader** | yt-dlp | ≥ 2024.1.0 | YouTube/Vimeo download with format selection |
| **Media** | ffmpeg + ffprobe | System | Audio normalisation, quality analysis, remuxing, preview generation |
| **Metadata** | MusicBrainz API (musicbrainzngs), BeautifulSoup4 | — | Artist/title/album/year/genre resolution |
| **AI** | OpenAI, Google Gemini, Anthropic Claude, Ollama | Optional | Metadata enrichment, scene analysis, fingerprinting |
| **Frontend** | React 19, Vite 7, TypeScript 5.9 | — | Single-page application |
| **Styling** | Tailwind CSS 4.2, lucide-react icons | — | Utility-first dark theme |
| **State** | @tanstack/react-query 5, Zustand 5 | — | Server state + client state |
| **HTTP** | Axios, httpx | — | Frontend/backend HTTP clients |
| **System Tray** | pystray + Pillow | Optional | Windows system tray icon |

---

## 3. Architecture

```
┌────────────────┐   HTTP/REST    ┌─────────────────┐    Celery/Redis   ┌──────────────┐
│   React SPA    │ ─────────────▸ │  FastAPI Server  │ ───────────────▸ │ Celery Worker │
│   (Vite)       │ ◂───────────── │  (Uvicorn)       │ ◂─────────────── │              │
└────────────────┘  JSON + Stream └────────┬────────┘   Task results    └──────┬───────┘
                                           │                                    │
                                   ┌───────▼────────┐               ┌──────────▼──────────┐
                                   │  SQLite / PG    │               │  yt-dlp · ffmpeg     │
                                   │  (SQLAlchemy)   │               │  ffprobe · httpx     │
                                   └─────────────────┘               │  musicbrainzngs      │
                                                                     └──────────────────────┘
```

### Component Roles

- **API Server** (port 6969): All REST endpoints, video streaming with Range header support, SPA static file serving, CORS middleware.
- **Celery Worker**: Executes the import/rescan/normalise pipeline in background threads. In-process fallback mode available when Redis is unavailable (thread-based).
- **Database**: Single SQLite file in dev (`playarr.db`, WAL mode). Dual engine pattern — main engine (pool_size=20, busy_timeout=30s) and cosmetic engine (pool_size=10, busy_timeout=15s) for non-critical writes. Foreign keys enforced.
- **Redis**: Celery message broker and optional cache. Port 6379.

### Deployment Options

1. **Development (Windows):** `python backend/_start_server.py` — auto-restart wrapper with system tray icon.
2. **Docker Compose:** 4 services — redis, api, worker, frontend (nginx). Multi-stage Dockerfile: Python 3.12-slim → Node 20-alpine → Nginx alpine.

---

## 4. Database Schema

### 4.1 Core Tables

#### `video_items` — Central entity (one row per music video)

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `id` | Integer | PK, autoincrement | — |
| `artist` | String(500) | — | Primary artist name |
| `title` | String(500) | — | Track/video title |
| `album` | String(500) | nullable | Album name |
| `year` | Integer | nullable | Release year |
| `plot` | Text | nullable | Description/synopsis |
| `mb_artist_id` | String(50) | nullable | MusicBrainz artist MBID |
| `mb_recording_id` | String(50) | nullable | MusicBrainz recording MBID |
| `mb_release_id` | String(50) | nullable | MusicBrainz release MBID |
| `mb_release_group_id` | String(50) | nullable | MusicBrainz release group MBID |
| `artist_entity_id` | Integer | FK → artists.id | Canonical artist link |
| `album_entity_id` | Integer | FK → albums.id | Canonical album link |
| `track_id` | Integer | FK → tracks.id | Canonical track link |
| `version_type` | String(20) | default "normal" | normal, cover, live, alternate, uncensored, 18+ |
| `alternate_version_label` | String(200) | nullable | e.g. "acoustic", "remix" |
| `original_artist` | String(500) | nullable | For covers: the original performer |
| `original_title` | String(500) | nullable | For covers: the original song title |
| `related_versions` | JSON | nullable | List of sibling video IDs |
| `review_status` | String(30) | default "none" | none, needs_human_review, needs_ai_review, reviewed |
| `review_reason` | String(500) | nullable | Why review is needed |
| `review_category` | String(40) | nullable | version_detection, duplicate, import_error, url_import_error, manual_review |
| `folder_path` | String(1000) | — | Absolute path to video folder |
| `file_path` | String(1000) | — | Absolute path to video file |
| `file_size_bytes` | BigInteger | nullable | File size |
| `resolution_label` | String(20) | nullable | e.g. "1080p", "4K" |
| `song_rating` | Integer | default 3 | 1-5 star song rating |
| `video_rating` | Integer | default 3 | 1-5 star video quality rating |
| `song_rating_set` | Boolean | default false | Whether user explicitly set song rating |
| `video_rating_set` | Boolean | default false | Whether user explicitly set video rating |
| `locked_fields` | JSON | nullable | List of field names protected from rescan overwrite |
| `processing_state` | JSON | nullable | Dict tracking pipeline step completion |
| `import_method` | String(20) | nullable | "url", "import", "scanned" |
| `audio_fingerprint` | Text | nullable | Chromaprint fingerprint |
| `acoustid_id` | String(50) | nullable | AcoustID identifier |
| `field_provenance` | JSON | nullable | Maps each field to its data source |
| `exclude_from_editor_scan` | Boolean | default false | Suppress video editor letterbox scan |
| `created_at` | DateTime | default now | — |
| `updated_at` | DateTime | default now, onupdate now | — |

**Relationships:** sources (1→many), quality_signature (1→1), metadata_snapshots (1→many), media_assets (1→many), genres (many→many via video_genres), processing_jobs (1→many), normalization_history (1→many), playback_history (1→many)

**Processing State Fields:** `metadata_scraped`, `metadata_ai_analyzed`, `track_identified`, `scenes_analyzed`, `audio_normalized`, `description_generated`, `filename_checked`, `canonical_linked`

#### `sources` — Provider URLs for a video

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `id` | Integer | PK | — |
| `video_id` | Integer | FK → video_items.id | Parent video |
| `provider` | Enum | — | youtube, vimeo, wikipedia, imdb, musicbrainz, tmvdb, other |
| `source_video_id` | String(200) | — | Platform-specific ID |
| `original_url` | String(2000) | — | URL as submitted |
| `canonical_url` | String(2000) | nullable | Normalised URL |
| `channel_name` | String(500) | nullable | YouTube channel name |
| `platform_title` | String(1000) | nullable | Title from platform |
| `platform_description` | Text | nullable | Description from platform |
| `platform_tags` | JSON | nullable | Tags from platform |
| `upload_date` | String(20) | nullable | YYYYMMDD from yt-dlp |
| `source_type` | String(20) | — | video, artist, album, single, recording |
| `provenance` | String(50) | nullable | import, ai, scraped, manual |
| `created_at` | DateTime | default now | — |

**Unique constraint:** (`video_id`, `provider`, `source_video_id`)

#### `quality_signatures` — Media analysis results

| Column | Type | Purpose |
|--------|------|---------|
| `id` | Integer, PK | — |
| `video_id` | Integer, FK (unique) | One per video |
| `width`, `height` | Integer | Resolution |
| `fps` | Float | Frame rate |
| `video_codec` | String | e.g. h264, vp9 |
| `video_bitrate` | Integer | Bits per second |
| `audio_codec` | String | e.g. aac, opus |
| `audio_bitrate` | Integer | Bits per second |
| `audio_sample_rate` | Integer | e.g. 48000 |
| `audio_channels` | Integer | e.g. 2 |
| `container` | String | mkv, mp4, webm |
| `duration_seconds` | Float | — |
| `hdr` | Boolean | HDR flag |
| `loudness_lufs` | Float | Integrated loudness measurement |

**Method:** `quality_score()` → comparable integer (height×1000 + bitrate/1000 + fps bonus + HDR bonus)

#### `media_assets` — Artwork and thumbnails

| Column | Type | Purpose |
|--------|------|---------|
| `id` | Integer, PK | — |
| `video_id` | Integer, FK | Parent video |
| `asset_type` | String | poster, thumb, fanart, album_thumb, preview |
| `file_path` | String | Local path |
| `source_url` | String | Original download URL |
| `resolved_url` | String | After redirects |
| `provenance` | String | wikipedia, musicbrainz, coverartarchive, youtube_thumb, custom |
| `source_provider` | String | — |
| `content_type` | String | MIME type |
| `file_hash` | String | SHA-256 |
| `width`, `height` | Integer | Dimensions |
| `file_size_bytes` | Integer | — |
| `status` | String | valid, invalid, missing, pending |
| `validation_error` | String | nullable |
| `last_validated_at` | DateTime | — |
| `created_at` | DateTime | — |

#### `processing_jobs` — Background task tracking

| Column | Type | Purpose |
|--------|------|---------|
| `id` | Integer, PK | — |
| `video_id` | Integer, FK (nullable, SET NULL) | Associated video |
| `celery_task_id` | String | Celery ID |
| `job_type` | String | import_url, rescan, normalize, library_scan, library_export, playlist_import |
| `status` | Enum | queued, downloading, downloaded, remuxing, analyzing, normalizing, tagging, writing_nfo, asset_fetch, complete, failed, cancelled, skipped |
| `display_name` | String(500) | Human-readable label |
| `action_label` | String(200) | Short action description |
| `input_url` | String(2000) | Import URL |
| `input_params` | JSON | Configuration params |
| `pipeline_steps` | JSON | List of {step, status} objects |
| `progress_percent` | Float | 0-100 progress |
| `current_step` | String | Current pipeline stage |
| `log_text` | Text | Append-only log |
| `error_message` | Text | Error details |
| `retry_count` | Integer | Current attempts |
| `max_retries` | Integer | Maximum attempts |
| `created_at`, `started_at`, `completed_at`, `updated_at` | DateTime | — |

#### `settings` — App configuration KV store

| Column | Type | Purpose |
|--------|------|---------|
| `id` | Integer, PK | — |
| `user_id` | String(200), nullable | None = global |
| `key` | String(200) | Setting name |
| `value` | Text | Serialised value |
| `value_type` | String(20) | string, int, float, bool, json |

**Unique constraint:** (`user_id`, `key`)

**Default settings (40+):** library_dir, library_source_dirs, archive_dir, normalization_target_lufs, normalization_lra, normalization_tp, preview_duration_sec, preview_start_percent, ai_provider, auto_normalize_on_import, preferred_container, transcode_audio_bitrate, server.port, ai_source_resolution, ai_final_review, import_scrape_wikipedia, import_scrape_musicbrainz, import_ai_auto, import_ai_only, import_find_source_video, max_concurrent_downloads, party_mode_exclusions, library_naming_pattern, library_folder_structure, tmvdb_enabled, tmvdb_api_key, tmvdb_auto_pull, tmvdb_auto_push, import_scrape_tmvdb

#### Other Core Tables

- **`genres`**: `id`, `name` (unique), `blacklisted` (bool). Many-to-many via `video_genres` join table.
- **`normalization_history`**: `id`, `video_id` FK, `target_lufs`, `measured_lufs_before`, `measured_lufs_after`, `gain_applied_db`, `created_at`.
- **`playback_history`**: `id`, `video_id` FK, `played_at`, `duration_watched_sec`, `user_id` (nullable).
- **`playlists`**: `id`, `name`, `description`, `created_at`, `updated_at`. Entries via `playlist_entries`.
- **`playlist_entries`**: `id`, `playlist_id` FK, `video_id` FK, `position`, `added_at`.

### 4.2 Entity Subsystem Tables

#### `artists` (ArtistEntity)

| Column | Type | Purpose |
|--------|------|---------|
| `id` | Integer, PK | — |
| `canonical_name` | String | Primary display name |
| `sort_name` | String | Sortable name |
| `mb_artist_id` | String | MusicBrainz MBID |
| `country` | String | Country code |
| `origin` | String | City/region of origin |
| `disambiguation` | String | MusicBrainz disambiguation |
| `biography` | Text | Artist bio |
| `artist_image` | String | Image URL or path |
| `field_provenance` | JSON | Source tracking |
| `created_at`, `updated_at` | DateTime | — |

**Relationships:** albums (1→many), tracks (1→many), genres (many→many)

#### `albums` (AlbumEntity)

| Column | Type | Purpose |
|--------|------|---------|
| `id` | Integer, PK | — |
| `title` | String | Album title |
| `artist_id` | Integer, FK → artists.id | Parent artist |
| `year` | Integer | Release year |
| `release_date` | String | ISO date |
| `mb_release_id` | String | MusicBrainz release MBID |
| `album_type` | String | album, single, EP, compilation |
| `cover_image` | String | Cover art path/URL |
| `field_provenance` | JSON | Source tracking |
| `created_at`, `updated_at` | DateTime | — |

**Relationships:** tracks (1→many), genres (many→many)

#### `tracks` (TrackEntity)

| Column | Type | Purpose |
|--------|------|---------|
| `id` | Integer, PK | — |
| `title` | String | Track title |
| `artist_id` | Integer, FK → artists.id | — |
| `album_id` | Integer, FK → albums.id | — |
| `track_number` | Integer | Position on album |
| `duration_seconds` | Float | — |
| `mb_recording_id` | String | MusicBrainz recording MBID |
| `mb_release_id` | String | — |
| `mb_artist_id` | String | — |
| `year` | Integer | — |
| `artwork_album`, `artwork_single` | String | Cover art paths |
| `canonical_verified` | Boolean | Verified by matching |
| `metadata_source` | String | Provider that sourced this |
| `ai_verified` | Boolean | AI-verified |
| `ai_verified_at` | DateTime | — |
| `is_cover` | Boolean | Whether it's a cover song |
| `original_artist`, `original_title` | String | Original song info |
| `field_provenance` | JSON | — |
| `created_at`, `updated_at` | DateTime | — |

**Relationships:** videos (1→many), genres (many→many)

#### `cached_assets` — Entity artwork cache

`id`, `entity_type`, `entity_id`, `asset_type`, `file_path`, `source_provider`, `resolved_url`, `status`, `file_hash`, `created_at`

#### `metadata_revisions` — Entity change history

`id`, `entity_type`, `entity_id`, `revision_data` (JSON), `reason`, `created_at`

### 4.3 Matching Subsystem Tables

- **`match_results`**: `video_id` (unique), `resolved_artist`, `artist_mbid`, `resolved_recording`, `recording_mbid`, `resolved_release`, `release_mbid`, `confidence_overall`, `confidence_breakdown` (JSON), `status`, `normalization_notes`, `is_user_pinned`
- **`match_candidates`**: `match_result_id` FK, `entity_type`, `candidate_mbid`, `canonical_name`, `provider`, `score`, `score_breakdown` (JSON), `is_selected`
- **`user_pinned_matches`**: User-forced resolution overrides

### 4.4 AI Subsystem Tables

- **`ai_metadata_results`**: `video_id`, `ai_model`, per-field confidences (artist, title, album, year, plot), `genres`, `ai_director`, `ai_studio`, `ai_actors`, `ai_tags`, `verification_status`, `mismatch_score`, `mismatch_signals`, `fingerprint_result`, `model_task`, `change_summary`, `dismissed_at`
- **`ai_scene_analyses`**: `video_id`, `ai_model`, `scenes` (JSON), thumbnail count
- **`ai_thumbnails`**: Thumbnail candidates for a video

### 4.5 New Videos / Recommendations Tables

- **`suggested_videos`**: `provider`, `provider_video_id`, `category`, `title`, `artist`, `url`, `thumbnail_url`, `rank`
- **`suggested_video_dismissals`**: Dismissed suggestions
- **`suggested_video_cart_items`**: Cart for batch import
- **`recommendation_snapshots`**, **`recommendation_feedback`**: Recommendation engine state

---

## 5. API Endpoints

### 5.1 Library (`/api/library`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | List videos — paginated, with search, filters (artist, album, album_entity_id, genre, year, year_from, year_to, version_type, review_status, enrichment, import_method, song_rating, video_rating), sort (artist, title, year, created_at, updated_at) |
| GET | `/artists` | Distinct artist names with video counts |
| GET | `/years` | Distinct years with counts |
| GET | `/genres` | Genre list with counts |
| GET | `/albums` | Album list with counts |
| GET | `/song-ratings` | Song rating distribution |
| GET | `/video-ratings` | Video rating distribution |
| GET | `/{id}` | Full video detail (all fields, sources, quality, assets, entities) |
| GET | `/{id}/snapshots` | Metadata version history |
| GET | `/{id}/nav` | Navigation context (prev/next/random IDs for current sort) |
| GET | `/{id}/orphans` | Detect orphaned folders for this video |
| POST | `/{id}/undo-rescan` | Restore metadata from a snapshot |
| POST | `/{id}/scrape` | Manual metadata scrape (with options: aiAutoAnalyse, aiOnly, scrapeWikipedia, wikipediaUrl, scrapeMusicbrainz, musicbrainzUrl, scrapeTmvdb, isCover, isLive, isAlternate, isUncensored, alternateVersionLabel, findSourceVideo, normalizeAudio) |
| PUT | `/{id}` | Update metadata fields manually |
| DELETE | `/{id}` | Delete video + all files |
| POST | `/batch-delete` | Delete multiple videos |
| POST | `/orphans/clean` | Clean all orphaned folders |
| POST | `/health` | Library health check (stale entries, missing files) |
| POST | `/clean-stale` | Remove stale entries |
| POST | `/{id}/rename` | Rename folder to match naming pattern |
| POST | `/bulk-rename/preview` | Preview what bulk rename would do |
| POST | `/bulk-rename/execute` | Execute bulk rename |
| POST | `/{id}/open-folder` | Open folder in OS file explorer |
| POST | `/{id}/sources` | Create a source record |
| PUT | `/{id}/sources/{source_id}` | Update source |
| DELETE | `/{id}/sources/{source_id}` | Delete source |
| GET | `/party-mode` | Random video with exclusion filters |

### 5.2 Jobs (`/api/jobs`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/import` | Submit URL for import (single or playlist) |
| POST | `/rescan/{id}` | Re-scrape metadata for one video |
| POST | `/rescan-batch` | Batch rescan (array of video IDs + options) |
| POST | `/redownload/{id}` | Re-download at specific format spec |
| GET | `/formats/{id}` | List available download formats (from yt-dlp) |
| POST | `/normalize` | Batch normalise (array of video IDs) |
| POST | `/library-scan` | Scan library directory for new untracked files |
| POST | `/library-export` | Export entire library (Kodi NFO) |
| GET | `/` | List jobs (filterable by status, job_type) |
| GET | `/{id}` | Job detail |
| GET | `/{id}/log` | Job log text |
| POST | `/{id}/retry` | Retry a failed job |
| POST | `/{id}/cancel` | Cancel a queued/running job |
| DELETE | `/history` | Delete job history |
| POST | `/batch/delete` | Batch delete jobs |
| GET | `/telemetry` | Live telemetry snapshot (all active jobs — speed, ETA, stall) |
| GET | `/{id}/telemetry` | Job-specific telemetry |

### 5.3 Playback (`/api/playback`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/stream/{video_id}` | Stream video (Range header support, auto-remux MKV→fMP4, audio transcode if needed) |
| GET | `/preview/{video_id}` | Hover preview clip (short looping video) |
| GET | `/poster/{video_id}` | Poster image (in-memory cache, 120s TTL) |
| GET | `/artwork/{video_id}` | Artist/album artwork (in-memory cache, 120s TTL) |
| GET | `/thumb/{video_id}` | Video thumbnail |
| GET | `/artwork-ids` | List of video IDs that have real artwork |
| GET | `/download-audio/{video_id}` | Extract audio as CBR MP3 with ID3 tags (artist, title, album, year, genre, artwork, POPM rating) |
| POST | `/history` | Record playback history entry |
| POST | `/kill-streams` | Kill all active FFmpeg streaming processes (called on track change) |

### 5.4 Settings (`/api/settings`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | List all settings (merged with defaults for missing keys) |
| PUT | `/` | Update or create a setting |
| DELETE | `/history` | Clear setting history |
| PUT | `/source-directories` | Save source directories + auto-import |
| GET | `/normalization-history/{id}` | LUFS normalisation history for a video |
| POST | `/restart` | Restart the server (exit code 75 → _start_server.py relaunches) |
| GET | `/browse-directories` | Open native OS directory picker |
| POST | `/naming-preview` | Preview file naming pattern with example data |
| GET | `/genre-blacklist` | List blacklisted genres |
| PUT | `/genre-blacklist` | Update genre blacklist |

### 5.5 Metadata / Entities (`/api/metadata`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/artists` | List artist entities |
| GET | `/artists/{id}` | Artist detail (with albums, tracks, video counts) |
| GET | `/albums` | List album entities |
| GET | `/albums/{id}` | Album detail (with tracks) |
| GET | `/albums/{album_id}/tracks` | Tracks in album |
| GET | `/tracks` | List track entities |
| GET | `/tracks/{id}` | Track detail (with linked videos) |
| POST | `/refresh/{video_id}` | Force entity refresh for one video |
| POST | `/refresh-all` | Refresh all entities |
| POST | `/refresh-missing` | Refresh low-confidence entities |
| POST | `/export` | Full Kodi export (all artist.nfo, album.nfo, video NFO) |
| POST | `/export/{video_id}` | Export single video + its entities |
| POST | `/undo/{entity_type}/{entity_id}` | Undo entity change (rollback) |
| GET | `/revisions/{entity_type}/{entity_id}` | Revision history for an entity |

### 5.6 AI (`/api/ai`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{video_id}/enrich` | Run AI metadata enrichment |
| GET | `/{video_id}/comparison` | Side-by-side scraped vs AI results |
| POST | `/{video_id}/apply` | Apply specific AI fields to video |
| POST | `/{video_id}/undo` | Undo AI enrichment |
| POST | `/{video_id}/fingerprint` | Run audio fingerprint identification (Chromaprint/AcoustID) |
| POST | `/{video_id}/scenes` | Run scene analysis |
| GET | `/{video_id}/scenes` | Get scene analysis results |
| POST | `/{video_id}/thumbnail` | Select thumbnail from candidates |
| GET | `/{video_id}/thumbnails` | List thumbnail candidates |
| GET | `/{video_id}/thumbnails/{id}/image` | Serve thumbnail image |
| GET | `/{video_id}/results` | All AI results for a video |
| GET | `/settings` | AI configuration |
| PUT | `/settings` | Update AI settings |
| POST | `/test` | Test AI provider connectivity |
| POST | `/batch/enrich` | Batch AI enrichment |
| POST | `/batch/scenes` | Batch scene analysis |
| GET | `/models` | Full model catalog (all providers) |
| GET | `/model-routing` | Model routing preview (which model for which task) |
| GET | `/model-availability` | Available models per tier (fast/standard/high) |

### 5.7 Artwork (`/api/artwork`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/status` | Overall artwork health summary |
| POST | `/validate/{video_id}` | Validate artwork for one video |
| POST | `/repair` | Repair all artwork (re-fetch option) |
| POST | `/repair/cached` | Repair cached entity assets only |
| POST | `/repair/media` | Repair media-level assets only |

### 5.8 Resolve / Matching (`/api/resolve`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/{video_id}` | Trigger metadata resolution/matching |
| GET | `/{video_id}` | Get last result + all candidates |
| POST | `/{video_id}/pin` | Pin user selection (force match) |
| POST | `/{video_id}/unpin` | Unpin |
| POST | `/{video_id}/apply` | Apply match without pinning |
| POST | `/batch` | Batch resolve |
| POST | `/{video_id}/undo` | Revert resolve |
| GET | `/{video_id}/normalization` | Normalization detail (input/output transforms) |

### 5.9 Review Queue (`/api/review`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | List items needing review (filterable) |
| POST | `/{video_id}/approve` | Approve review item |
| POST | `/{video_id}/dismiss` | Dismiss review item |
| POST | `/{video_id}/set-version` | Change version type (cover/live/etc.) |
| POST | `/batch/approve` | Batch approve |
| POST | `/batch/dismiss` | Batch dismiss |
| POST | `/batch/apply-rename` | Batch apply naming convention renames |
| POST | `/batch/delete` | Batch delete videos from review queue |
| POST | `/batch/scrape` | Batch scrape metadata for review items |
| POST | `/scan-enrichment` | Scan library for incomplete enrichment |
| POST | `/scan-renames` | Scan library for naming convention mismatches |

### 5.10 Search (`/api/search`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/artist` | Manual MusicBrainz artist search |
| GET | `/recording` | Manual MusicBrainz recording search |
| GET | `/release` | Manual MusicBrainz release search |

### 5.11 Playlists (`/api/playlists`)

Full CRUD for playlists and entries. Create, update, delete playlists. Add, remove, reorder entries. Each entry links a video at a position.

### 5.12 Library Import (`/api/library-import`)

Batch import from filesystem — scan directories, match files, create video entries with full metadata scraping pipeline.

### 5.13 Video Editor (`/api/video-editor`)

Letterbox detection (`/scan`), crop configuration. Identifies black bars and generates crop parameters.

### 5.14 Scraper Tester (`/api/scraper-test`)

Manual scraper testing UI — submit artist/title, run MusicBrainz + Wikipedia scraping, view detailed results with collapsible sections.

### 5.15 TMVDB (`/api/tmvdb`)

The Music Video Database integration — pull/push metadata, sync.

### 5.16 New Videos (`/api/new-videos`)

Discovery feed with categories (similar artists, new releases, trending). Cart system for collecting recommendations. Batch import from cart. Dismiss/feedback tracking.

### 5.17 System Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Health check (`{"status":"ok","service":"playarr","version":"1.0.0"}`) |
| GET | `/api/version` | Version info (`app_version`, `db_version`, `version_mismatch` flag) |
| GET | `/api/stats` | Quick stats (total videos, genres, active/failed jobs) |
| POST | `/api/export/kodi` | Kodi export trigger |

---

## 6. Import Pipeline

The core pipeline executes when a user imports a URL. It's a state machine tracked via `ProcessingJob.status`:

```
queued
  → downloading        yt-dlp downloads best video+audio, merges to MKV
  → downloaded
  → remuxing            (optional: container fix)
  → analyzing           ffprobe extracts quality signature
  → [Metadata Resolution]
      1. AI Source Resolution (pre-scrape) — identify canonical identity + source links
      2. MusicBrainz search (preference for AI-provided links)
      3. Wikipedia scraping (artist page, album page, track page)
      4. IMDB music video search (optional)
      5. TMVDB lookup (optional)
      6. Version detection (cover/live/alternate/uncensored)
      7. Validation (title similarity, artist match, duration check)
      8. AI Final Review (post-scrape verification + correction)
  → normalizing         Measure LUFS, apply gain to target (-14.0 LUFS default)
  → tagging             (reserved for future use)
  → [File Organisation]
      - Rename folder: {artist}/{video_folder}
      - Rename file: {artist} - {title} [{quality}].mkv
      - Archive old version if quality upgrade
  → writing_nfo         Generate Kodi-compatible XML
  → [Entity Resolution]
      Phase 1 (Network): Query providers for canonical entities
      Phase 2 (DB writes): Create/update ArtistEntity, AlbumEntity, TrackEntity
  → [Entity Enrichment]  Backfill genre, plot, artwork on entities
  → [Poster Upgrade]     Replace YouTube thumbnail with album cover (CAA > Wikipedia > YT)
  → [Kodi Export]        Write artist.nfo, album.nfo, video NFO
  → asset_fetch          Download artwork assets
  → [Preview Generation] Create 8-second hover preview clip
  → [Deferred Tasks]
      - AI Metadata Enrichment
      - Scene Analysis + thumbnail generation
      - Audio fingerprint identification
  → complete
```

**Error handling:** Any stage failure → `failed` status with error_message. Retry available (3 max attempts, 30s delay).

**Concurrency:** Write operations serialised via threading.Lock (`_pipeline_lock`). All I/O-heavy work (download, analyse, scrape, normalise) runs without lock. Prevents SQLite write contention.

**Rescan pipeline:** Same metadata resolution flow but skips download/normalise. Respects `locked_fields`.

---

## 7. Metadata Resolution

### 7.1 Sources

| Provider | Data Retrieved |
|----------|---------------|
| **MusicBrainz** | Artist MBID, recording MBID, release MBID, release group MBID, album, year, track number, duration |
| **Wikipedia** | Artist biography, album description, track description, artwork images, external links |
| **IMDB** | Music video page (director, studio, actors) |
| **TMVDB** | TheMovieVideoDatabase metadata |
| **CoverArtArchive** | Album/single cover art (highest priority artwork source) |
| **YouTube** | Thumbnail, channel name, upload date, description, tags |
| **AI** | Pre-scrape source identification, post-scrape verification, enrichment (plot, genres, year) |

### 7.2 Artwork Priority

1. CoverArtArchive (album cover or single cover)
2. Wikipedia (extracted from article)
3. YouTube thumbnail (default fallback)

### 7.3 Version Detection

Detected via title analysis, AI enrichment, and user input:
- **normal** — Standard official music video
- **cover** — Cover version by a different artist
- **live** — Live performance recording
- **alternate** — Alternate version (with label: acoustic, remix, etc.)
- **uncensored** — Uncensored/explicit version
- **18+** — Age-restricted content

---

## 8. AI System

### 8.1 Supported Providers

| Provider | Models | Connection |
|----------|--------|------------|
| **OpenAI** | gpt-5, gpt-5-mini, gpt-5-nano, gpt-4.1, gpt-4.1-mini, gpt-4.1-nano, o3-mini, o4-mini | API key |
| **Google Gemini** | gemini-2.0-flash, gemini-2.0-flash-lite, gemini-2.5-flash-preview, gemini-2.5-pro-preview | API key |
| **Anthropic Claude** | claude-haiku-3, claude-haiku-3.5, claude-sonnet-4, claude-opus-4 | API key |
| **Ollama (Local)** | Dynamic discovery from local server | Base URL |

### 8.2 Task-Based Model Routing

AI tasks are assigned to models by tier:
- **Fast tier** — Source resolution (pre-scrape), quick verification
- **Standard tier** — Metadata enrichment, final review
- **High tier** — Scene analysis, complex disambiguation

### 8.3 AI Capabilities

1. **Source Resolution (Pre-scrape):** Identify the canonical artist/title from video URL and platform metadata. Provide MusicBrainz/Wikipedia links for targeted scraping.
2. **Final Review (Post-scrape):** Verify scraped metadata. Detect mismatches between sources. Correct obvious errors.
3. **Metadata Enrichment:** Generate plot/description, suggest genres, verify year, fill gaps.
4. **Scene Analysis:** Detect scene boundaries in the video. Generate thumbnail candidates at scene transitions.
5. **Audio Fingerprint:** Chromaprint + AcoustID identification.
6. **Mismatch Detection:** Compare scraped vs AI metadata, flag discrepancies with confidence scores.

---

## 9. Matching / Resolution System

### 9.1 Overview

The matching system resolves scraped metadata against canonical databases (MusicBrainz) to establish authoritative identity for each video.

### 9.2 Scoring Signals

| Signal | Weight | Description |
|--------|--------|-------------|
| String similarity (artist) | High | Normalised Levenshtein/SequenceMatcher |
| String similarity (title) | High | — |
| String similarity (album) | Medium | — |
| Duration match | Medium | ±5% tolerance |
| Metadata completeness | Low | Bonus for having more fields |
| User pin | Override | Force-select a specific match |
| Hysteresis | Tiebreak | Prefer previous selection to avoid thrashing |

### 9.3 Review Queue

Videos are flagged for review when:
- Version detection is uncertain
- Duplicate detected
- Import metadata quality is low
- AI mismatch score exceeds threshold
- Enrichment incomplete (missing scene analysis or AI metadata)
- Audio normalization failed

Review actions: approve, dismiss, change version type.

**Auto-clearing:** When deferred tasks (scene analysis, AI enrichment, normalization) complete successfully and resolve the flagged issue, the review flag is automatically cleared. Only enrichment and normalization categories are auto-cleared; categories requiring human judgement (duplicates, renames, version detection) are never auto-cleared.

---

## 10. Frontend Pages & UI

### 10.1 Design System

- **Theme:** Dark mode only, rock/music-inspired
- **Surfaces:** `#0f1117` (base) → `#151923` (card) → `#1c2230` (elevated) → `#2b3245` (border)
- **Accent:** `#e11d2e` (rock red) with hover `#ff3b3b` and glow effects
- **Secondary:** `#ff6a00` (flame orange)
- **Text:** `#e5e7eb` (primary) → `#b8bcc7` (secondary) → `#8b93a7` (muted)
- **Status:** green `#22c55e`, warning `#f59e0b`, danger `#ef4444`
- **Font:** Inter (body), Roboto Mono (technical metadata)
- **Icons:** lucide-react (24×24 SVG)
- **Custom scrollbars:** Thin (7px), dark track, styled thumb
- **Selection colour:** Red tint (`rgba(225, 29, 46, 0.3)`)

### 10.2 Component Library (Custom CSS Utilities)

- `btn`, `btn-primary` (gradient red + glow), `btn-secondary` (outlined), `btn-ghost`, `btn-danger`, `btn-sm`, `btn-icon`
- `input-field` (red focus border + glow)
- `card` (rounded, bordered), `card-glow` (hover glow)
- `badge` variants: blue, green, yellow, red, purple, gray, orange

### 10.3 Routes & Pages

| Route | Page Component | Purpose |
|-------|---------------|---------|
| `/library` | LibraryPage | Main view — grid/list of videos with hover previews, inline player, faceted filters |
| `/artists` | ArtistsPage | Browse by artist (card grid) |
| `/years` | YearsPage | Browse by year |
| `/genres` | GenresPage | Browse by genre |
| `/albums` | AlbumsPage | Browse by album entity |
| `/ratings` | RatingsPage | Filter by song/video star rating |
| `/playlists` | PlaylistsPage | CRUD playlists, drag-reorder |
| `/video/:videoId` | VideoDetailPage | Full detail — metadata editor, sources, quality, artwork, AI, entity links, history |
| `/queue` | QueuePage | Job queue — active, completed, failed. Progress bars, logs, retry/cancel |
| `/review` | ReviewQueuePage | Items needing human review — filtering, batch approve/dismiss |
| `/review/:videoId` | MatchDetailPage | Matching candidates, confidence breakdown, pin/apply |
| `/settings` | SettingsPage | Tabbed settings (Library, Media, AI, Playback, System, TMVDB, Discovery) |
| `/library-import` | ImportLibraryPage | Batch filesystem import wizard |
| `/now-playing` | NowPlayingPage | Current playback + full player |
| `/video-editor` | VideoEditorPage | Letterbox detection, crop config |
| `/scraper-tester` | ScraperTesterPage | Manual scraper testing |
| `/new-videos` | NewVideosPage | Discovery feed, cart, batch import |

### 10.4 Key Components

**Layout:** Sidebar navigation with collapsible sections. Bottom player bar. Global search.

**VideoCard:** Grid item with poster, hover preview (8s loop), artist/title overlay, version badge, rating stars.

**VideoDetailPage sections:** Metadata editor form (with field locking), source editor modal, quality panel, file panel, artwork tiles, canonical track panel (linked versions), AI panel, playback history.

**QueuePage:** Real-time telemetry polling (speed, ETA). Pipeline step visualisation. Log viewer. Batch actions.

**SettingsPage tabs:**
- **Library:** Directory paths, naming pattern, folder structure, scan
- **Media:** Normalisation targets (LUFS, LRA, true peak), container preference, audio bitrate
- **AI:** Provider selection, API keys, model routing, source resolution toggle, final review toggle
- **Playback:** Preview duration, start position
- **System:** Version display (with mismatch warning), restart button
- **TMVDB:** API key, auto push/pull toggles
- **Discovery:** New videos feed configuration

### 10.5 State Management

- **@tanstack/react-query:** All server data (videos, jobs, settings, entities) — automatic caching, refetching, optimistic updates
- **Zustand stores:**
  - `playbackStore` — Current video, position, duration
  - `artworkSettingsStore` — Artwork cache preferences
  - `fireworksStore` — Party mode celebration state

---

## 11. File Organisation

### 11.1 Naming Pattern

Default: `{artist} - {title} [{quality}]`
Example: `Foo Fighters - Everlong [1080p].mkv`

### 11.2 Folder Structure

Default: `{artist}/{file_folder}`
Example: `Library/Foo Fighters/Foo Fighters - Everlong [1080p]/`

### 11.3 Kodi NFO Export

Generates Kodi-compatible XML files:
- `<artist>.nfo` — Artist metadata
- `<album>.nfo` — Album metadata
- `<video>.nfo` — Video metadata (title, artist, year, plot, genres, sources, ratings)

---

## 12. Audio Normalisation

- **Standard:** EBU R128 loudness normalisation
- **Target LUFS:** -14.0 (configurable)
- **Loudness Range (LRA):** 7.0 (configurable)
- **True Peak (TP):** -1.5 dBTP (configurable)
- **Process:** Measure integrated loudness → calculate gain → apply with ffmpeg two-pass
- **History:** Every normalisation logged with before/after LUFS and gain applied

---

## 13. Docker Configuration

### Dockerfile (Multi-stage)

```dockerfile
# Stage 1: Backend
FROM python:3.12-slim
RUN apt-get install -y ffmpeg curl
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/ .
EXPOSE 6969
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6969"]

# Stage 2: Frontend build
FROM node:20-alpine
COPY frontend/ .
RUN npm ci && npm run build

# Stage 3: Nginx
FROM nginx:alpine
COPY --from=frontend /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

### docker-compose.yml

```yaml
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  api:
    build: { context: ., target: backend }
    ports: ["6969:6969"]
    volumes: [library, archive, db data]
    depends_on: [redis]

  worker:
    build: { context: ., target: backend }
    command: celery -A app.worker worker --concurrency=2
    depends_on: [redis, api]

  frontend:
    build: { context: ., target: nginx }
    ports: ["3080:80"]
    depends_on: [api]
```

### Nginx

- SPA fallback (`try_files $uri $uri/ /index.html`)
- API proxy to backend with 300s timeout
- Streaming proxy with buffering disabled and Range header passthrough

---

## 14. Configuration (.env)

| Variable | Default | Purpose |
|----------|---------|---------|
| `LIBRARY_DIR` | `D:\MusicVideos\Library` | Main library directory |
| `ARCHIVE_DIR` | `D:\MusicVideos\Archive` | Archive directory |
| `DATABASE_URL` | `sqlite:///./playarr.db` | Database connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis broker URL |
| `AI_PROVIDER` | `none` | AI provider (openai, gemini, claude, local, none) |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `CLAUDE_API_KEY` | — | Anthropic Claude API key |
| `LOCAL_LLM_BASE_URL` | `http://localhost:11434/v1` | Ollama endpoint |
| `LOCAL_LLM_MODEL` | `llama3` | Ollama model |
| `NORMALIZATION_TARGET_LUFS` | `-14.0` | Audio target |
| `FFMPEG_PATH` | auto | ffmpeg path (auto-detect) |
| `FFPROBE_PATH` | auto | ffprobe path (auto-detect) |
| `YTDLP_PATH` | auto | yt-dlp path (auto-detect) |
| `PREVIEW_DURATION_SEC` | `8` | Hover preview length |
| `PREVIEW_START_PERCENT` | `30` | Preview start position |
| `STARTUP_REPAIR_MODE` | `light` | Artwork repair on startup (off/light/full) |
| `PORT` | `6969` | Server port |

---

## 15. Versioning System

- **Single source of truth:** `backend/app/version.py` → `APP_VERSION = "1.0.0"`
- **DB stamping:** On startup, writes `schema_version` to settings table. Compares with current `APP_VERSION`.
- **Mismatch detection:** If `db_version > app_version`, logs warning and exposes via `/api/version` endpoint.
- **Frontend display:** Settings > System tab shows current version with amber mismatch warning banner.
- **Upgrade path:** New version installs changes code only — DB and settings are inherited. Alembic handles schema migrations.

---

## 16. Critical Anti-Patterns (from FAILED_APPROACHES.md)

This project has a documented history of 100+ failed approaches. Key lessons:

1. **AI cannot be trusted to leave fields blank.** It will always try to fill album, year, etc. Code must override AI for empty fields.
2. **Single-label album patterns must strip enclosing quotes first.** AI wraps values in literal quote characters.
3. **YouTube matching needs multi-signal scoring** — title similarity alone cannot distinguish official from fan uploads. Must incorporate channel authority, official markers, and unofficial penalties.
4. **Wikipedia search must handle featuring credits** — extract primary artist from "Artist feat. Other" before searching.
5. **Source persistence must UPDATE existing records**, not just skip creation on re-import.
6. **Critical pipeline flags must be logged at the JOB level**, not just inside inner functions.
7. **SQLite write contention** requires careful lock scoping — all I/O outside the lock, only final DB writes inside.

---

## 17. Services Summary

| Service | Responsibility |
|---------|---------------|
| `downloader` | yt-dlp wrapper: format selection, progress callbacks, metadata extraction |
| `media_analyzer` | ffprobe: resolution, codec, bitrate, HDR, LUFS measurement |
| `normalizer` | ffmpeg LUFS normalisation: gain calculation, two-pass application |
| `file_organizer` | Folder rename, NFO writing, library scanning, folder parsing |
| `metadata_resolver` | MusicBrainz, Wikipedia, IMDB search + scraping |
| `artwork_manager` | Asset download, poster selection, CoverArtArchive |
| `artwork_service` | Asset validation, repair, health checks |
| `preview_generator` | Generate hover preview clips |
| `canonical_track` | Canonical track linking, entity resolution |
| `duplicate_detection` | Fingerprint matching, duplicate detection |
| `filename_parser` | Extract metadata from folder/filename |
| `nfo_parser` | Parse existing Kodi NFO files |
| `playarr_xml` | NFO XML generation |
| `source_validation` | Validate scraped metadata, sanitisation |
| `telemetry` | Real-time telemetry store (download speed, progress) |
| `unified_metadata` | Single code path for all metadata resolution |
| `url_utils` | URL parsing, provider detection |
| `video_editor` | Letterbox detection, cropping |
| `youtube_matcher` | YouTube source matching scoring |
| `artist_album_scraper` | Artist/album metadata scraping |
| `library_export` | Export utilities (TMVDB, Kodi) |
| `retry_policy` | Retry logic with exponential backoff |
| `ai_summary` | AI plot/description generation |
