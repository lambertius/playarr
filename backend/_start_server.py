"""
Development server launcher for Playarr.

Starts uvicorn with auto-restart support: when the backend exits with
code 75 (triggered by the Settings > Restart button), this script
re-launches the process automatically.
"""
import os
import sys
import subprocess

# Ensure the backend directory is on the Python path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BACKEND_DIR)

# Import and start the system-tray icon (best-effort)
try:
    from tray import start_tray, stop_tray
except ImportError:
    start_tray = stop_tray = lambda *a, **kw: None

RESTART_EXIT_CODE = 75


def main():
    start_tray(port=6969)

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
