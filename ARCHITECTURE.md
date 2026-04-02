# Playarr — Architecture & Build Plan

## 1. System Overview

Playarr is a self-hosted music-video manager in the spirit of Sonarr / Radarr / Lidarr.
It downloads music videos from YouTube/Vimeo, auto-resolves metadata (MusicBrainz + Wikipedia),
normalizes loudness, organises files on disk, writes Kodi-compatible NFO files, and serves
everything through a dark-themed web UI with browser-native playback and hover previews.

```
┌────────────┐   HTTP/REST    ┌─────────────────┐    Celery/Redis   ┌──────────────┐
│  React SPA │ ─────────────▸ │  FastAPI Server  │ ───────────────▸  │ Celery Worker │
│  (Vite)    │ ◂───────────── │  (uvicorn)       │ ◂─────────────── │              │
└────────────┘  JSON + Stream └────────┬────────┘   task results    └──────┬───────┘
                                       │                                    │
                               ┌───────▼────────┐               ┌──────────▼──────────┐
                               │  SQLite / PG    │               │  yt-dlp · ffmpeg    │
                               │  (SQLAlchemy)   │               │  ffprobe · httpx    │
                               └─────────────────┘               │  musicbrainzngs     │
                                                                 └─────────────────────┘
```

### Component roles

| Component        | Technology             | Purpose                                       |
|------------------|------------------------|-----------------------------------------------|
| **API**          | FastAPI + Uvicorn      | REST endpoints, video streaming, CORS          |
| **Worker**       | Celery + Redis         | Background pipeline (download → organize)      |
| **Database**     | SQLAlchemy + Alembic   | ORM, migrations, metadata storage              |
| **Downloader**   | yt-dlp                 | Best-quality download with progress callbacks   |
| **Analyzer**     | ffprobe                | Quality signature extraction (res, codecs, HDR) |
| **Normalizer**   | ffmpeg (EBU R128)      | LUFS loudness normalization                     |
| **Metadata**     | MusicBrainz + Wikipedia| Artist/title/year/genre/album/artwork           |
| **AI Summary**   | Gemini / OpenAI (opt.) | Plot/visual description generation              |
| **Frontend**     | React 18 + TailwindCSS | Dark SPA with grid, player, hover previews      |

---

## 2. Database Schema

### Tables

| Table                  | Key Columns                                                         |
|------------------------|---------------------------------------------------------------------|
| `video_items`          | id, title, artist, album, year, resolution_label, file_path, poster_path, folder_path, duration_sec, source_provider, source_id, source_url, status, ai_summary, created_at, updated_at |
| `sources`              | id, video_item_id, provider, video_id, url, added_at               |
| `quality_signatures`   | id, video_item_id, width, height, fps, video_codec, audio_codec, video_bitrate, audio_bitrate, container, hdr, duration_sec, measured_lufs, quality_score, created_at |
| `metadata_snapshots`   | id, video_item_id, snapshot_json, source_label, created_at          |
| `media_assets`         | id, video_item_id, asset_type (poster/fanart/thumb/preview), file_path, url, created_at |
| `genres`               | id, name (unique)                                                   |
| `video_genres`         | video_item_id, genre_id (M2M join)                                  |
| `processing_jobs`      | id, video_item_id, job_type, status, current_step, progress_percent, input_url, error_message, log_text, created_at, started_at, completed_at |
| `app_settings`         | id, key (unique), value, updated_at                                 |
| `normalization_history`| id, video_item_id, before_lufs, after_lufs, target_lufs, gain_applied_db, method, created_at |
| `playback_history`     | id, video_item_id, position_sec, duration_sec, completed, played_at |

### Entity Relationships

```
video_items 1──* sources
video_items 1──* quality_signatures
video_items 1──* metadata_snapshots
video_items 1──* media_assets
video_items *──* genres  (via video_genres)
video_items 1──* processing_jobs
video_items 1──* normalization_history
video_items 1──* playback_history
```

---

## 3. API Endpoints

