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
import signal
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
        # Check the self-managed tools dir (populated by the in-app updater).
        try:
            from app.runtime_dirs import get_runtime_dirs
            tools_dir = get_runtime_dirs().tools_dir
            for fn in (f"{name}.exe", name):
                if os.path.isfile(os.path.join(str(tools_dir), fn)):
                    return True
        except Exception:
            pass
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


def _read_db_setting(key: str, default: str) -> str:
    """Read an instance setting before the application server is running."""
    try:
        import sqlite3
        from app.runtime_dirs import get_runtime_dirs
        db_path = str(get_runtime_dirs().db_path)
        if not os.path.exists(db_path):
            return default
        conn = sqlite3.connect(db_path, timeout=2)
        cur = conn.execute(
            "SELECT value FROM app_settings WHERE key = ? AND user_id IS NULL", (key,)
        )
        row = cur.fetchone()
        conn.close()
        return str(row[0]) if row else default
    except Exception:
        return default


def _read_db_bool(key: str, default: str = "false") -> bool:
    return _read_db_setting(key, default).lower() == "true"


def _default_port() -> int:
    """Environment overrides DB; DB overrides the packaged default."""
    raw = os.environ.get("PLAYARR_PORT") or _read_db_setting("server.port", "6969")
    try:
        port = int(raw)
        return port if 1 <= port <= 65535 else 6969
    except (TypeError, ValueError):
        return 6969


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
# Supervisor / server-child model
# ---------------------------------------------------------------------------
# The server runs as a *child* process supervised by the parent.  If the server
# dies for any reason (crash, native fault, external kill of the child) the
# supervisor relaunches it, so a transient failure can't leave Playarr dead.
# Exit codes the child returns tell the supervisor what to do:
#   RESTART_EXIT_CODE (75)  -> intentional restart (e.g. /settings/restart)
#   0 or 15 (SIGTERM)       -> clean shutdown / tray Quit -> stop, do not relaunch
#   anything else           -> crash/kill -> relaunch (with crash-loop guard)
SHUTDOWN_EXIT_CODES = (0, 15)

_child_proc = None             # current server child (subprocess.Popen)
_supervisor_stopping = False   # set when the supervisor is intentionally stopping


def _watch_parent(parent_pid: int):
    """Run in a supervised child: exit if the supervisor process dies, so a
    killed supervisor can never leave an orphaned server holding the port."""
    if sys.platform != "win32" or not parent_pid:
        return
    try:
        import ctypes
        import threading
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        WAIT_OBJECT_0 = 0x0
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(parent_pid))
        if not handle:
            return

        def _watch():
            while True:
                if kernel32.WaitForSingleObject(handle, 2000) == WAIT_OBJECT_0:
                    try:
                        from app.crash_diagnostics import record
                        record(f"supervisor (pid {parent_pid}) exited — child stopping to avoid orphan")
                    except Exception:
                        pass
                    os._exit(0)

        threading.Thread(target=_watch, daemon=True, name="parent-watch").start()
    except Exception:
        logger.exception("Could not start parent-watch (continuing without it)")


def _run_server(args):
    """Run the FastAPI server in-process — the supervised child (and the
    in-process fallback if a child can't be spawned)."""
    # Arm crash diagnostics so a hard death leaves a record in logs/crash.log.
    try:
        from app.runtime_dirs import get_runtime_dirs
        from app.crash_diagnostics import install_crash_handlers
        install_crash_handlers(str(get_runtime_dirs().log_dir))
    except Exception:
        logger.exception("Could not arm crash diagnostics")

    if getattr(args, "parent_pid", 0):
        _watch_parent(args.parent_pid)

    import uvicorn
    try:
        uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
    except BaseException:
        # Anything that propagates out of uvicorn.run ends the process; the
        # windowed build's stderr is devnull, so record it before exiting.
        import traceback as _traceback
        try:
            from app.crash_diagnostics import record
            record("uvicorn.run exited via exception:\n" + _traceback.format_exc())
        except Exception:
            pass
        raise


