# Playarr

A self-hosted music video manager inspired by Sonarr, Radarr, and Lidarr. Download, organise, enrich, and browse your music video collection through a modern web interface.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Version](https://img.shields.io/badge/version-1.9.12-green.svg)

> **Note:** Playarr is in its first public release. Contributions, issues, and feedback are welcome.

---

## Overview

Playarr automates the process of building and maintaining a music video library:

1. **Import** — Paste a YouTube/Vimeo URL or point at an existing folder of videos.
2. **Enrich** — Metadata is resolved automatically via MusicBrainz, Wikipedia, and (optionally) AI providers.
3. **Organise** — Files are renamed, sorted into artist/album folders, and tagged with Kodi-compatible NFO sidecars.
4. **Normalise** — Audio loudness is adjusted to a consistent LUFS target using ffmpeg EBU R128.
5. **Browse** — A dark-themed React UI with filtering, playback, hover previews, and entity pages.

---

## Features

- **URL Import** — Download music videos from YouTube and Vimeo via yt-dlp with automatic quality selection
- **Library Import** — Bulk-import an existing collection with metadata scraping and source matching
- **Metadata Resolution** — MusicBrainz recordings/releases, Wikipedia scraping, IMDB lookups
- **AI Enrichment** (optional) — OpenAI, Google Gemini, Anthropic Claude, or local Ollama models for metadata verification, scene detection, and descriptions
- **Entity System** — Canonical artist, album, and track entities with artwork and cross-linking
- **Audio Normalisation** — EBU R128 loudness normalization (configurable LUFS target)
- **Quality Upgrades** — Automatically detect and replace lower-quality versions
- **Hover Previews** — Short looping preview clips generated from each video
- **Kodi Integration** — NFO sidecar generation for artist, album, and video metadata
- **Playlist Management** — Create and manage playlists within the UI
- **Video Discovery** — Suggested new videos based on your existing library
- **Match & Review System** — Confidence scoring for metadata matches with manual review workflow
- **System Tray** — Optional Windows system tray icon for background operation
- **Update Checker** — Automatic GitHub release checking with in-app notification banner
- **Docker Support** — Multi-container deployment with docker-compose

---

## Screenshots

> Screenshots will be added in a future update. The UI features a dark theme with a card-based library grid, detail pages for artists/albums/tracks, an inline video player, and a settings dashboard.

---

## Architecture

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

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | React 19, Vite 7, TypeScript 5.9, Tailwind CSS 4 | Dark-themed SPA with library browsing, player, and settings |
| **API** | FastAPI, Uvicorn | REST endpoints, video streaming, SSE progress |
| **Background Jobs** | Celery + Redis | Import pipeline, metadata scraping, AI enrichment |
| **Database** | SQLAlchemy + Alembic, SQLite (WAL mode) | ORM with migrations, ~30 tables |
| **Downloader** | yt-dlp | YouTube/Vimeo download with format selection |
| **Media Processing** | ffmpeg + ffprobe | Quality analysis, audio normalisation, preview generation |
| **Metadata** | MusicBrainz API, Wikipedia scraping | Artist/title/year/genre/album/artwork resolution |
| **AI** (optional) | OpenAI, Gemini, Claude, Ollama | Metadata verification, scene analysis, descriptions |

For detailed architecture documentation, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Requirements

| Dependency | Version | Notes |
|-----------|---------|-------|
| **Python** | 3.10+ | 3.12 recommended |
| **ffmpeg** | 5+ | Must include ffprobe; must be on PATH |
| **yt-dlp** | 2024+ | For URL imports (`pip install yt-dlp` or standalone binary) |
| **Node.js** | 18+ | Build only — not needed at runtime |
| Redis | 6+ | Optional — only for advanced multi-worker mode |

---

## Quick Start (Production — Single Port)

The simplest way to run Playarr as a user:

```bash
# 1. Build the frontend (one-time)
cd frontend && npm install && npm run build && cd ..

# 2. Install backend dependencies
cd backend
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
cd ..

# 3. Start Playarr
python run_playarr.py
```

Or on Windows:

```
build_playarr.bat     # one-time build
start_playarr.bat     # launch Playarr
```

Playarr runs on **http://localhost:6969** — frontend and API on a single port.

No Redis, no Nginx, no separate frontend server. Background tasks run in-process.

See [docs/RUN_PRODUCTION.md](docs/RUN_PRODUCTION.md) for full production documentation.

---

## Quick Start (Development)

For contributing or debugging, run frontend and backend separately:

```bash
# Terminal 1: Backend (port 6969)
cd backend
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
python _start_server.py

# Terminal 2: Frontend dev server (port 3000, proxies to backend)
cd frontend
npm install
npm run dev
```

Or on Windows: `start_dev.bat` opens both in separate terminal windows.

The Vite dev server at `http://localhost:3000` provides hot module replacement and proxies all `/api` requests to the backend at port 6969.

---

## Docker

```bash
docker-compose up -d
```

This starts four containers:

| Service | Port | Description |
|---------|------|-------------|
| `redis` | 6379 | Message broker |
| `api` | 6969 | FastAPI backend |
| `worker` | — | Celery background worker |
| `frontend` | 3080 | Nginx serving the built React app |

Configure volumes in `docker-compose.yml` to mount your library, archive, and downloads directories.

---

## Configuration

Playarr works out-of-the-box with sensible defaults — no `.env` file required for normal desktop use.

All settings can be changed at runtime via the **Settings** page in the UI. For advanced configuration, set environment variables or create a `.env` file in `backend/`.

Key settings:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LIBRARY_DIR` | No | Auto-detected | Path to your music video library |
| `ARCHIVE_DIR` | No | Auto-detected | Path for archived/replaced files |
| `FFMPEG_PATH` | No | `auto` (PATH search) | Path to ffmpeg |
| `AI_PROVIDER` | No | `none` | `none`, `openai`, `gemini`, `claude`, or `local` |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Only needed with `CELERY_WORKER_ENABLED=1` |

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the full environment variable reference.

---

## Usage

### Importing Videos

1. **By URL** — Click "Add Video" and paste a YouTube or Vimeo URL. Playarr downloads, analyses, and enriches the video automatically.
2. **By Library Import** — Go to Settings > Library > Import Library to bulk-import an existing music video folder with metadata scraping.
3. **Batch Import** — Paste multiple URLs at once for parallel downloading.

### Metadata

Playarr resolves metadata through multiple sources in priority order:
1. AI source resolution (identifies artist/title from video content)
2. MusicBrainz recording/release lookup
3. Wikipedia artist and single/album page scraping
4. AI final review (verifies and corrects scraped data)

Each source is optional and configurable in Settings.

### AI Enrichment

AI features are entirely optional. When enabled, they provide:
- Automatic artist/title identification from video content
- Metadata verification against scraped data
- Scene detection and description generation
- Thumbnail generation from key scenes

Configure your preferred AI provider and API key in Settings > AI.

---

## Known Limitations

- **Pipeline duplication** — Three parallel pipeline implementations exist (`pipeline/`, `pipeline_url/`, `pipeline_lib/`). `pipeline_url/` handles URL imports, `pipeline_lib/` handles library imports, and `pipeline/` is legacy. See [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).
- **SQLite concurrency** — WAL mode with dual connection pools mitigates most issues, but very high concurrency may encounter locking.
- **Test coverage** — Integration and unit test coverage is incomplete.

---

## Roadmap

- [x] Single-port production mode (frontend + API on one port)
- [x] Windows desktop launcher with system tray
- [x] In-process background tasks (no Redis required)
- [x] Platform-appropriate data directories
- [ ] Windows installer (Inno Setup / NSIS)
- [ ] Consolidate pipeline implementations into a single unified pipeline
- [ ] Expand test coverage (unit + integration)
- [ ] PostgreSQL as a first-class database option
- [ ] Plugin system for additional metadata providers
- [ ] Mobile-responsive UI improvements
- [ ] Scheduled library scans and auto-import

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a pull request.

---

## Legal Disclaimer

Playarr is a self-hosted media management tool. It does **not** host, distribute, or provide access to copyrighted content.

- Users are solely responsible for the content they download and manage.
- Users must comply with all applicable laws and the terms of service of any third-party platforms (YouTube, Vimeo, etc.).
- Playarr integrates with third-party APIs (MusicBrainz, Wikipedia, AI providers) — users must comply with each service's terms and rate limits.
- The developers of Playarr are not responsible for any misuse of this software.

---

## License

This project is licensed under the [MIT License](LICENSE).