### Library (`/api/library`)

| Method | Path                        | Description                      |
|--------|-----------------------------|----------------------------------|
| GET    | `/`                         | List videos (paginated, filtered, sorted) |
| GET    | `/artists`                  | Distinct artist list + counts    |
| GET    | `/years`                    | Distinct year list + counts      |
| GET    | `/genres`                   | Genre list + counts              |
| GET    | `/albums`                   | Album list + counts              |
| GET    | `/{id}`                     | Full video detail                |
| PUT    | `/{id}`                     | Update video metadata            |
| DELETE | `/{id}`                     | Delete video + files             |
| GET    | `/{id}/snapshots`           | Metadata version history         |
| POST   | `/{id}/undo-rescan`         | Restore previous metadata        |

### Jobs (`/api/jobs`)

| Method | Path                        | Description                      |
|--------|-----------------------------|----------------------------------|
| POST   | `/import`                   | Import video by URL              |
| POST   | `/rescan/{id}`              | Re-scrape metadata for one video |
| POST   | `/rescan-batch`             | Batch re-scrape                  |
| POST   | `/normalize/{id}`           | Normalize one video              |
| POST   | `/normalize-batch`          | Batch normalize                  |
| POST   | `/library-scan`             | Scan library directory on disk   |
| GET    | `/`                         | List jobs (filtered)             |
| GET    | `/{id}`                     | Job detail                       |
| GET    | `/{id}/log`                 | Job log text                     |
| POST   | `/{id}/retry`               | Retry a failed job               |
| POST   | `/{id}/cancel`              | Cancel a queued job              |

### Playback (`/api/playback`)

| Method | Path                        | Description                      |
|--------|-----------------------------|----------------------------------|
| GET    | `/stream/{id}`              | Range-request video streaming    |
| GET    | `/preview/{id}`             | Hover-preview clip               |
| GET    | `/poster/{id}`              | Poster image                     |
| POST   | `/history`                  | Record playback position         |

### Settings (`/api/settings`)

| Method | Path                        | Description                      |
|--------|-----------------------------|----------------------------------|
| GET    | `/`                         | All settings                     |
| PUT    | `/{key}`                    | Update one setting               |
| GET    | `/normalization-history/{id}`| LUFS history for a video        |

### Root

| Method | Path                        | Description                      |
|--------|-----------------------------|----------------------------------|
| GET    | `/api/health`               | Health check                     |
| GET    | `/api/stats`                | Dashboard stats (counts, active) |

---

## 4. Job Pipeline — State Machine

The import pipeline moves through these states in `processing_jobs.status`:

```
queued → downloading → downloaded → remuxing → analyzing → normalizing → tagging → writing_nfo → asset_fetch → complete
                                                                                                            ↘ failed
Any step may transition to → failed (with error_message)
A queued job may be → cancelled
A failed job may be → retried (re-queued)
```

### Import pipeline steps (in order)

1. **Identify** — Parse URL, extract provider + video ID, check for existing item  
2. **Check existing** — If item exists, fetch new format list, compare quality scores; skip if no upgrade  
3. **Download** — yt-dlp best video+audio → merge to MKV in temp dir (retry + format fallback)  
4. **Analyze** — ffprobe quality signature (resolution, codecs, bitrates, HDR, LUFS)  
5a. **Resolve metadata (unified)** — Single code path via `resolve_metadata_unified()`:
    - **AI Source Resolution** (pre-scrape, if enabled) — Determines canonical identity (artist/title) and
      external source links (MusicBrainz recording UUID, Wikipedia URL, IMDB URL) BEFORE any scraping.
      Controlled by `ai_source_resolution` setting (default: true when AI provider configured).
    - **Source-guided scraping** — Uses AI-provided links/IDs first (direct lookup), falls back to
      search-based resolution only if AI links are missing or invalid.
      - MusicBrainz: AI recording UUID → `get_recording_by_id` → search fallback
      - Wikipedia: AI URL → direct scrape → search fallback (validated via `detect_article_mismatch`)
      - IMDB: AI URL → search fallback
    - **AI Final Review** (post-scrape, if enabled) — Verifies scraped data against all signals,
      applies high-confidence corrections (threshold ≥ 0.7), and approves/rejects artwork.
      Controlled by `ai_final_review` setting (default: true when AI provider configured).
    - Both automatic import and manual "Analyze Metadata" use this SAME code path.
