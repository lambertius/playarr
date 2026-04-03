# Setup Guide

Step-by-step instructions for setting up Playarr from a fresh clone.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.10+ (3.12 recommended) | [python.org](https://www.python.org/downloads/) |
| Node.js | 18+ (20 LTS recommended) | [nodejs.org](https://nodejs.org/) |
| ffmpeg | 5+ (includes ffprobe) | [ffmpeg.org](https://ffmpeg.org/download.html) |
| yt-dlp | 2024+ | `pip install yt-dlp` or [github.com/yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Redis | 6+ *(optional)* | Docker or [redis.io](https://redis.io/download) |

### Verify prerequisites

```bash
python --version    # 3.10+
node --version      # 18+
ffmpeg -version     # should print version
ffprobe -version    # should print version
yt-dlp --version    # should print version
redis-cli ping      # should respond PONG (optional — only if using Redis)
```

---

## 1. Clone the Repository

```bash
git clone https://github.com/lambertius/playarr.git
cd playarr
```

---

## 2. Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 3. Configure Environment

```bash
# Copy the template
cp .env.example .env
```

Edit `.env` to customise paths if needed. All directories have sensible defaults — you only need to override them if you want data stored elsewhere:

```env
# Optional: override default library location
LIBRARY_DIR=/path/to/your/music-videos
ARCHIVE_DIR=/path/to/your/archive
PREVIEW_CACHE_DIR=/path/to/your/previews
```

Playarr creates these directories automatically on startup if they don't exist.

### Tool paths

By default, `FFMPEG_PATH`, `FFPROBE_PATH`, and `YTDLP_PATH` are set to `auto`, which searches your system PATH. If the tools aren't on PATH, set absolute paths in `.env`.

### AI (optional)

To enable AI features, set `AI_PROVIDER` to one of `openai`, `gemini`, `claude`, or `local`, and provide the corresponding API key. AI features are entirely optional — Playarr works fully without them.

---

## 4. (Optional) Start Redis

Redis enables Celery-based background task processing. **It is not required** — without Redis, Playarr uses an in-process thread worker that is perfectly fine for desktop/single-user use.

If you want Redis:

```bash
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

Or install Redis natively for your OS and run `redis-server`.

---

## 5. Start the Backend

```bash
# From the backend/ directory with venv activated
python _start_server.py
```

This starts Uvicorn on port 6969. The server automatically:
- Creates the SQLite database on first run
- Applies any pending schema upgrades
- Creates required directories

You can also start directly:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 6969
```

Verify the backend is running:

```
http://localhost:6969/api/health
```

---

## 6. Start the Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server starts on `http://localhost:3000` and proxies API requests to the backend.

Open `http://localhost:3000` in your browser.

---

## 7. (Optional) Start Celery Worker

For production-like background task processing:

```bash
# From backend/ with venv activated
celery -A app.worker.celery_app worker --loglevel=info --pool=solo
```

---

## Docker Setup

As an alternative to the manual setup above:

```bash
# From the project root
docker-compose up -d
```

This starts Redis, the API server, a Celery worker, and an Nginx frontend. Configure volumes in `docker-compose.yml` to point to your library directories.

| Service | Port | URL |
|---------|------|-----|
| API | 6969 | http://localhost:6969 |
| Frontend | 3080 | http://localhost:3080 |
| Redis | 6379 | — |

---

## First Steps After Setup

1. Go to **Settings > Library** and verify your library directory path
2. Click **Add Video** and paste a YouTube music video URL
3. Watch the import pipeline process and organise the video
4. Browse your library in the main grid view

---

## Troubleshooting

### `ffmpeg not found`
Set `FFMPEG_PATH` in `.env` to the absolute path of your ffmpeg binary, or add it to your system PATH.

### `Redis connection refused`
Ensure Redis is running on the configured URL. Check with `redis-cli ping`.

### Database locked errors
Ensure only one Uvicorn process is running. SQLite WAL mode handles concurrency, but multiple server instances can conflict.

### Frontend can't reach backend
The Vite dev server proxies `/api` to `http://localhost:6969`. Ensure the backend is running on that port.
