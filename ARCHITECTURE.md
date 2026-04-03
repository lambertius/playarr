# Playarr — Architecture

## System Overview

Playarr is a self-hosted music video manager built with a FastAPI backend, React frontend, and Celery background workers. It downloads music videos, resolves metadata from multiple sources, normalises audio loudness, organises files on disk, and serves everything through a dark-themed web UI.

```
┌────────────┐   HTTP/REST    ┌─────────────────┐   Celery/Redis   ┌──────────────┐
│  React SPA │ ─────────────▸ │  FastAPI Server  │ ───────────────▸ │ Celery Worker │
│  (Vite)    │ ◂───────────── │  (Uvicorn)       │ ◂─────────────── │              │
└────────────┘  JSON + Stream └────────┬────────┘   task results   └──────┬───────┘
                                       │                                   │
                               ┌───────▼────────┐              ┌──────────▼──────────┐
                               │     SQLite      │              │  yt-dlp · ffmpeg    │
                               │  (SQLAlchemy)   │              │  ffprobe · httpx    │
                               └─────────────────┘              │  musicbrainzngs     │
                                                                └─────────────────────┘
```

---

## Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| **Frontend** | React, Vite, TypeScript, Tailwind CSS | 19, 7, 5.9, 4.2 |
| **API** | FastAPI + Uvicorn | 0.104+ |
| **Background** | Celery + Redis | 5.3+ |
| **ORM** | SQLAlchemy + Alembic | 2.0+ |
| **Database** | SQLite (WAL mode) | — |
| **Downloader** | yt-dlp | 2024+ |
| **Media** | ffmpeg + ffprobe | 5+ |
| **Metadata** | MusicBrainz API, Wikipedia, CoverArtArchive | — |
| **AI** (optional) | OpenAI, Google Gemini, Anthropic Claude, Ollama | — |

---

## Backend Structure

```
backend/
├── app/
│   ├── main.py                # FastAPI app, lifespan, startup tasks
│   ├── config.py              # Pydantic-settings configuration
│   ├── database.py            # SQLAlchemy engine, sessions, dual pool
│   ├── models.py              # ORM models (~30 tables)
│   ├── schemas.py             # Pydantic request/response schemas
│   ├── tasks.py               # Celery task definitions
│   ├── version.py             # APP_VERSION constant
│   ├── worker.py              # Celery app configuration
│   │
│   ├── routers/               # FastAPI route handlers
│   │   ├── library.py         # Library CRUD, filtering, stats
│   │   ├── jobs.py            # Job management, import, rescan
│   │   ├── playback.py        # Video streaming, previews, posters
│   │   ├── settings.py        # Settings CRUD, startup management
│   │   ├── metadata.py        # Metadata operations
│   │   ├── ai.py              # AI enrichment endpoints
│   │   ├── artwork.py         # Artwork cache management
│   │   ├── resolve.py         # Match/review/search/export
│   │   ├── playlists.py       # Playlist CRUD
│   │   ├── library_import.py  # Bulk library import wizard
│   │   ├── video_editor.py    # Video trim/clip editor
│   │   ├── scraper_test.py    # Metadata scraper testing tool
│   │   ├── new_videos.py      # Video discovery/suggestions
│   │   └── tmvdb.py           # TMVDB integration
│   │
│   ├── services/              # Business logic
│   │   ├── unified_metadata.py      # Single-path metadata resolution
│   │   ├── entity_resolution.py     # Artist/Album/Track entity linking
│   │   ├── canonical_track.py       # Canonical track matching
│   │   ├── library_scanner.py       # Filesystem scanning
│   │   ├── preview_generator.py     # Hover preview clip creation
│   │   └── ...                      # (~22 service modules)
│   │
│   ├── scraper/               # Metadata scraping
│   │   ├── metadata_resolver.py     # MusicBrainz + Wikipedia + IMDB
│   │   ├── musicbrainz_client.py    # MB API wrapper
│   │   └── wikipedia_scraper.py     # Wikipedia page parsing
│   │
│   ├── ai/                    # AI provider abstraction
│   │   ├── providers/         # OpenAI, Gemini, Claude, Ollama
│   │   └── prompts/           # Shared prompt templates
│   │
│   ├── matching/              # Match scoring & confidence
│   │
│   ├── new_videos/            # Video discovery subsystem
│   │
│   ├── pipeline_url/          # ★ ACTIVE — URL import pipeline
│   │   ├── stages.py          # Step-by-step import stages
│   │   ├── workspace.py       # Import workspace state
│   │   ├── mutation_plan.py   # DB mutation plan builder
│   │   ├── db_apply.py        # Apply plan to database
│   │   ├── deferred.py        # Deferred/async task dispatch
│   │   ├── write_queue.py     # Serialised DB write queue
│   │   ├── services/          # Pipeline-specific services
│   │   ├── metadata/          # Metadata resolution
│   │   ├── matching/          # Version detection
│   │   └── ai/                # AI source resolution + review
│   │
│   ├── pipeline_lib/          # ★ ACTIVE — Library import pipeline
│   │   └── (mirrors pipeline_url/ structure)
│   │
│   └── pipeline/              # ⚠ LEGACY — Original pipeline (unused)
│       └── (minimal, superseded by pipeline_url/)
│
├── tray.py                    # Windows system tray icon
├── _start_server.py           # Development server launcher
├── requirements.txt           # Python dependencies
└── .env.example               # Configuration template
```

---

## Pipeline Architecture

Playarr has three pipeline directories. This is a known technical debt item (see [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)):

| Directory | Status | Purpose |
|-----------|--------|---------|
| `pipeline_url/` | **Active** | Handles URL-based imports (YouTube, Vimeo). Used by the main import workflow. |
| `pipeline_lib/` | **Active** | Handles library imports (importing existing files from disk). Used by the Library Import wizard. |
| `pipeline/` | **Legacy** | Original pipeline implementation. Superseded by the above. Should not be modified without a refactor plan. |

