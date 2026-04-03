# Playarr — Deployment Guide

This document covers all the ways to deploy and run Playarr.

---

## Deployment Modes

| Mode | Use Case | Redis? | Separate Frontend? | Port(s) |
|------|----------|--------|--------------------|---------|
| **Production (Desktop)** | Installed Windows app | No | No | 6969 |
| **Development** | Contributing / debugging | No | Yes (Vite) | 6969 + 3000 |
| **Docker** | Server / NAS | Yes | Yes (Nginx) | 6969 + 3080 |
| **Advanced** | High-throughput server | Yes + Celery | No | 6969 |

---

## 1. Production Desktop (Recommended for Users)

Single-process, single-port, no external dependencies beyond ffmpeg.

```bash
# Build frontend (one-time)
cd frontend && npm install && npm run build && cd ..

# Run
python run_playarr.py
# Or: start_playarr.bat (Windows)
```

See [RUN_PRODUCTION.md](RUN_PRODUCTION.md) for details.

---

## 2. Development Mode

For contributors working on the codebase.

```bash
# Terminal 1: Backend
cd backend
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
python _start_server.py

# Terminal 2: Frontend
cd frontend
npm install
npm run dev
```

Or use the convenience script:

```
start_dev.bat    # Opens both in separate windows
```

The frontend dev server (port 3000) proxies `/api` requests to the backend (port 6969).

---

## 3. Docker Compose

```bash
docker-compose up -d
```

Services: Redis (6379), API (6969), Celery Worker, Frontend/Nginx (3080).

Configure in `docker-compose.yml`:
- `LIBRARY_DIR`, `ARCHIVE_DIR`, `DOWNLOAD_DIR` environment variables
- Volume mounts for persistent data

---

## 4. Advanced Server Mode

For multi-worker setups with Redis + Celery:

```bash
# Redis
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Celery worker(s)
CELERY_WORKER_ENABLED=1 celery -A app.worker.celery_app worker --loglevel=info --concurrency=4

# API server
CELERY_WORKER_ENABLED=1 python run_playarr.py
```

---

## Environment Variables Reference

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `PLAYARR_DEV` | auto-detected | `1` = development mode (repo-relative paths) |
| `PLAYARR_PORT` | `6969` | Server port |
| `PLAYARR_HOST` | `0.0.0.0` | Bind address |
| `DATABASE_URL` | auto (see runtime_dirs) | SQLAlchemy connection string |

### Directories

| Variable | Dev Default | Production Default |
|----------|-----------|-------------------|
| `LIBRARY_DIR` | `./data/library` | `~/Music/Playarr` |
| `ARCHIVE_DIR` | `./data/archive` | `~/Music/Playarr/archive` |
| `LOG_DIR` | `./logs` | `%APPDATA%/Playarr/logs` |
| `PREVIEW_CACHE_DIR` | `./data/previews` | `%LOCALAPPDATA%/Playarr/cache/previews` |
| `ASSET_CACHE_DIR` | `./data/cache/assets` | `%LOCALAPPDATA%/Playarr/cache/assets` |

### Tools

| Variable | Default | Description |
|----------|---------|-------------|
| `FFMPEG_PATH` | `auto` | Path to ffmpeg or `auto` to search PATH |
| `FFPROBE_PATH` | `auto` | Path to ffprobe or `auto` to search PATH |
| `YTDLP_PATH` | `auto` | Path to yt-dlp or `auto` to search PATH |

### Background Processing

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection (only if Celery enabled) |
| `CELERY_WORKER_ENABLED` | `0` | Set to `1` to dispatch tasks via Celery |

### AI (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_PROVIDER` | `none` | `none`, `openai`, `gemini`, `claude`, `local` |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `CLAUDE_API_KEY` | — | Anthropic Claude API key |

---

## Port Summary

| Port | Service | When |
|------|---------|------|
| 6969 | Playarr (API + UI) | Always |
| 3000 | Vite dev server | Development only |
| 6379 | Redis | Docker / advanced mode only |
| 3080 | Nginx frontend | Docker only |
