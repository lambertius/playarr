"""
Playarr System Tray Icon — provides a tray icon with Open / Quit actions.

Uses pystray + Pillow to render the Playarr play-button icon as a
system tray icon on Windows.
"""
import os
import sys
import signal
import threading
import webbrowser

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Icon generation — recreates the favicon play-triangle design at 64×64
# ---------------------------------------------------------------------------

def _create_icon_image(size: int = 64) -> Image.Image:
    """Draw the Playarr icon: dark rounded-rect background with red play triangle."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background rounded rectangle
    bg_color = (28, 34, 48, 255)  # #1c2230
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 5, fill=bg_color)

    # Subtle border
    border_color = (43, 50, 69, 255)  # #2b3245
    draw.rounded_rectangle(
        [1, 1, size - 2, size - 2],
        radius=size // 5 - 1,
        fill=None,
        outline=border_color,
        width=1,
    )

    # Play triangle — red gradient approximated as solid red
    play_color = (225, 29, 46, 255)  # #e11d2e accent
    # Triangle vertices scaled to icon size
    left = int(size * 0.39)
    top = int(size * 0.28)
    right = int(size * 0.73)
    mid_y = size // 2
    bottom = int(size * 0.72)
    draw.polygon([(left, top), (right, mid_y), (left, bottom)], fill=play_color)

    return img


# ---------------------------------------------------------------------------
# Tray setup
# ---------------------------------------------------------------------------

_tray_icon = None
_server_url = "http://localhost:6969"


def _open_browser(icon, item):
    webbrowser.open(_server_url)


def _quit_app(icon, item):
    icon.stop()
    # Signal the main process to terminate
    os.kill(os.getpid(), signal.SIGTERM)


def start_tray(port: int = 6969):
    """
    Start the system tray icon in a daemon thread.

    Call this *after* the server is ready to accept connections.
    """
    global _tray_icon, _server_url
    _server_url = f"http://localhost:{port}"

    try:
        import pystray
    except ImportError:
        print("[tray] pystray not installed — skipping system tray icon")
        return

    image = _create_icon_image(64)
    menu = pystray.Menu(
        pystray.MenuItem("Open Playarr", _open_browser, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit_app),
    )

    _tray_icon = pystray.Icon("Playarr", image, "Playarr", menu)

    # pystray.Icon.run() blocks, so run in a daemon thread
    tray_thread = threading.Thread(target=_tray_icon.run, daemon=True)
    tray_thread.start()


def stop_tray():
    """Stop the tray icon if running."""
    global _tray_icon
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass
        _tray_icon = None