### Import Pipeline State Machine

```
queued → downloading → analyzing → metadata_resolution → normalizing → writing_nfo → entity_resolution → complete
                                                                                                          ↘ failed
Any step may transition to → failed (with error_message)
A queued job may be → cancelled
A failed job may be → retried (re-queued)
```

### Metadata Resolution Flow

```
1. AI Source Resolution (optional)
   └─ Identifies canonical artist/title and external IDs

2. Source-Guided Scraping
   ├─ MusicBrainz: AI recording UUID → direct lookup → search fallback
   ├─ Wikipedia: AI URL → direct scrape → search fallback
   └─ IMDB: AI URL → search fallback

3. AI Final Review (optional)
   └─ Verifies scraped data, applies corrections (threshold ≥ 0.7)
```

---

## Database

SQLite in WAL mode with dual connection pools:
- **Main pool** (20 connections) — General read/write operations
- **Cosmetic pool** (10 connections) — Lightweight UI updates (ratings, play counts)

### Core Tables

| Table | Purpose |
|-------|---------|
| `video_items` | Central video metadata (~48 fields) |
| `sources` | Provider URLs/IDs with provenance tracking |
| `quality_signatures` | Resolution, codecs, bitrate, HDR, LUFS |
| `processing_jobs` | Job queue with status, progress, logs |
| `app_settings` | Key-value configuration store |
| `genres` / `video_genres` | Genre taxonomy (M2M) |
| `normalization_history` | LUFS before/after for each normalisation |
| `playback_history` | Position tracking for resume |
| `playlists` / `playlist_entries` | User playlists |

### Entity Tables

| Table | Purpose |
|-------|---------|
| `artist_entities` | Canonical artists with MusicBrainz IDs |
| `album_entities` | Albums with release group IDs |
| `track_entities` | Canonical tracks linking videos |
| `cached_assets` | Downloaded artwork cache |
| `metadata_revisions` | Entity metadata change history |

### Matching Tables

| Table | Purpose |
|-------|---------|
| `match_results` | Confidence scores for metadata matches |
| `match_candidates` | Alternative match options |
| `user_pinned_matches` | User-confirmed match selections |

### AI Tables

| Table | Purpose |
|-------|---------|
| `ai_metadata_results` | AI enrichment outputs |
| `ai_scene_analyses` | Scene detection results |
| `ai_thumbnails` | AI-generated thumbnail data |

### Discovery Tables

| Table | Purpose |
|-------|---------|
| `suggested_videos` | Recommended new videos |
| `suggested_video_cart` | User cart for suggested videos |
| `suggested_video_dismissals` | Dismissed suggestions |
| `suggested_video_feedback` | User feedback on suggestions |

---

## Frontend Structure

```
frontend/src/
├── pages/                     # Route pages
│   ├── LibraryPage.tsx        # Main grid with filters
│   ├── VideoDetailPage.tsx    # Video detail + player
│   ├── ArtistDetailPage.tsx   # Artist entity page
│   ├── AlbumDetailPage.tsx    # Album entity page
│   ├── TrackDetailPage.tsx    # Track entity page
│   ├── SettingsPage.tsx       # Configuration UI
│   ├── JobsPage.tsx           # Job queue management
│   ├── PlaylistPage.tsx       # Playlist views
│   └── ...                    # (~18 routes total)
│
├── components/                # Reusable UI components (~50)
│   ├── VideoCard.tsx          # Library grid card
│   ├── VideoPlayer.tsx        # Browser video player
│   ├── MetadataPanel.tsx      # Metadata editor
│   ├── Toast.tsx              # Notification system
│   └── ...
│
├── hooks/                     # React Query hooks, custom hooks
├── stores/                    # Zustand state stores
├── lib/
│   └── api.ts                 # Typed API client (axios)
└── index.css                  # Tailwind CSS 4 theme
```

### Design System

- **Theme:** Dark surfaces (#0f1117 → #2b3245), accent red (#e11d2e), secondary orange (#ff6a00)
- **Font:** Inter (via Google Fonts)
- **Components:** Custom CSS utility classes (`btn`, `card`, `badge`, `input-field`) defined in `index.css`

---

## AI Subsystem

AI features are optional and require an API key for cloud providers or a local Ollama instance.

| Provider | Models |
|----------|--------|
| OpenAI | gpt-4o, gpt-4.1, o3, o4-mini |
| Gemini | gemini-2.0-flash, gemini-2.5-pro |
| Claude | claude-sonnet-4-20250514, claude-opus-4-20250918 |
| Ollama | Any locally hosted model |

AI tasks:
- **Source Resolution** — Pre-scrape identification of artist, title, and external IDs
- **Final Review** — Post-scrape verification and correction
- **Scene Analysis** — Detect scenes and generate descriptions
- **Metadata Enrichment** — Fill missing fields from video content analysis

---

## Video Discovery (New Videos)

The recommendation engine suggests new music videos based on:
- Artists in your library
- Related artists via MusicBrainz relationships
- Genre analysis
- User feedback (thumbs up/down, dismissals)

---

## Docker Architecture

The `docker-compose.yml` defines four services:

```
┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐
│  Redis   │   │  API      │   │  Worker  │   │ Frontend │
│  :6379   │◄──│  :6969    │   │  (Celery)│   │  :3080   │
│          │   │  (FastAPI)│   │          │   │  (Nginx) │
└──────────┘   └───────────┘   └──────────┘   └──────────┘
```

All backend containers share the same image (multi-stage Dockerfile). The frontend is built as static files and served by Nginx.


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
