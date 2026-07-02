# -*- coding: utf-8 -*-
"""
Thin wrappers around the Kodi Python API: settings, logging, notifications,
and URL helpers.  Keeping every xbmc.* touch-point here makes the rest of the
add-on easy to read and (mostly) testable.
"""
import sys
from urllib.parse import urlencode

import xbmc
import xbmcaddon
import xbmcgui

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")
ADDON_VERSION = ADDON.getAddonInfo("version")
ADDON_PATH = ADDON.getAddonInfo("path")
ADDON_ICON = ADDON.getAddonInfo("icon")
ADDON_FANART = ADDON.getAddonInfo("fanart")


# ── Routing ────────────────────────────────────────────────

def base_url():
    """The plugin:// base URL Kodi handed us in argv[0]."""
    return sys.argv[0]


def handle():
    """The integer directory handle Kodi handed us in argv[1]."""
    return int(sys.argv[1])


def build_url(**kwargs):
    """Build a plugin:// URL for an internal navigation target.

    None values are dropped so callers can pass optional filters freely.
    """
    params = {k: v for k, v in kwargs.items() if v is not None}
    return base_url() + "?" + urlencode(params, doseq=True)


# ── Settings ───────────────────────────────────────────────

def _s(key):
    return ADDON.getSetting(key)


def get_setting_bool(key):
    return ADDON.getSettingBool(key)


def get_setting_int(key, default=0):
    try:
        return ADDON.getSettingInt(key)
    except (TypeError, ValueError):
        try:
            return int(_s(key))
        except (TypeError, ValueError):
            return default


def server_base():
    """Return the configured Playarr server base URL, e.g. http://host:6969."""
    scheme = "https" if get_setting_bool("use_https") else "http"
    host = (_s("host") or "127.0.0.1").strip().rstrip("/")
    # If the user pasted a full URL into the host field, respect it verbatim.
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/")
    port = get_setting_int("port", 6969)
    return "{0}://{1}:{2}".format(scheme, host, port)


def page_size():
    n = get_setting_int("page_size", 50)
    return max(10, min(n, 200))


def party_limit():
    """Max videos to queue for Party Mode.

    'Shuffle all' on a large library can build a playlist of thousands of
    items, which exhausts memory and crashes Kodi on constrained devices.  The
    server already returns the queue weighted-shuffled, so taking the first N
    is still a good random selection.
    """
    n = get_setting_int("party_limit", 200)
    return max(1, min(n, 2000))


def playback_mode():
    """'stream' (via the API) or 'direct' (read the file path / share)."""
    # Stored as an enum index: 0 = stream, 1 = direct.
    return "direct" if _s("playback_mode") == "1" else "stream"


def record_history():
    return get_setting_bool("record_history")


def path_remap():
    """(from, to) string pair for rewriting server file paths to a Kodi-reachable
    path in direct mode.  Empty strings mean 'no remap'."""
    return (_s("remap_from") or "", _s("remap_to") or "")


def open_settings():
    ADDON.openSettings()


def add_favourite(title, plugin_path, thumbnail=None):
    """Add a Kodi favourite that opens the given plugin path in the Videos
    window.  Returns True on success.  Used to give Playarr a one-click home /
    favourites entry (Kodi add-ons cannot inject themselves into a skin's main
    menu directly)."""
    import json
    params = {
        "title": title,
        "type": "window",
        "window": "videos",
        "windowparameter": plugin_path,
    }
    if thumbnail:
        params["thumbnail"] = thumbnail
    req = {"jsonrpc": "2.0", "id": 1, "method": "Favourites.AddFavourite", "params": params}
    try:
        resp = json.loads(xbmc.executeJSONRPC(json.dumps(req)))
        return resp.get("result") == "OK"
    except Exception as exc:  # noqa: BLE001
        log_error("add_favourite failed: {0}".format(exc))
        return False


# ── UI helpers ─────────────────────────────────────────────

def localize(text):
    """Placeholder for future localisation — returns the string unchanged."""
    return text


def notify(message, heading=None, icon=xbmcgui.NOTIFICATION_INFO, time_ms=4000):
    xbmcgui.Dialog().notification(
        heading or ADDON_NAME, message, icon, time_ms
    )


def error(message, heading=None):
    notify(message, heading=heading, icon=xbmcgui.NOTIFICATION_ERROR, time_ms=6000)


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[{0}] {1}".format(ADDON_ID, message), level)


def log_error(message):
    log(message, level=xbmc.LOGERROR)


def keyboard(heading, default=""):
    """Prompt for text. Returns the entered string, or '' if cancelled."""
    kb = xbmc.Keyboard(default, heading)
    kb.doModal()
    if kb.isConfirmed():
        return kb.getText()
    return ""


def yesno(heading, message, yeslabel=None, nolabel=None):
    """Confirm dialog. Returns True if the user chose 'yes'."""
    kwargs = {}
    if yeslabel:
        kwargs["yeslabel"] = yeslabel
    if nolabel:
        kwargs["nolabel"] = nolabel
    return bool(xbmcgui.Dialog().yesno(heading, message, **kwargs))
