"""
Playarr Production Launcher — single entry point for installed use.

This module:
  1. Validates the runtime environment (Python, ffmpeg, etc.)
  2. Ensures required directories exist
  3. Runs database migrations / schema initialization
  4. Starts the FastAPI server via uvicorn
  5. Optionally starts the system tray icon
  6. Opens the browser on first successful start

Usage:
    python run_playarr.py                  # normal launch
    python run_playarr.py --delay 5        # delayed start (e.g. Windows startup)
    python run_playarr.py --headless       # no tray icon or browser open
    python run_playarr.py --port 8080      # custom port

Environment variables:
    PLAYARR_DEV=1         Force development mode (repo-relative paths)
    PLAYARR_PORT=6969     Override default port
    PLAYARR_HOST=0.0.0.0  Override listen address
"""
import argparse
import logging
import os
import shutil
import sys
import subprocess
import time

# ---------------------------------------------------------------------------
# Resolve paths BEFORE any app imports
# ---------------------------------------------------------------------------
_IS_FROZEN = getattr(sys, "frozen", False)

# Windowed mode (console=False) sets stdout/stderr to None — redirect to
# devnull so logging / uvicorn don't crash on .isatty() / .write() calls.
if _IS_FROZEN:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

if _IS_FROZEN:
    # PyInstaller bundle — exe lives in dist/Playarr/, code in _internal/
    _SCRIPT_DIR = os.path.dirname(sys.executable)
    _BACKEND_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    _BACKEND_DIR = os.path.join(_SCRIPT_DIR, "backend") if os.path.isdir(os.path.join(_SCRIPT_DIR, "backend")) else _SCRIPT_DIR

# Ensure backend is on sys.path
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
os.chdir(_BACKEND_DIR)


RESTART_EXIT_CODE = 75
logger = logging.getLogger("playarr.launcher")


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------
class StartupError:
    """Collects validation failures."""

    def __init__(self, name: str, message: str, fatal: bool = True):
        self.name = name
        self.message = message
        self.fatal = fatal


def validate_environment(port: int) -> list[StartupError]:
    """Check that required tools and directories are available."""
    errors: list[StartupError] = []

    # Python version
    if sys.version_info < (3, 10):
        errors.append(StartupError(
            "python",
            f"Python 3.10+ required (found {sys.version})",
            fatal=True,
        ))

    # ffmpeg (not fatal — app starts but video processing won't work)
    def _find_tool(name: str) -> bool:
        if shutil.which(name):
            return True
        # Check bundled location next to exe
        if _IS_FROZEN:
            for candidate in [
                os.path.join(_SCRIPT_DIR, f"{name}.exe"),
                os.path.join(_SCRIPT_DIR, "tools", f"{name}.exe"),
            ]:
                if os.path.isfile(candidate):
                    return True
        return False

    if not _find_tool("ffmpeg"):
        errors.append(StartupError(
            "ffmpeg",
            "ffmpeg not found on PATH. Video processing will not work until ffmpeg is installed.",
            fatal=False,
        ))

    # ffprobe
    if not _find_tool("ffprobe"):
        errors.append(StartupError(
            "ffprobe",
            "ffprobe not found on PATH. It is usually included with ffmpeg.",
            fatal=False,
        ))

    # yt-dlp (not fatal — library import works without it)
    if not _find_tool("yt-dlp"):
        errors.append(StartupError(
            "yt-dlp",
            "yt-dlp not found on PATH. URL imports will not work until yt-dlp is installed.",
            fatal=False,
        ))

    # Check port availability
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", port))
            if result == 0:
                errors.append(StartupError(
                    "port",
                    f"Port {port} is already in use. Is another Playarr instance running?",
                    fatal=True,
                ))
    except Exception:
        pass

    return errors


