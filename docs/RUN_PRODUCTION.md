# Playarr — Running in Production

This document explains how to run Playarr as a production application (single port, built frontend, no development tools required).

---

## Quick Start

```bash
# 1. Build the frontend
cd frontend
npm install
npm run build
cd ..

# 2. Start Playarr
python run_playarr.py
```

Or on Windows, use the batch scripts:

```
build_playarr.bat     # one-time: install deps + build frontend
start_playarr.bat     # start Playarr
```

Playarr will be available at **http://localhost:6969**.

---

## How It Works

In production mode, Playarr runs as a **single process on a single port**:

```
┌──────────────────────────────────────────────┐
│           Playarr (port 6969)                │
│                                              │
│  FastAPI serves:                             │
│    /api/*        → REST API endpoints        │
│    /assets/*     → JS/CSS/images (built)     │
│    /*            → React SPA (index.html)    │
│                                              │
│  Background tasks run in-process via threads │
│  SQLite database (no external DB required)   │
└──────────────────────────────────────────────┘
```

- **No Nginx** — FastAPI serves the built frontend directly
- **No Redis** — background tasks run in-process via threads
- **No separate frontend server** — Vite build output is served by the backend
- **No Celery worker** — the thread-based executor handles all pipeline tasks

---

## Prerequisites

| Dependency | Required | Notes |
|-----------|----------|-------|
| Python 3.10+ | Yes | 3.12 recommended |
| ffmpeg + ffprobe | Yes | Must be on PATH |
| yt-dlp | For URL imports | `pip install yt-dlp` or standalone binary |
| Node.js 18+ | Build only | Only needed to build the frontend |
| Redis | No | Only for advanced multi-worker setups |

---

## Command-Line Options

```
python run_playarr.py [options]

  --port PORT     Port to listen on (default: 6969)
  --host HOST     Host to bind to (default: 0.0.0.0)
  --delay N       Wait N seconds before starting (for Windows startup)
  --headless      No system tray icon or auto-browser
```

Environment variables:

```
PLAYARR_PORT=6969       Override default port
PLAYARR_HOST=0.0.0.0   Override bind address
PLAYARR_DEV=1           Force development mode (repo-relative paths)
```

---

## Data Directories

### Development Mode (PLAYARR_DEV=1 or running from git repo)

All data is stored repo-relative:

| Directory | Path |
|-----------|------|
| Database | `backend/playarr.db` |
| Library | `data/library/` |
| Archive | `data/archive/` |
| Logs | `logs/` |
| Cache | `data/cache/` |
| Previews | `data/previews/` |
| Workspaces | `data/workspaces/` |
| Config | `backend/.env` |

### Production / Installed Mode

Data is stored in platform-appropriate AppData directories:

| Directory | Windows Path |
|-----------|-------------|
| Config | `%APPDATA%\Playarr\config\` |
| Database | `%APPDATA%\Playarr\data\playarr.db` |
| Logs | `%APPDATA%\Playarr\logs\` |
| Cache | `%LOCALAPPDATA%\Playarr\cache\` |
| Previews | `%LOCALAPPDATA%\Playarr\cache\previews\` |
| Workspaces | `%LOCALAPPDATA%\Playarr\cache\workspaces\` |
| Library | `~/Music/Playarr/` (user-configurable) |
| Archive | `~/Music/Playarr/archive/` (user-configurable) |

Library and archive directories are user-configurable in **Settings > Library** within the UI.

---

## Startup Behavior

On every start, Playarr:

1. **Validates environment** — checks Python version, ffmpeg, ffprobe, yt-dlp
2. **Creates directories** — all required dirs are auto-created
3. **Initializes database** — creates tables on first run, applies schema upgrades
4. **Stamps version** — records app version in DB, warns on version mismatch
5. **Cleans up** — purges orphan jobs, workspaces, cached assets, previews
6. **Runs artwork repair** — validates cached artwork integrity
7. **Starts watchdog** — background task monitors for stuck jobs

---

## Advanced: Redis + Celery Mode

For high-throughput setups (multiple concurrent imports), enable Celery:

```bash
# Terminal 1: Start Redis
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Terminal 2: Start Celery worker
cd backend
celery -A app.worker.celery_app worker --loglevel=info --pool=solo

# Terminal 3: Start Playarr with Celery enabled
CELERY_WORKER_ENABLED=1 python run_playarr.py
```

This is **not required** for normal desktop use.

---

## Stopping Playarr

- **From UI**: Settings > System > Restart / Stop
- **From terminal**: Ctrl+C in the terminal running `run_playarr.py`
- **From tray**: Right-click tray icon > Quit
