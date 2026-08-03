"""
yt-dlp self-update service.

yt-dlp tracks YouTube's frequently-changing player, so a build that ships with
the app goes stale within weeks (symptom: high-res formats disappear and only
360p is downloadable). This lets an *installed* Playarr upgrade yt-dlp on its
own, without waiting for a full app release.

The managed binary is written to a user-writable tools dir (see
``RuntimeDirs.tools_dir``) which ``Settings.resolved_ytdlp`` prefers over PATH,
so an update takes effect immediately for every download path.
"""
import logging
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

import httpx

from app.config import get_settings
from app.runtime_dirs import get_runtime_dirs

logger = logging.getLogger(__name__)

_GITHUB_API_LATEST = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
_last_checked_at: Optional[str] = None
_latest_version: Optional[str] = None
_last_check_monotonic = 0.0
_check_in_progress = False
_check_lock = threading.Lock()
_STATUS_TTL_SECONDS = 30 * 60


def _subprocess_kwargs() -> dict:
    """Hide the console window on Windows (mirrors downloader.py)."""
    kwargs: dict = {"stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _asset_name() -> str:
    """Release asset filename for the current platform."""
    system = platform.system()
    if system == "Windows":
        return "yt-dlp.exe"
    if system == "Darwin":
        return "yt-dlp_macos"
    return "yt-dlp"  # Linux — single-file binary


def managed_ytdlp_path() -> str:
    """Absolute path where the self-managed yt-dlp binary lives."""
    tools_dir = get_runtime_dirs().tools_dir
    return str(tools_dir / _asset_name())


def is_managed() -> bool:
    """True when the yt-dlp currently in use is the self-managed one."""
    try:
        return os.path.samefile(get_settings().resolved_ytdlp, managed_ytdlp_path())
    except (FileNotFoundError, OSError):
        return False


def resolved_path() -> Optional[str]:
    """The yt-dlp path currently resolved, or None if none is available."""
    try:
        return get_settings().resolved_ytdlp
    except FileNotFoundError:
        return None


def get_installed_version() -> Optional[str]:
    """Return the version string of the resolved yt-dlp, or None if absent."""
    path = resolved_path()
    if not path:
        return None
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True, text=True, timeout=30,
            **_subprocess_kwargs(),
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception as e:
        logger.warning(f"Failed to read yt-dlp version: {e}")
    return None


def get_latest_version() -> Optional[str]:
    """Return the latest yt-dlp release tag from GitHub, or None on failure."""
    global _last_checked_at, _latest_version, _last_check_monotonic
    try:
        resp = httpx.get(
            _GITHUB_API_LATEST,
            timeout=15,
            headers={"Accept": "application/vnd.github+json"},
        )
        if resp.status_code != 200:
            logger.warning(f"yt-dlp release check returned HTTP {resp.status_code}")
            return None
        tag = resp.json().get("tag_name", "").strip()
        if tag:
            _latest_version = tag
        return tag or None
    except Exception as e:
        logger.warning(f"Failed to check latest yt-dlp version: {e}")
        return None
    finally:
        _last_checked_at = datetime.now(timezone.utc).isoformat()
        _last_check_monotonic = time.monotonic()


def _refresh_latest_in_background() -> None:
    global _check_in_progress
    try:
        get_latest_version()
    finally:
        with _check_lock:
            _check_in_progress = False


def _ensure_latest_check() -> None:
    """Start at most one non-blocking release check when the cache is stale."""
    global _check_in_progress
    fresh = _last_check_monotonic and (
        time.monotonic() - _last_check_monotonic < _STATUS_TTL_SECONDS
    )
    if fresh:
        return
    with _check_lock:
        if _check_in_progress:
            return
        _check_in_progress = True
    threading.Thread(
        target=_refresh_latest_in_background,
        daemon=True,
        name="ytdlp-version-check",
    ).start()


def get_status() -> dict:
    """Return local/cached status immediately and refresh remote state async."""
    installed = get_installed_version()
    _ensure_latest_check()
    latest = _latest_version
    # yt-dlp versions are date-based (YYYY.MM.DD) and sort lexically, so a plain
    # string compare is a reliable "is newer" test.
    update_available = bool(installed and latest and latest > installed)
    if installed is None:
        # Nothing installed yet — offer to install if we know a target.
        update_available = latest is not None
    return {
        "installed_version": installed,
        "latest_version": latest,
        "update_available": update_available,
        "managed": is_managed(),
        "path": resolved_path(),
        "managed_path": managed_ytdlp_path(),
        "last_checked_at": _last_checked_at,
    }


def update(target_version: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    """Download the latest yt-dlp into the managed tools dir.

    Returns ``(success, message, new_version)``. The download is atomic: it
    writes to a temp file first and only swaps it into place once verified.
    """
    tag = target_version or get_latest_version()
    if not tag:
        return False, "Could not determine the latest yt-dlp version (GitHub unreachable?).", None

    asset = _asset_name()
    url = f"https://github.com/yt-dlp/yt-dlp/releases/download/{tag}/{asset}"
    dest = managed_ytdlp_path()
    tools_dir = os.path.dirname(dest)
    os.makedirs(tools_dir, exist_ok=True)

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".part", dir=tools_dir)
    os.close(tmp_fd)
    try:
        logger.info(f"Downloading yt-dlp {tag} from {url}")
        with httpx.stream("GET", url, timeout=120, follow_redirects=True) as resp:
            if resp.status_code != 200:
                return False, f"Download failed (HTTP {resp.status_code}) for {url}", None
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                    f.write(chunk)

        if os.name != "nt":
            os.chmod(tmp_path, 0o755)

        # Verify the freshly downloaded binary actually runs before swapping.
        try:
            verify = subprocess.run(
                [tmp_path, "--version"],
                capture_output=True, text=True, timeout=30,
                **_subprocess_kwargs(),
            )
            new_version = verify.stdout.strip() if verify.returncode == 0 else None
        except Exception as e:
            return False, f"Downloaded yt-dlp failed to run: {e}", None
        if not new_version:
            return False, "Downloaded yt-dlp did not report a version — aborting.", None

        os.replace(tmp_path, dest)  # atomic on same filesystem
        logger.info(f"yt-dlp updated to {new_version} at {dest}")
        return True, f"yt-dlp updated to {new_version}.", new_version
    except Exception as e:
        logger.error(f"yt-dlp update failed: {e}")
        return False, f"yt-dlp update failed: {e}", None
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