5b. **Version detection** — Identify cover/live/alternate versions from filename + description signals  
6. **Organize** — Build folder name `Artist - Title [Resolution]`, move to library, archive old if upgrade  
7. **Normalize** — Measure LUFS, apply gain to hit target (default -14.0 LUFS), remux  
8a. **Write NFO** — Kodi-format XML sidecar with all metadata  
8c. **Entity resolution** — Resolve canonical Artist/Album/Track entities  
    - Phase 1: Network resolution (MusicBrainz + Wikipedia + CoverArtArchive providers)  
    - Entity name safeguard: resolved `canonical_name` must have `fuzzy_score ≥ 0.4` vs input  
    - Phase 2: DB writes (get_or_create_artist/album/track with genre dedup)  
    - Entity asset download (artist/album images from CoverArtArchive)  
8b. **Artist/Album artwork** — Runs AFTER entity resolution (uses MB IDs for CoverArtArchive)  
8c.6. **Entity enrichment** — Backfill video metadata from resolved entities (year, album, plot, genres)  
8c.7. **Poster upgrade** — Replace YouTube thumbnail with album cover art if available  
8d. **Kodi export** — Write artist.nfo, album.nfo, and video NFO for Kodi library scanning  
9. **Save to DB** — Upsert VideoItem, QualitySignature, Sources (with provenance), Genres, MediaAssets  
10. **Match scoring** — Compute fingerprint + metadata match confidence  
11. **Preview generation** — Short hover preview clip (cache keyed by `v{video_id}_{basename}`)  
12. **Scene analysis** — AI scene detection + thumbnail generation  
13. **AI metadata enrichment** — Full-context AI verification with Source records  
    - Mismatch detection → lowered threshold (0.5) for identity corrections  
    - If AI changes artist/title/album → **re-run entity resolution** to fix contaminated links  

### Source Provenance Tracking

Every Source record includes a `provenance` field indicating how the URL/ID was discovered:
- `"import"` — Primary source URL from yt-dlp at import time
- `"ai"` — URL/ID provided by AI Source Resolution stage
- `"scraped"` — Found via search-based scraping (MusicBrainz search, Wikipedia search, IMDB search)
- `"manual"` — User-entered via the UI

### Settings (metadata pipeline)

| Key                    | Default    | Description                                                |
|------------------------|------------|------------------------------------------------------------|
| `ai_source_resolution` | `true`     | Run AI source resolution pre-scrape (requires AI provider) |
| `ai_final_review`      | `true`     | Run AI final review post-scrape (requires AI provider)     |
| `ai_provider`          | `none`     | AI provider (openai, gemini, claude, local, none)          |

---

## 5. Key Algorithms

### Quality Score

```python
score = height * 1000 + video_bitrate / 1000 + (fps if fps >= 50 else 0) * 10 + (500 if hdr else 0)
```

Used to determine if a re-download is an upgrade over the existing file.

### Resolution Label Derivation

| Height range   | Label  |
|----------------|--------|
| ≥ 2160         | 2160p  |
| ≥ 1440         | 1440p  |
| ≥ 1080         | 1080p  |
| ≥ 720          | 720p   |
| ≥ 480          | 480p   |
| < 480          | SD     |

### LUFS Normalization

1. Extract audio to WAV (pcm_s24le, 48kHz)  
2. Measure integrated loudness via ffmpeg EBU R128 (ebur128 filter)  
3. Calculate gain = target_LUFS − measured_LUFS  
4. If |gain| > tolerance (default 0.5 dB): apply gain via `volume` filter → new WAV → remux back  
5. Record before/after LUFS in `normalization_history`

