"""
Playarr Configuration - Settings management via pydantic-settings.
All settings can be overridden via environment variables or .env file.

In production / installed mode, directories default to platform-appropriate
AppData locations (see runtime_dirs.py).  In development mode (PLAYARR_DEV=1
or detected git repo), dirs remain repo-relative for backward-compat.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from functools import lru_cache
from typing import Optional, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.runtime_dirs import get_runtime_dirs, IS_DEV

# Resolve env file path from runtime dirs (may be repo .env or AppData config)
_rdirs = get_runtime_dirs()

# Critical subdirectories created inside every library/source directory
CRITICAL_SUBDIRS = ("_albums", "_artists", "_archive", "_PlayarrCache")


def ensure_library_subdirs(library_root: str) -> None:
    """Create critical subdirectories inside a library root if they don't exist."""
    for sub in CRITICAL_SUBDIRS:
        os.makedirs(os.path.join(library_root, sub), exist_ok=True)


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=str(_rdirs.env_file) if _rdirs.env_file.is_file() else ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Mode ---
    playarr_dev: bool = IS_DEV

    # --- Directories ---
    library_dir: str = str(_rdirs.library_dir)
    library_source_dirs: str = ""  # JSON list of additional source dirs, e.g. '["/mnt/videos"]'

    # --- Library Naming Convention ---
    library_naming_pattern: str = "{artist} - {title} [{quality}]"
    library_folder_structure: str = "{artist}/{file_folder}"
    preview_cache_dir: str = str(_rdirs.preview_dir)
    artist_root: str = ""   # blank = <library_dir>/_artists
    album_root: str = ""    # blank = <library_dir>/_albums
    log_dir: str = str(_rdirs.log_dir)

    @property
    def archive_dir(self) -> str:
        """Archive is always _archive inside the library directory."""
        return os.path.join(self.library_dir, "_archive")

    @property
    def asset_cache_dir(self) -> str:
        """Asset cache lives inside _PlayarrCache in the library directory."""
        return os.path.join(self.library_dir, "_PlayarrCache")

    # --- Tool Paths ---
    ffmpeg_path: str = "auto"
    ffprobe_path: str = "auto"
    ytdlp_path: str = "auto"

    # --- Audio Normalization ---
    normalization_target_lufs: float = -14.0
    normalization_lra: float = 7.0
    normalization_tp: float = -1.5

    # --- Database ---
    database_url: str = _rdirs.database_url

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- MusicBrainz ---
    musicbrainz_app: str = "Playarr"
    musicbrainz_version: str = "1.0.0"
    musicbrainz_contact: str = "user@example.com"

    # --- AI Summaries ---
    ai_provider: Literal["none", "gemini", "openai", "claude", "local"] = "none"
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    claude_api_key: Optional[str] = None
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "llama3"

    # --- Preview Generation ---
    preview_duration_sec: int = 8
    preview_start_percent: int = 30

    # --- Artwork Cache ---
    # Startup repair mode: "light" (quick validation of recently-used/suspicious
    # assets), "full" (validate every cached asset, re-download invalid ones),
    # or "off" (skip startup repair entirely).
    # "light" is recommended — keeps startup fast on large libraries while
    # still catching the most common corrupt-cache issues.
    startup_repair_mode: Literal["off", "light", "full"] = "light"

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 6969

    def resolve_tool_path(self, setting_value: str, tool_name: str) -> str:
        """Resolve 'auto' tool paths by searching PATH, or return explicit path."""
        if setting_value.lower() == "auto":
            found = shutil.which(tool_name)
            if found:
                return found
            # Check adjacent to the executable (bundled installs)
            bundled_paths = []
            if getattr(sys, "frozen", False):
                exe_dir = os.path.dirname(sys.executable)
                bundled_paths.append(os.path.join(exe_dir, f"{tool_name}.exe"))
                bundled_paths.append(os.path.join(exe_dir, "tools", f"{tool_name}.exe"))
            # Try common Windows locations
            common_paths = [
                os.path.join(os.environ.get("APPDATA", ""), f"{tool_name}.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), f"{tool_name}.exe"),
                rf"C:\ffmpeg\bin\{tool_name}.exe",
            ]
            for p in bundled_paths + common_paths:
                if os.path.isfile(p):
                    return p
            raise FileNotFoundError(
                f"Could not auto-detect {tool_name}. "
                f"Set {tool_name.upper()}_PATH in your .env or install {tool_name} on PATH."
            )
        path = Path(setting_value)
        if not path.is_file():
            raise FileNotFoundError(f"{tool_name} not found at configured path: {setting_value}")
        return str(path)

    @property
    def resolved_ffmpeg(self) -> str:
        return self.resolve_tool_path(self.ffmpeg_path, "ffmpeg")

    @property
    def resolved_ffprobe(self) -> str:
        return self.resolve_tool_path(self.ffprobe_path, "ffprobe")

    @property
    def resolved_ytdlp(self) -> str:
        return self.resolve_tool_path(self.ytdlp_path, "yt-dlp")

    def get_all_library_dirs(self) -> list[str]:
        """Return [library_dir] + any additional source directories."""
        import json
        dirs = [self.library_dir]
        if self.library_source_dirs.strip():
            try:
                extra = json.loads(self.library_source_dirs)
                if isinstance(extra, list):
                    dirs.extend(str(d) for d in extra if d)
            except (json.JSONDecodeError, TypeError):
                pass
        return dirs

    def ensure_directories(self):
        """Create required directories if they don't exist."""
        for d in [self.library_dir, self.archive_dir, self.preview_cache_dir,
                  self.asset_cache_dir, self.log_dir]:
            os.makedirs(d, exist_ok=True)
        # Ensure critical subdirectories for all library dirs
        ensure_library_subdirs(self.library_dir)
        for d in self.get_all_library_dirs()[1:]:  # skip library_dir (already done)
            ensure_library_subdirs(d)
        # Runtime dirs (workspace, cache) managed by runtime_dirs module
        _rdirs.ensure_all()

    @property
    def workspace_dir(self) -> str:
        """Directory for temporary import workspaces."""
        return str(_rdirs.workspace_dir)

    @property
    def data_dir(self) -> str:
        """Root data directory."""
        return str(_rdirs.data_dir)


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton."""
    settings = Settings()
    settings.ensure_directories()
    return settings