def ensure_first_run_setup():
    """Create directories, initialize DB, run migrations on first start."""
    from app.runtime_dirs import get_runtime_dirs

    dirs = get_runtime_dirs()
    dirs.ensure_all()

    # If DB doesn't exist, it will be created by SQLAlchemy on first connect.
    # Schema upgrades happen in main.py lifespan, so no extra work needed here.
    logger.info(f"Data directory: {dirs.data_dir}")
    logger.info(f"Database: {dirs.db_path}")
    logger.info(f"Logs: {dirs.log_dir}")


def _read_db_bool(key: str, default: str = "false") -> bool:
    """Read a boolean setting from the SQLite DB (best-effort)."""
    try:
        import sqlite3
        from app.runtime_dirs import get_runtime_dirs
        db_path = str(get_runtime_dirs().db_path)
        if not os.path.exists(db_path):
            return default == "true"
        conn = sqlite3.connect(db_path, timeout=2)
        cur = conn.execute(
            "SELECT value FROM app_settings WHERE key = ? AND user_id IS NULL", (key,)
        )
        row = cur.fetchone()
        conn.close()
        return (row[0] if row else default).lower() == "true"
    except Exception:
        return default == "true"


# ---------------------------------------------------------------------------
# Tray icon (optional)
# ---------------------------------------------------------------------------
try:
    from tray import start_tray, stop_tray
except ImportError:
    # Running from repo root — try backend/tray.py
    try:
        sys.path.insert(0, _BACKEND_DIR)
        from tray import start_tray, stop_tray
    except ImportError:
        start_tray = stop_tray = lambda *a, **kw: None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Playarr — Music Video Manager",
        prog="Playarr",
    )
    parser.add_argument("--delay", type=int, default=0,
                        help="Seconds to wait before starting (for Windows startup)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PLAYARR_PORT", "6969")),
                        help="Port to listen on (default: 6969)")
    parser.add_argument("--host", type=str, default=os.environ.get("PLAYARR_HOST", "0.0.0.0"),
                        help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--headless", action="store_true",
                        help="No tray icon or auto-browser")
    args = parser.parse_args()

    # Banner
    print("=" * 50)
    print("  Playarr — Music Video Manager")
    print(f"  http://localhost:{args.port}")
    print("=" * 50)

    # Delayed start (Windows startup integration)
    if args.delay > 0:
        print(f"  Waiting {args.delay}s before starting...")
        time.sleep(args.delay)

    # Validate environment
    errors = validate_environment(args.port)
    fatal_errors = [e for e in errors if e.fatal]
    warnings = [e for e in errors if not e.fatal]

    for w in warnings:
        print(f"  WARNING: [{w.name}] {w.message}")

    if fatal_errors:
        print()
        print("  STARTUP FAILED — the following issues must be resolved:")
        for e in fatal_errors:
            print(f"    ERROR: [{e.name}] {e.message}")
        print()
        print("  See docs/SETUP.md for installation instructions.")
        sys.exit(1)

    # First-run setup (directories, DB)
    ensure_first_run_setup()

    # System tray
    if not args.headless:
        minimize_to_tray = _read_db_bool("minimize_to_tray", "true")
        if minimize_to_tray:
            start_tray(port=args.port)

        auto_open = _read_db_bool("auto_open_browser", "true")
        if auto_open:
            import threading
            import webbrowser

            def _open():
                time.sleep(3)
                webbrowser.open(f"http://localhost:{args.port}")

            threading.Thread(target=_open, daemon=True).start()

    # Run uvicorn
    if _IS_FROZEN:
        # PyInstaller bundle — run uvicorn in-process (subprocess won't work)
        import uvicorn
        uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
    else:
        # Development / non-frozen — subprocess allows restart via exit code
        while True:
            result = subprocess.run([
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", args.host,
                "--port", str(args.port),
            ], cwd=_BACKEND_DIR)

            if result.returncode == RESTART_EXIT_CODE:
                print("[Playarr] Restart requested — relaunching...")
                stop_tray()
                os.execv(sys.executable, [sys.executable] + sys.argv)
            else:
                break

    stop_tray()
    print("[Playarr] Stopped.")


if __name__ == "__main__":
    main()