### Metadata Resolution Priority

Fields are merged with this priority (first non-empty wins):

1. AI Final Review corrections (high-confidence field overrides, threshold ≥ 0.7)
2. User overrides (artist_override, title_override)
3. AI Source Resolution identity (pre-scrape canonical identity)
4. Source-guided scraper results (AI links first, then search fallback)
   - MusicBrainz results (validated: artist name must overlap input ≥ 40%)
   - Wikipedia infobox scrape (validated: `detect_article_mismatch()` must pass)
5. yt-dlp extracted metadata

### Folder Naming

```
{Library Root}/{Artist} - {Title} [{Resolution}]/
  {Artist} - {Title} [{Resolution}].mkv
  {Artist} - {Title} [{Resolution}].nfo
  poster.jpg
  fanart.jpg
```

Filenames are sanitized: `< > : " / \ | ? *` replaced with `_`, trimmed, max 200 chars.

### Title Cleaning (from reference script)

Strips common YouTube noise from titles:
- "(Official Video)", "(Official Music Video)", "(Lyrics)", "(Audio)"
- "[HD]", "(HQ)", "(Live)", "(Remastered)", "(Visualizer)"
- Leading "VEVO -" prefixes
- Trailing whitespace and dashes

---

## 6. UI Pages

| Route        | Page              | Features                                               |
|--------------|-------------------|--------------------------------------------------------|
| `/`          | Library           | Video grid, search, sort, pagination, scan/rescan      |
| `/artists`   | Artists           | Artist cards with video counts, click → filtered library |
| `/years`     | Years             | Year grid with counts                                  |
| `/genres`    | Genres            | Tag cloud with counts                                  |
| `/video/:id` | Video Detail      | Player, metadata editor, quality sidebar, actions       |
| `/queue`     | Queue             | Job list with status, progress bars, log viewer         |
| `/import`    | Import            | URL input, overrides, normalize toggle, recent imports  |
| `/settings`  | Settings          | Grouped config (library, tools, normalization, AI)      |

### UI Features

- **Dark theme** — Slate/blue palette, no light mode
- **Hover preview** — 500ms delay, loads low-res preview clip, muted autoplay loop
- **Video player** — HTML5 native, playback position tracking, resume support
- **Metadata editor** — Inline edit with save, metadata snapshot history, undo
- **Quality badges** — Resolution label color-coded on every card
- **Status badges** — Color-coded job states throughout queue and detail views

---

## 7. Technology Stack Rationale

| Choice                | Why                                                       |
|-----------------------|-----------------------------------------------------------|
| **FastAPI**           | Async, auto OpenAPI docs, Pydantic validation, fast       |
| **Celery + Redis**    | Battle-tested task queue; Redis doubles as cache/broker    |
| **SQLAlchemy + Alembic** | Most mature Python ORM; Alembic for safe migrations    |
| **SQLite (dev) / PG (prod)** | Zero-config dev, production-ready with PG switch    |
| **React 18 + Vite**   | Fast HMR, modern JSX, huge ecosystem                     |
| **TailwindCSS**       | Utility-first, easy dark theme, no CSS files to maintain  |
| **TanStack Query**    | Caching, polling, refetch — ideal for job status updates  |
| **yt-dlp**            | Most maintained YouTube downloader, format selection       |
| **ffmpeg / ffprobe**  | Industry standard for media analysis and transcoding       |
| **MusicBrainz**       | Open, free music metadata database with good coverage      |
| **MKV container**     | Universal, supports all codecs, no re-encoding needed      |

---

## 8. Phased Build Plan

### Phase 1: Foundation (MVP) ✅
**Checkpoint: Can import a video by URL and see it in the library**

- [x] Project scaffold (FastAPI + React + Vite)
- [x] Database models and Alembic migrations setup
- [x] Configuration module with env vars
- [x] yt-dlp download service with progress
- [x] ffprobe media analysis service
- [x] Basic file organizer (move + folder naming)
- [x] Celery task pipeline (import flow)
- [x] REST API — import, list, stream
- [x] React frontend — library grid, video player, import page

