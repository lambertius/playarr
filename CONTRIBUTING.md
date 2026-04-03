# Contributing to Playarr

Thanks for your interest in contributing! This document explains how to get started.

---

## Getting Started

### Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.10+ (3.12 recommended) |
| Node.js | 18+ (20 LTS recommended) |
| Redis | 6+ |
| ffmpeg | 5+ (includes ffprobe) |
| yt-dlp | 2024+ |

### Running Locally

```bash
# Clone the repo
git clone https://github.com/lambertius/playarr.git
cd playarr

# Backend
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS/Linux
pip install -r requirements.txt
cp .env.example .env           # Edit .env with your paths
python _start_server.py

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

See [docs/SETUP.md](docs/SETUP.md) for detailed instructions.

---

## How to Contribute

### Reporting Bugs

- Open a [GitHub Issue](../../issues) with a clear title and description.
- Include steps to reproduce, expected vs. actual behaviour, and your environment (OS, Python version, etc.).
- Attach relevant log output if available (check `backend/logs/`).

### Suggesting Features

- Open an issue with the `enhancement` label.
- Describe the use case and how you'd expect the feature to work.

### Submitting Pull Requests

1. Fork the repository and create a feature branch from `main`.
2. Make your changes in small, focused commits.
3. Ensure the backend starts without errors (`python _start_server.py`).
4. Ensure the frontend builds without errors (`npm run build`).
5. Run existing tests if applicable (`python -m pytest tests/ -v`).
6. Open a PR against `main` with a clear description of what changed and why.

---

## Code Style

### Python (Backend)

- Type hints on function signatures.
- One logger per module (`logger = logging.getLogger(__name__)`).
- Import order: standard library, third-party, local — separated by blank lines.
- Use `get_db()` dependency injection for database sessions.

### TypeScript (Frontend)

- Function components with hooks.
- API calls through `lib/api.ts`.
- Tailwind CSS for styling — no inline style objects.

### General

- No trailing whitespace. Files end with a newline.
- Keep PRs focused — one logical change per PR.

---

## Architecture Notes

- `pipeline_url/` handles URL imports (YouTube, Vimeo) — **active**.
- `pipeline_lib/` handles library file imports — **active**.
- `pipeline/` is legacy and should not be modified without a refactor plan.

See [ARCHITECTURE.md](ARCHITECTURE.md) for full details.

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
