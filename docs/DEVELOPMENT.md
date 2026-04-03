# Development Guide

Guide for developers contributing to Playarr.

---

## Project Structure

```
playarr/
├── backend/                   # Python FastAPI backend
│   ├── app/                   # Main application package
│   │   ├── main.py            # FastAPI app + lifespan
│   │   ├── config.py          # Settings (pydantic-settings)
│   │   ├── models.py          # SQLAlchemy ORM models
│   │   ├── routers/           # API route handlers
│   │   ├── services/          # Business logic
│   │   ├── scraper/           # Metadata scraping
│   │   ├── ai/                # AI provider abstraction
│   │   ├── pipeline_url/      # URL import pipeline (active)
│   │   ├── pipeline_lib/      # Library import pipeline (active)
│   │   └── pipeline/          # Legacy pipeline (do not modify)
│   ├── tests/                 # Test files
│   ├── alembic/               # Database migrations
│   ├── requirements.txt
│   └── .env.example
├── frontend/                  # React 19 + Vite 7 + Tailwind CSS 4
│   ├── src/
│   │   ├── pages/             # Route pages
│   │   ├── components/        # Reusable components
│   │   ├── hooks/             # React Query hooks
│   │   ├── stores/            # Zustand state
│   │   ├── lib/api.ts         # Typed API client
│   │   └── index.css          # Theme + utility CSS
│   └── package.json
├── docker-compose.yml
├── Dockerfile
└── nginx.conf
```

---

## Running the Development Environment

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS/Linux
pip install -r requirements.txt

# Start the server (auto-restarts on Settings > Restart)
python _start_server.py

# Or directly with auto-reload:
uvicorn app.main:app --host 0.0.0.0 --port 6969 --reload
```

The backend serves on `http://localhost:6969`. API docs are at `http://localhost:6969/docs`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server runs on `http://localhost:3000` with HMR. It proxies `/api` requests to the backend.

### Redis

```bash
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

If Redis isn't available, the backend uses an in-process fallback thread for background tasks.

---

## Code Conventions

### Backend (Python)

- **Framework:** FastAPI with type hints
- **ORM:** SQLAlchemy 2.0+ (new-style queries)
- **Config:** pydantic-settings via `.env` file
- **Logging:** Python `logging` module, logger per module
- **Imports:** Standard library → third-party → local, separated by blank lines
- **Database sessions:** Use `get_db()` dependency injection, never hold sessions across async boundaries

### Frontend (TypeScript)

- **Framework:** React 19 with function components and hooks
- **State:** Zustand for global state, React Query for server state
- **Styling:** Tailwind CSS 4 with custom theme in `index.css`
- **API calls:** All through `lib/api.ts` typed client
- **Icons:** Lucide React

### General

- No trailing whitespace
- Files end with a newline
- Prefer composition over inheritance

---

## Database

SQLite is the default database. The schema is managed in two ways:

1. **`Base.metadata.create_all()`** — Creates tables on startup (dev convenience)
2. **Alembic** — Formal migrations for production

When adding new columns or tables:
- Add them to `models.py`
- If needed, add a schema upgrade in `main.py → _apply_schema_upgrades()`
- For production, create an Alembic migration

The database uses WAL mode with dual connection pools (main + cosmetic) for concurrency.

---

## Pipeline Architecture

There are three pipeline directories — this is a known technical debt:

| Directory | Status | Used By |
|-----------|--------|---------|
| `pipeline_url/` | **Active** | URL imports (YouTube, Vimeo) |
| `pipeline_lib/` | **Active** | Library file imports |
| `pipeline/` | **Legacy** | Not actively used; superseded |

**Do not modify `pipeline/` without a refactor plan.** See `docs/KNOWN_ISSUES.md`.

When making changes to import logic, ensure both `pipeline_url/` and `pipeline_lib/` are updated if the change applies to both pathways.

---

## Adding a New API Endpoint

1. Add the route handler in the appropriate `routers/*.py` file
2. Add Pydantic schemas to `schemas.py` if needed
3. Register the router in `main.py` if it's a new router file
4. Add the API method to `frontend/src/lib/api.ts`
5. Add React Query hooks in `frontend/src/hooks/queries.ts` if applicable

---

## Adding a New Setting

1. Add the default to `DEFAULT_SETTINGS` in `routers/settings.py`
2. Add the `SETTING_META` entry in `frontend/src/pages/SettingsPage.tsx`
3. Assign it to the appropriate `group` (library, server, import, etc.)
4. If it needs a config.py backing field, add it to `Settings` in `config.py`

---

## Testing

```bash
cd backend
python -m pytest tests/ -v
```

Test coverage is currently incomplete. When adding new features, include tests where feasible.

---

## Building for Production

### Frontend

```bash
cd frontend
npm run build
```

Output goes to `frontend/dist/`. Serve with any static file server or the Docker Nginx container.

### Docker

```bash
docker-compose up -d --build
```

---

## Useful Commands

```bash
# Check for Python syntax errors
python -c "import py_compile; py_compile.compile('app/main.py', doraise=True)"

# TypeScript type-check without building
cd frontend && npx tsc --noEmit

# Format check
cd frontend && npx eslint .

# Database shell
sqlite3 data/playarr.db

# View API docs
open http://localhost:6969/docs
```