### Phase 2: Metadata & Quality
**Checkpoint: Metadata auto-resolves; upgrades are detected**

- [x] MusicBrainz search integration
- [x] Wikipedia scraper (infobox + plot)
- [x] Metadata merge with priority system
- [x] Quality signature comparison and upgrade detection
- [x] NFO file writer (Kodi XML)
- [x] Metadata snapshots and undo
- [x] Artists / Years / Genres browse pages

### Phase 3: Normalization & Previews
**Checkpoint: Audio is normalized; hover previews work**

- [x] LUFS measurement (EBU R128)
- [x] Audio normalization (gain + remux)
- [x] Normalization history tracking
- [x] Hover-preview clip generation
- [x] Playback history / resume
- [x] Settings page with all config groups

### Phase 4: Polish & Deployment
**Checkpoint: Docker-compose up gives working system**

- [x] Queue page with real-time job status
- [x] Batch operations (rescan, normalize)
- [x] Library scan (discover on-disk files)
- [x] Docker multi-stage build
- [x] docker-compose.yml (API + worker + Redis + nginx)
- [x] .env.example with all settings documented

### Phase 5: Future Enhancements (not yet implemented)

- [ ] AI summary generation (Gemini / OpenAI plug-in is scaffolded)
- [ ] WebSocket for real-time job progress push
- [ ] Scheduled library scans (celery-beat)
- [ ] Duplicate detection across sources
- [ ] Playlist / collection management
- [ ] Multi-user auth
- [ ] Mobile-responsive layout tuning
- [ ] Import from text file (batch URLs)
- [ ] Plex/Jellyfin/Emby webhook integration

---

## 9. Running Locally (Development)

### Prerequisites
- Python 3.11+
- Node.js 18+
- Redis server
- ffmpeg + ffprobe on PATH
- yt-dlp on PATH

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env         # Edit paths as needed

# Start API server
uvicorn app.main:app --reload --port 8000

# Start Celery worker (separate terminal)
celery -A app.worker.celery_app worker --loglevel=info --pool=solo
```

### Frontend

```bash
cd frontend
npm install
npm run dev                  # http://localhost:3000
```

### Docker

```bash
docker compose up --build    # API at :8000, UI at :3080
```

---

## 10. Configuration Reference

| Variable              | Default                    | Description                        |
|-----------------------|----------------------------|------------------------------------|
| `LIBRARY_DIR`         | `./data/library`           | Organized video files              |
| `ARCHIVE_DIR`         | `./data/archive`           | Replaced files backup              |
| `DOWNLOAD_DIR`        | `./data/downloads`         | Temporary download location        |
| `DATABASE_URL`        | `sqlite:///./playarr.db`   | Database connection string         |
| `REDIS_URL`           | `redis://localhost:6379/0` | Celery broker URL                  |
| `FFMPEG_PATH`         | auto-detect                | Path to ffmpeg binary              |
| `FFPROBE_PATH`        | auto-detect                | Path to ffprobe binary             |
| `YTDLP_PATH`          | auto-detect                | Path to yt-dlp binary              |
| `TARGET_LUFS`         | `-14.0`                    | Loudness normalization target      |
| `LUFS_TOLERANCE`      | `0.5`                      | Acceptable LUFS deviation          |
| `MERGE_CONTAINER`     | `mkv`                      | Output container format            |
| `AI_PROVIDER`         | `none`                     | `none`, `gemini`, or `openai`      |
| `GEMINI_API_KEY`      | —                          | Google Gemini API key              |
| `OPENAI_API_KEY`      | —                          | OpenAI API key                     |
| `PREVIEW_DURATION`    | `4`                        | Hover preview duration (seconds)   |
| `PREVIEW_START_PCT`   | `0.25`                     | Start preview at this % into video |
| `PREVIEW_HEIGHT`      | `480`                      | Preview clip resolution height     |
| `CORS_ORIGINS`        | `http://localhost:3000`    | Allowed CORS origins               |

