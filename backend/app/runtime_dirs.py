"""
Playarr Runtime Directory Management.

Resolves platform-appropriate writable directories for an installed application.

Directory layout strategy:
  - DEVELOPMENT mode (PLAYARR_DEV=1 or running from repo):
      All data lives repo-relative (./data/, ./logs/, etc.) — current behavior.

  - PRODUCTION / INSTALLED mode:
      AppData directories are used so nothing writes into the install folder.

      Windows:
        config:     %APPDATA%/Playarr/config
        data:       %APPDATA%/Playarr/data
        db:         %APPDATA%/Playarr/data/playarr.db
        logs:       %APPDATA%/Playarr/logs
        cache:      %LOCALAPPDATA%/Playarr/cache
        previews:   %LOCALAPPDATA%/Playarr/cache/previews
        workspaces: %LOCALAPPDATA%/Playarr/cache/workspaces
        library:    user-configurable (default: ~/Music/Playarr)
        archive:    user-configurable (default: ~/Music/Playarr/archive)
"""
import os
import sys
from pathlib import Path
from typing import Optional


def _is_dev_mode() -> bool:
    """Detect whether Playarr is running in development mode."""
    # Explicit env override
    env = os.environ.get("PLAYARR_DEV", "").lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False

    # Heuristic: if we're inside a git repo with pyproject/requirements,
    # it's a dev checkout.
    backend_dir = Path(__file__).resolve().parent.parent
    if (backend_dir / "requirements.txt").is_file() and (backend_dir.parent / ".git").is_dir():
        return True

    return False


def _is_frozen() -> bool:
    """True when running from a PyInstaller / cx_Freeze bundle."""
    return getattr(sys, "frozen", False)


IS_DEV = _is_dev_mode()
IS_FROZEN = _is_frozen()


class RuntimeDirs:
    """Resolved directory paths for the current runtime environment."""

    def __init__(self):
        if IS_DEV:
            self._init_dev()
        else:
            self._init_production()

    # ── Dev mode (repo-relative) ──────────────────────────────────────────
    def _init_dev(self):
        backend = Path(__file__).resolve().parent.parent  # backend/
        repo = backend.parent

        self.config_dir = backend
        self.data_dir = repo / "data"
        self.db_path = backend / "playarr.db"
        self.log_dir = repo / "logs"
        self.cache_dir = repo / "data" / "cache"
        self.preview_dir = repo / "data" / "previews"
        self.workspace_dir = repo / "data" / "workspaces"
        self.asset_cache_dir = repo / "data" / "cache" / "assets"
        self.library_dir = repo / "data" / "library"
        self.archive_dir = repo / "data" / "archive"
        self.env_file = backend / ".env"

    # ── Production / installed mode ───────────────────────────────────────
    def _init_production(self):
        if os.name == "nt":
            appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            # Linux/macOS XDG
            appdata = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            local = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

        self.config_dir = appdata / "Playarr" / "config"
        self.data_dir = appdata / "Playarr" / "data"
        self.db_path = appdata / "Playarr" / "data" / "playarr.db"
        self.log_dir = appdata / "Playarr" / "logs"
        self.cache_dir = local / "Playarr" / "cache"
        self.preview_dir = local / "Playarr" / "cache" / "previews"
        self.workspace_dir = local / "Playarr" / "cache" / "workspaces"
        self.asset_cache_dir = local / "Playarr" / "cache" / "assets"
        self.env_file = appdata / "Playarr" / "config" / "playarr.conf"

        # User-configurable; defaults for first run
        default_music = Path.home() / "Music" / "Playarr"
        self.library_dir = default_music
        self.archive_dir = default_music / "archive"

    @property
    def database_url(self) -> str:
        """SQLAlchemy connection URL for the resolved DB path."""
        return f"sqlite:///{self.db_path}"

    @property
    def alembic_url(self) -> str:
        """Alembic-compatible database URL."""
        return self.database_url

    def ensure_all(self):
        """Create all required directories."""
        dirs = [
            self.config_dir,
            self.data_dir,
            self.log_dir,
            self.cache_dir,
            self.preview_dir,
            self.workspace_dir,
            self.asset_cache_dir,
            self.library_dir,
            self.archive_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


# Singleton
_dirs: Optional[RuntimeDirs] = None


def get_runtime_dirs() -> RuntimeDirs:
    global _dirs
    if _dirs is None:
        _dirs = RuntimeDirs()
    return _dirs
