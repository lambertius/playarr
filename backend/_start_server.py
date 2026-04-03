"""
Development server launcher for Playarr.

For production use, prefer run_playarr.py (in the repo root) which includes
startup validation, first-run setup, and works in both dev and installed modes.

This script starts uvicorn with auto-restart support: when the backend exits with
code 75 (triggered by the Settings > Restart button), this script
re-launches the process automatically.

Supports --delay N for delayed startup (used by "Start with Windows").
"""
import argparse
import os
import sys
import subprocess
import time

# Ensure the backend directory is on the Python path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BACKEND_DIR)

# Import and start the system-tray icon (best-effort)
try:
    from tray import start_tray, stop_tray
except ImportError:
    start_tray = stop_tray = lambda *a, **kw: None

RESTART_EXIT_CODE = 75


def _read_db_bool(key: str, default: str = "false") -> bool:
    """Read a boolean setting from the SQLite DB (best-effort)."""
    try:
        import sqlite3
        # Use runtime_dirs to find the DB in all modes
        sys.path.insert(0, BACKEND_DIR)
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


def main():
    parser = argparse.ArgumentParser(description="Playarr server launcher")
    parser.add_argument("--delay", type=int, default=0,
                        help="Seconds to wait before starting the server")
    args = parser.parse_args()

    if args.delay > 0:
        print(f"[start_server] Delayed start: waiting {args.delay}s …")
        time.sleep(args.delay)

    minimize_to_tray = _read_db_bool("minimize_to_tray", "true")
    if minimize_to_tray:
        start_tray(port=6969)

    auto_open = _read_db_bool("auto_open_browser", "true")
    if auto_open:
        import threading, webbrowser
        def _open():
            time.sleep(3)  # Wait for server to be ready
            webbrowser.open("http://localhost:6969")
        threading.Thread(target=_open, daemon=True).start()

    while True:
        result = subprocess.run(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "0.0.0.0", "--port", "6969"],
        )
        if result.returncode == RESTART_EXIT_CODE:
            print("[start_server] Restart requested — relaunching …")
            stop_tray()
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            break

    stop_tray()


if __name__ == "__main__":
    main()