def _child_command(args) -> list:
    """Command line that relaunches this program as a supervised server child."""
    cmd = [sys.executable]
    if not _IS_FROZEN:
        cmd.append(os.path.abspath(__file__))
    cmd += [
        "--server-child",
        "--host", args.host,
        "--port", str(args.port),
        "--parent-pid", str(os.getpid()),
    ]
    return cmd


def _supervise(args):
    """Keep the server child alive: relaunch on crash/abnormal exit, stop on a
    clean shutdown or an explicit restart loop."""
    global _child_proc, _supervisor_stopping

    def _stop(_signum, _frame):
        global _supervisor_stopping
        _supervisor_stopping = True
        p = _child_proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    for _sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, _sig):
            try:
                signal.signal(getattr(signal, _sig), _stop)
            except Exception:
                pass

    spawn_flags = {}
    if sys.platform == "win32":
        spawn_flags["creationflags"] = subprocess.CREATE_NO_WINDOW

    crash_times: list[float] = []
    while True:
        try:
            _child_proc = subprocess.Popen(_child_command(args), cwd=_BACKEND_DIR, **spawn_flags)
        except Exception:
            logger.exception("Could not spawn server child — running in-process instead")
            _run_server(args)
            return

        code = _child_proc.wait()
        _child_proc = None

        if _supervisor_stopping or code in SHUTDOWN_EXIT_CODES:
            logger.info("Server stopped (exit %s) — supervisor exiting.", code)
            break
        if code == RESTART_EXIT_CODE:
            logger.info("Server requested a restart — relaunching...")
            continue

        # Abnormal exit (crash or external kill of the child): relaunch, but if
        # it's failing repeatedly fall back to running the server in-process —
        # so a supervisor-side problem can never leave Playarr unable to start.
        now = time.monotonic()
        crash_times[:] = [t for t in crash_times if now - t < 60]
        crash_times.append(now)
        if len(crash_times) >= 5:
            logger.error(
                "Server child exited abnormally %d times within 60s (last code %s) — "
                "falling back to in-process server. Check logs/crash.log.",
                len(crash_times), code,
            )
            _run_server(args)
            return
        delay = min(15, 2 * len(crash_times))
        logger.warning(
            "Server child exited abnormally (code %s) — relaunching in %ds (attempt %d).",
            code, delay, len(crash_times),
        )
        time.sleep(delay)


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
    parser.add_argument("--port", type=int, default=_default_port(),
                        help="Port to listen on (default: saved Settings value, then 6969)")
    parser.add_argument("--host", type=str, default=os.environ.get("PLAYARR_HOST", "0.0.0.0"),
                        help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--headless", action="store_true",
                        help="No tray icon or auto-browser")
    # Internal: launched by the supervisor to run the actual server.
    parser.add_argument("--server-child", dest="server_child", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", dest="parent_pid", type=int, default=0,
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    # ---- Supervised child: just run the server ----
    if args.server_child:
        _run_server(args)
        return

    # ---- Supervisor / parent ----
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

    # Arm crash diagnostics in the supervisor too (records supervisor-side issues).
    try:
        from app.runtime_dirs import get_runtime_dirs
        from app.crash_diagnostics import install_crash_handlers
        install_crash_handlers(str(get_runtime_dirs().log_dir))
    except Exception:
        logger.exception("Could not arm crash diagnostics")

    # Tray + browser live in the supervisor so they persist across server
    # restarts (and the tray Quit stops the whole supervisor).
    if not args.headless:
        if _read_db_bool("minimize_to_tray", "true"):
            start_tray(port=args.port)

        if _read_db_bool("auto_open_browser", "true"):
            import threading
            import webbrowser

            def _open():
                time.sleep(3)
                webbrowser.open(f"http://localhost:{args.port}")

            threading.Thread(target=_open, daemon=True).start()

    # Supervise the server child (self-healing).  Falls back to in-process if a
    # child can't be spawned.
    _supervise(args)

    stop_tray()
    print("[Playarr] Stopped.")


if __name__ == "__main__":
    main()
