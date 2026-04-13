# Playarr — Project State Summary

> **Generated:** 11 April 2026
> **Version:** 1.9.13
> **Git:** Local repository, main branch

---

## 1. Codebase Metrics

| Metric | Count |
|--------|-------|
| **Backend Python files** | 187 |
| **Backend lines of code** | ~95,100 |
| **Frontend TypeScript files** | 84 |
| **Frontend lines of code** | ~34,600 |
| **Frontend CSS** | 1 file (index.css, ~220 lines, Tailwind CSS 4 @theme + @utility design system) |
| **Total lines (backend + frontend)** | ~129,700 |
| **Alembic migrations** | 22 |
| **Test files** | 7 |
| **Documentation files** | 8 (in docs/) |

---

## 2. Architecture Overview

```
React 19 SPA ──▸ FastAPI REST API (port 6969) ──▸ Celery + Redis ──▸ Workers
     │                    │                                              │
     │              SQLite (WAL)                           yt-dlp, ffmpeg, ffprobe
     │              (playarr.db)                           MusicBrainz, Wikipedia
     │                                                     AI (OpenAI/Gemini/Claude/Ollama)
     │
  Tailwind CSS 4 dark theme · Zustand · React Query · Axios
```

---

## 3. Backend Structure

```
backend/
├── _start_server.py          # Dev launcher with auto-restart (exit code 75)
├── tray.py                   # Windows system tray icon (pystray + Pillow)
├── requirements.txt          # Python dependencies
├── alembic.ini               # Alembic config
├── .env.example              # Environment template
├── alembic/versions/         # 11 migration scripts
├── tests/                    # 7 test files (matching, AI, artwork, batch import)
└── app/
    ├── main.py               # FastAPI entry — lifespan, routers, schema upgrades, version stamp
    ├── config.py             # Pydantic-settings config from .env
    ├── database.py           # SQLAlchemy dual-engine setup (WAL mode)
    ├── models.py             # Core ORM models (~950 lines)
    ├── schemas.py            # Pydantic request/response schemas
    ├── tasks.py              # Celery tasks (import, rescan, normalise, scan, export)
    ├── version.py            # APP_VERSION = "1.9.13"
    ├── worker.py             # Celery setup + Redis broker
    ├── routers/              # 12 FastAPI routers
    │   ├── library.py        # Video CRUD, search, filters, party mode, rename
    │   ├── jobs.py           # Import, rescan, normalise, scan, telemetry
    │   ├── playback.py       # Stream, preview, poster, history
    │   ├── settings.py       # Config CRUD, directories, restart, naming
    │   ├── metadata.py       # Entity CRUD, Kodi export, revisions
    │   ├── ai.py             # AI enrichment, scenes, fingerprint, models
    │   ├── artwork.py        # Artwork health, validation, repair
    │   ├── resolve.py        # Matching, review queue, pinning, search
    │   ├── playlists.py      # Playlist CRUD + entries
    │   ├── library_import.py # Batch filesystem import
    │   ├── video_editor.py   # Letterbox detection, cropping
    │   ├── scraper_test.py   # Manual scraper testing
    │   └── tmvdb.py          # TMVDB integration
    ├── services/             # 22 service modules
    ├── ai/                   # AI subsystem (providers, enrichment, scenes, fingerprint)
    ├── scraper/              # Unified metadata scraper
    ├── matching/             # Matching + scoring + version detection
    ├── metadata/             # Entity resolution, assets, Kodi export
    ├── new_videos/           # Discovery feed + recommendations
    ├── pipeline/             # Import pipeline stages
    ├── pipeline_url/         # URL import pipeline variant
    └── pipeline_lib/         # Library import pipeline variant
```

## 4. Frontend Structure

```
frontend/src/
├── App.tsx                   # Routes (18 pages)
├── main.tsx                  # Entry point
├── index.css                 # Tailwind 4 design system (dark theme)
├── pages/                    # 17 page components
│   ├── LibraryPage.tsx       # Main grid/list with hover previews
│   ├── VideoDetailPage.tsx   # Video detail + metadata editor
│   ├── QueuePage.tsx         # Job queue with telemetry
│   ├── SettingsPage.tsx      # Tabbed settings (7 tabs)
│   ├── ReviewQueuePage.tsx   # Review queue with batch actions
│   └── [13 more pages]
├── components/               # ~50 components (cards, panels, modals, editors)
├── hooks/                    # React Query hooks, hover preview, telemetry, party mode
├── stores/                   # Zustand (playback, artwork, fireworks)
├── lib/
│   ├── api.ts               # Axios API client (11 API objects)
│   └── types.ts             # TypeScript interfaces
└── assets/                   # Static assets
```

---

## 5. Database State

- **Engine:** SQLite, WAL mode, dual-engine pattern (main pool=20, cosmetic pool=10)
- **Tables (core):** video_items, sources, quality_signatures, media_assets, metadata_snapshots, processing_jobs, settings, genres, video_genres, normalization_history, playback_history, playlists, playlist_entries
- **Tables (entities):** artists, albums, tracks, cached_assets, metadata_revisions, export_manifests
- **Tables (matching):** match_results, match_candidates, normalization_results, user_pinned_matches
- **Tables (AI):** ai_metadata_results, ai_scene_analyses, ai_thumbnails
- **Tables (new videos):** suggested_videos, suggested_video_dismissals, suggested_video_cart_items, recommendation_snapshots, recommendation_feedback
- **Total tables:** ~30
- **Versioning:** `schema_version` setting stamped on startup, mismatch detection for downgrades

---

