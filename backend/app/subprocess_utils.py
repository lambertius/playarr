"""
Subprocess utilities — suppress console windows on Windows.

Usage:
    from app.subprocess_utils import HIDE_WINDOW
    subprocess.run(cmd, capture_output=True, text=True, **HIDE_WINDOW)
"""
import subprocess
import sys

# On Windows (especially frozen/windowed apps), prevent subprocess from
# creating visible console windows when running ffmpeg/ffprobe etc.
HIDE_WINDOW: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if sys.platform == "win32"
    else {}
)