---

## Artwork Pipeline Architecture

### Overview

All artwork operations (download, validation, caching, invalidation, repair) are
centralised in a single service: `app/services/artwork_service.py`.  No other code
path is permitted to download, validate, or persist image files directly.

### Validation guarantees

Every image passes through a multi-layer validation gate before it is persisted:

```
URL → HTTP GET → Status 200? → Content-Type image/*? → Magic bytes match?
     → Write to temp file → PIL open+verify → Resize/convert → Atomic move → SHA-256
```

If **any** step fails, no file is left on disk.  Specifically:

1. **HTTP status** — only 200 is accepted.
2. **Content-Type** — must start with `image/` (catches HTML error pages, XML, JSON).
3. **Magic bytes** — first 16 bytes must match JPEG, PNG, WebP, or GIF signatures.
   This catches servers that lie about Content-Type (e.g. `image/jpeg` for an HTML 
   error page).
4. **PIL verify** — Pillow opens the image and calls `verify()` to detect corrupt
   data or truncated streams.
5. **Resize/convert** — converts to JPEG for uniform storage.  If resize fails,
   the corrupt file is **deleted** (destructive failure — never leaves a bad file).
6. **Atomic write** — download goes to a temp file in the same directory; only after
   all checks pass is it renamed to the final path.

### Two asset stores

| Store | Model | Purpose | Location |
|-------|-------|---------|----------|
| **Entity cache** | `CachedAsset` | Canonical source for artist/album artwork | `PlayarrCache/assets/{entity_type}/{entity_id}/` |
| **Kodi layout** | `MediaAsset` | Per-video artwork for Kodi/Plex scanners | `_artists/{name}/`, `_albums/{artist}/{album}/`, video folder |

The entity cache is the **source of truth**.  Kodi-layout files are derived copies
created by `artwork_manager.py`, which delegates all downloads through `artwork_service`.

### Provenance tracking

Both `CachedAsset` and `MediaAsset` now carry provenance metadata:

| Column | Purpose |
|--------|---------|
| `status` | `valid`, `invalid`, `missing`, `pending` |
| `content_type` | HTTP Content-Type from the download |
| `source_provider` | `musicbrainz`, `wikipedia`, `coverartarchive`, `youtube`, etc. |
| `resolved_url` | Final URL after redirects (provenance) |
| `file_hash` | SHA-256 of the downloaded file |
| `last_validated_at` | Timestamp of last validation check |
| `validation_error` | Human-readable error if status != valid |

### Invalidation rules

| Event | Action |
|-------|--------|
| **Video deleted** | All MediaAssets for that video marked `invalid`, files deleted |
| **Entity orphaned** | `delete_entity_cached_assets()` removes CachedAsset records + files |
| **Re-import** | Entity asset downloads use `overwrite=True` to replace stale caches |
| **Corrupt cache detected** | `download_asset()` validates cache before reuse; re-downloads on failure |
| **Manual repair** | `/api/artwork/repair` endpoint scans all assets, re-downloads invalid ones |

### Frontend resolution

`ArtworkTiles.tsx` filters assets by `status === "valid"` and prefers local
`/api/playback/asset/{id}` paths over remote `source_url` values (since local
files are guaranteed validated).  Invalid/missing assets show the placeholder.

### Repair API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/artwork/status` | GET | Health summary counts (valid/invalid/missing) |
| `/api/artwork/validate/{video_id}` | POST | Validate all assets for one video |
| `/api/artwork/repair` | POST | Full repair: scan + re-download all invalid |
| `/api/artwork/repair/cached` | POST | Repair only CachedAssets |
| `/api/artwork/repair/media` | POST | Repair only MediaAssets |

### Migration

Alembic migration `002_asset_validity_provenance` adds the provenance columns.
The startup schema upgrade in `main.py` (`_apply_schema_upgrades`) also applies
these columns idempotently for SQLite databases that bypass Alembic.