## 6. API Surface

| Router | Prefix | Endpoints |
|--------|--------|-----------|
| Library | `/api/library` | ~22 endpoints (CRUD, search, filters, rename, party mode) |
| Jobs | `/api/jobs` | ~17 endpoints (import, rescan, normalise, telemetry) |
| Playback | `/api/playback` | 5 endpoints (stream, preview, poster, artwork-ids, history) |
| Settings | `/api/settings` | ~10 endpoints (CRUD, directories, restart, naming) |
| Metadata | `/api/metadata` | ~14 endpoints (entities, export, revisions) |
| AI | `/api/ai` | ~19 endpoints (enrich, scenes, fingerprint, models) |
| Artwork | `/api/artwork` | 5 endpoints (status, validate, repair) |
| Resolve | `/api/resolve` | ~8 endpoints (match, pin, batch, undo) |
| Review | `/api/review` | ~6 endpoints (queue, approve, dismiss, batch) |
| Search | `/api/search` | 3 endpoints (artist, recording, release) |
| Playlists | `/api/playlists` | ~8 endpoints (CRUD, entries) |
| Library Import | `/api/library-import` | Batch import endpoints |
| Video Editor | `/api/video-editor` | Letterbox/crop endpoints |
| Scraper Test | `/api/scraper-test` | Manual test endpoints |
| TMVDB | `/api/tmvdb` | Push/pull/sync |
| New Videos | `/api/new-videos` | Feed, cart, dismiss, feedback |
| System | `/api/` | health, version, stats |

**Total:** ~130+ REST endpoints

---

## 7. External Integrations

| Service | Status | Purpose |
|---------|--------|---------|
| **MusicBrainz API** | Active | Artist/recording/release metadata |
| **Wikipedia** | Active | Scraping artist/album/track pages for data + images |
| **CoverArtArchive** | Active | Album + single cover artwork |
| **YouTube (yt-dlp)** | Active | Video download, thumbnail, metadata |
| **Vimeo (yt-dlp)** | Active | Video download |
| **IMDB** | Active | Music video search |
| **TMVDB** | Optional | The Music Video Database sync |
| **OpenAI** | Optional | AI enrichment (gpt-5 family) |
| **Google Gemini** | Optional | AI enrichment (gemini-2.x family) |
| **Anthropic Claude** | Optional | AI enrichment (claude-sonnet-4, opus-4) |
| **Ollama** | Optional | Local AI (dynamic model discovery) |
| **AcoustID** | Optional | Audio fingerprint identification |
| **Redis** | Required (prod) | Celery broker (thread fallback available) |

---

## 8. Known Gaps & Technical Debt

### Testing
- **7 test files** with focused coverage on matching, AI pipeline, and artwork
- **No endpoint tests** — routers lack automated test coverage
- **No service-layer unit tests** — core services (downloader, organiser, analyser, normaliser) untested
- **No integration tests** — full pipeline (import URL → complete) not tested end-to-end

### Architecture
- **Triple pipeline architecture** — three parallel pipeline implementations exist (`pipeline/`, `pipeline_lib/`, `pipeline_url/`). Each contains duplicated service code. Only `pipeline_url/` appears to be the active primary path. The other two are legacy but still imported.
- **Duplicate service files** — Some services exist in both `app/services/` and `app/scraper/` (unified_metadata, metadata_resolver, source_validation, artist_album_scraper). Both locations are actively imported from different callers.
- **SQLite concurrency** — Dual-engine pattern with WAL mode works but is a workaround for SQLite's single-writer limitation
- **Subprocess cleanup** — Playback stream tracking (`_streams_by_file`) has no explicit cleanup on server crash/restart
- **Partial download cleanup** — Error paths in downloader may leave incomplete files on disk

### Documentation
- `ARCHITECTURE.md` is partially outdated — describes original schema, missing entity tables, AI subsystem, matching, recommendations
- `FAILED_APPROACHES.md` is comprehensive and actively maintained (100+ documented failures)
- No API documentation beyond auto-generated OpenAPI
- No user-facing documentation or setup guide beyond README basics

### Frontend
- **Chunk size warning** — Single JS bundle at 971 KB (optimisation: code-splitting recommended)
- **No error boundary** — Unhandled React errors crash the entire SPA

---

## 9. Git State

```
Branch: main
Commits: 44+
Tags: v1.0.0 through v1.9.4
Remote: origin → https://github.com/lambertius/playarr
```
**Status:** Active development, main branch, remote configured

---

## 10. Dependencies

### Backend (Python 3.12)
fastapi, uvicorn, sqlalchemy, alembic, celery[redis], redis, pydantic, pydantic-settings, httpx, beautifulsoup4, musicbrainzngs, yt-dlp, python-multipart, aiofiles, fake-useragent, natsort, Pillow, python-dotenv

### Frontend (Node 20)
react 19, react-dom, react-router-dom 7, @tanstack/react-query 5, axios, zustand 5, lucide-react, clsx, tailwind-merge

### Dev
typescript 5.9, vite 7, tailwindcss 4, eslint 9, @vitejs/plugin-react

### System
ffmpeg, ffprobe (system packages), Redis 7 (Docker or system)

---

## 11. Deployment Options

1. **Dev (Windows):** `python backend/_start_server.py` — auto-restart, system tray, port 6969
2. **Docker Compose:** `docker-compose up` — redis + api + worker + nginx. Frontend on port 3080, API on 6969.
3. **Tools auto-detection:** ffmpeg, ffprobe, yt-dlp paths auto-detected via `shutil.which()` with Windows fallback paths
