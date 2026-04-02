# Playarr

A music video manager inspired by Sonarr/Radarr/Lidarr — download, organize, normalize, and browse your music video library.

## Features

- **Download & Process** music videos from YouTube and Vimeo via yt-dlp
- **Manage Metadata** with Wikipedia scraping, MusicBrainz lookups, and Kodi-ready .nfo generation
- **Browser Playback** with a music-library browsing experience (artists, years, genres, albums)
- **Audio Normalization** using LUFS-based loudness measurement and gain adjustment
- **Quality Upgrades** — automatically detect and replace lower-quality versions
- **Hover Previews** — short looping preview clips on hover

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Reverse Proxy                     │
│                  (Caddy / Nginx)                     │
├──────────────────┬──────────────────────────────────┤
│   Frontend (SPA) │        Backend API               │
│   React + Vite   │     FastAPI (Python)             │
│   Port 3000      │     Port 8000                    │
├──────────────────┴──────────────────────────────────┤
│              Background Workers                      │
│           Celery + Redis (broker)                    │
├─────────────────────────────────────────────────────┤
│               Data Layer                             │
│    SQLite (dev) / PostgreSQL (prod)                  │
│    Redis (cache + job broker)                        │
├─────────────────────────────────────────────────────┤
│             File System                              │
│   Library Dir │ Archive Dir │ Preview Cache          │
└─────────────────────────────────────────────────────┘
```

## Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| API | FastAPI | Async, typed, auto OpenAPI docs |
| Workers | Celery + Redis | Reliable job queue, retries, chaining |
| DB | SQLAlchemy + Alembic | ORM + migrations, SQLite dev / Postgres prod |
| Frontend | React + Vite + TailwindCSS | Fast dev, rich component ecosystem |
| Downloader | yt-dlp | Best YouTube/Vimeo support |
| Media | ffmpeg + ffprobe | Industry standard media processing |
| Metadata | MusicBrainz API + Wikipedia scraping | Comprehensive music metadata |

## Quick Start

```bash
# Backend
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload

# Worker
celery -A app.worker worker --loglevel=info

# Frontend
cd frontend
npm install
npm run dev
```

## Configuration

Copy `.env.example` to `.env` and configure:

```env
LIBRARY_DIR=D:\MusicVideos\Library
ARCHIVE_DIR=D:\MusicVideos\Archive
FFMPEG_PATH=auto               # auto-detect or absolute path
FFPROBE_PATH=auto
NORMALIZATION_TARGET_LUFS=-14.0
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=sqlite:///./playarr.db
```

## License

MIT
