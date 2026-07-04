# -*- coding: utf-8 -*-
"""
Playarr add-on — a pure Party Mode streaming endpoint for Kodi.

Deliberately minimal: it only *plays* Party Mode. There are two entries — play
in Theatre (video over a scrolling artwork wall, composited server-side) or
Fullscreen (native video) — and a read-only summary of the server's exclusions
before playback. It cannot change any Playarr settings; to shape what plays, the
user opens Playarr on the server. Browsing/search/playlists and the on-device
exclusion editor were removed in favour of this streaming-first experience.
"""
from urllib.parse import parse_qsl

import xbmc
import xbmcgui
import xbmcplugin

from resources.lib import kodiutils as ku
from resources.lib.api import PlayarrApi, PlayarrApiError


# ── Construction helpers ───────────────────────────────────

def _api():
    return PlayarrApi(ku.server_base())


def _add_dir(label, *, art=None, **params):
    """Add a navigable folder item to the current directory listing."""
    li = xbmcgui.ListItem(label=label)
    li.setArt(art or {"icon": ku.ADDON_ICON, "thumb": ku.ADDON_ICON,
                       "fanart": ku.ADDON_FANART})
    xbmcplugin.addDirectoryItem(
        handle=ku.handle(),
        url=ku.build_url(**params),
        listitem=li,
        isFolder=True,
    )


def _video_label(artist, title, version_type=None, resolution=None):
    label = u"{0} - {1}".format(artist or u"Unknown", title or u"Unknown")
    extra = []
    if version_type and version_type != "normal":
        extra.append(version_type.replace("_", " ").title())
    if resolution:
        extra.append(resolution)
    if extra:
        label = u"{0}  [{1}]".format(label, u" · ".join(extra))
    return label


def _apply_video_info(li, *, artist, title, album=None, year=None,
                      genres=None, plot=None, duration=None):
    """Populate musicvideo InfoLabels on a ListItem (Kodi 19+ compatible)."""
    info = {
        "mediatype": "musicvideo",
        "title": title or u"",
        "artist": [a.strip() for a in (artist or u"").split(";") if a.strip()],
    }
    if album:
        info["album"] = album
    if year:
        info["year"] = int(year)
        info["premiered"] = u"{0}-01-01".format(int(year))
    if genres:
        info["genre"] = genres
    if plot:
        info["plot"] = plot
    if duration:
        try:
            info["duration"] = int(float(duration))
        except (TypeError, ValueError):
            pass
    li.setInfo("video", info)


def _video_art(api, video_id, has_poster):
    art = {"fanart": ku.ADDON_FANART, "icon": ku.ADDON_ICON}
    if has_poster:
        poster = api.poster_url(video_id)
        art["poster"] = poster
        art["thumb"] = poster
        # The video player thumbnail (scene frame) makes nicer fanart when present.
        art["fanart"] = api.thumb_url(video_id)
    return art


def _make_play_listitem(api, video_id, artist, title, *, album=None, year=None,
                        genres=None, plot=None, duration=None,
                        version_type=None, resolution=None, has_poster=False):
    """A playable ListItem whose path points back into the plugin (action=play)."""
    li = xbmcgui.ListItem(label=_video_label(artist, title, version_type, resolution))
    li.setProperty("IsPlayable", "true")
    _apply_video_info(li, artist=artist, title=title, album=album, year=year,
                      genres=genres, plot=plot, duration=duration)
    li.setArt(_video_art(api, video_id, has_poster))
    return li


def _end(succeeded=True, update=False):
    # Reached both from directory clicks (valid handle) and RunPlugin (handle -1).
    # endOfDirectory on a -1 handle just logs a Kodi error, so skip it.
    if ku.handle() >= 0:
        xbmcplugin.endOfDirectory(ku.handle(), succeeded=succeeded, updateListing=update)


# ── Root menu ──────────────────────────────────────────────

def root_menu():
    _add_dir(u"▶ Play in Theatre", action="party", layout="theatre")
    _add_dir(u"▶ Play Fullscreen", action="party", layout="fullscreen")
    _add_dir(u"Test connection", action="ping")
    _add_dir(u"Add Playarr to Favourites", action="addfav")
    _add_dir(u"Add-on Settings", action="settings")
    _end()


# ── Party Mode (the only thing this add-on does) ───────────

def _exclusion_summary(ex):
    """Human-readable lines describing the active exclusions (empty list = none)."""
    lines = []
    if ex.get("version_types"):
        lines.append(u"Version types: {0}".format(u", ".join(ex["version_types"])))
    if ex.get("artists"):
        lines.append(u"Artists: {0}".format(u", ".join(ex["artists"])))
    if ex.get("genres"):
        lines.append(u"Genres: {0}".format(u", ".join(ex["genres"])))
    if ex.get("albums"):
        lines.append(u"Albums: {0}".format(u", ".join(ex["albums"])))
    if ex.get("min_song_rating") is not None:
        lines.append(u"Min song rating: {0}".format(ex["min_song_rating"]))
    if ex.get("min_video_rating") is not None:
        lines.append(u"Min video rating: {0}".format(ex["min_video_rating"]))
    return lines


def party_prompt(layout):
    """Show a read-only exclusions summary, then play Party Mode in the chosen
    layout. All filtering (exclusions, era) is owned by the server — this just
    plays what the server returns."""
    api = _api()

    # Read-only summary, like the web TV/Cast prompt. When a Party Mode playlist
    # is configured on the server it is authoritative (played shuffled, ignoring
    # exclusions), so show that instead of the exclusions summary.
    try:
        playlist = api.get_party_playlist()
    except PlayarrApiError as exc:
        ku.error(str(exc))
        _end(succeeded=False)
        return
    if playlist:
        message = (u"Playing playlist: {0}\n\nShuffled. Change it in Playarr "
                   u"Settings → Party Mode on the server.").format(playlist["name"])
    else:
        try:
            ex = api.get_exclusions()
        except PlayarrApiError as exc:
            ku.error(str(exc))
            _end(succeeded=False)
            return
        lines = _exclusion_summary(ex)
        summary = u"\n".join(lines) if lines else u"No exclusions set."
        message = u"{0}\n\nTo change what plays, open Playarr Settings on the server.".format(summary)
    if not ku.yesno(u"Start the Party?", message, yeslabel=u"Start", nolabel=u"Cancel"):
        _end(succeeded=False)
        return

    try:
        data = api.party_mode()
    except PlayarrApiError as exc:
        ku.error(str(exc))
        _end(succeeded=False)
        return

    tracks = (data or {}).get("tracks", [])
    limit = ku.party_limit()
    if len(tracks) > limit:
        tracks = tracks[:limit]
    if not tracks:
        ku.notify(u"No videos match the current filters")
        _end(succeeded=False)
        return
    ku.notify(u"Party Mode: queued {0} videos".format(len(tracks)))
    _queue_and_play(api, tracks, layout)


def _queue_and_play(api, tracks, layout="fullscreen"):
    """Build the Kodi video playlist from track dicts and start playback.

    Each entry is a plugin:// URL (action=play) so Kodi resolves the real
    stream lazily when it reaches that track.

    Crash-safety: we release the originating directory handle *before* starting
    playback.  Playback re-enters the plugin (action=play) to resolve each
    item, and starting it while the folder's directory handle is still open can
    hang or crash Kodi.  `_end()` is a no-op when invoked via RunPlugin
    (handle -1), so this is safe from both folder clicks and context menus.
    """
    tracks = [t for t in (tracks or [])
              if (t.get("videoId") or t.get("video_id") or t.get("id"))]
    if not tracks:
        ku.notify(u"Nothing to play")
        _end(succeeded=False)
        return

    _end(succeeded=False)

    try:
        pl = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        pl.clear()
        for t in tracks:
            vid = t.get("videoId") or t.get("video_id") or t.get("id")
            li = _make_play_listitem(
                api, vid,
                t.get("artist"), t.get("title"),
                duration=t.get("duration") or t.get("duration_seconds"),
                has_poster=bool(t.get("hasPoster") or t.get("has_poster")),
            )
            pl.add(ku.build_url(action="play", id=vid, layout=layout), li)
        xbmc.Player().play(pl)
    except Exception as exc:  # noqa: BLE001 — never let a bad track crash Kodi
        ku.log_error("Queue/play failed: {0}".format(exc))
        ku.error(u"Could not start playback")


# ── Single-video resolution ────────────────────────────────

def _resolve_play_path(api, detail):
    """Return the URL Kodi should open for fullscreen (native) playback."""
    video_id = detail.get("id")
    if ku.playback_mode() == "direct":
        file_path = detail.get("file_path")
        if file_path:
            remap_from, remap_to = ku.path_remap()
            if remap_from and remap_from in file_path:
                file_path = file_path.replace(remap_from, remap_to)
            # Network shares (smb://, nfs://) and POSIX paths use forward slashes.
            if remap_to and ("://" in remap_to or remap_to.startswith("/")):
                file_path = file_path.replace("\\", "/")
            return file_path
        ku.log_error("Direct mode but no file_path for video {0}; "
                     "falling back to stream".format(video_id))
    # Untouched file with HTTP Range support — ideal for Kodi's native decoder
    # (no server-side transcode; the compat transcode is only for browsers).
    return api.raw_stream_url(video_id)


def _version_tuple(v):
    """Parse a dotted version string into a comparable tuple of ints."""
    parts = []
    for chunk in str(v or "").split("."):
        digits = u"".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def test_connection():
    """Settings 'Test connection' — verify the server is reachable."""
    api = _api()
    try:
        info = api.ping()
    except PlayarrApiError as exc:
        ku.error(u"Connection failed: {0}".format(exc))
        return
    version = (info or {}).get("version") or (info or {}).get("app_version") or u"?"
    # Warn if the add-on and server versions differ on major.minor — the add-on
    # is distributed from the server (Settings → System → Kodi) so the two are
    # meant to stay matched.  Patch-level differences are tolerated silently.
    if _version_tuple(version)[:2] != _version_tuple(ku.ADDON_VERSION)[:2]:
        ku.error(
            u"Version mismatch: add-on {0} vs server {1}. "
            u"Re-download the Kodi add-on from Playarr settings.".format(
                ku.ADDON_VERSION, version),
        )
        return
    ku.notify(u"Connected to Playarr {0} at {1}".format(version, ku.server_base()))


def add_to_favourites():
    """Create a Kodi favourite that opens Playarr — the closest cross-skin
    equivalent of a home-menu shortcut (skins own the main menu, so an add-on
    can't add a sidebar item itself)."""
    if ku.add_favourite(ku.ADDON_NAME, ku.base_url(), ku.ADDON_ICON):
        ku.notify(u"Added Playarr to Favourites")
    else:
        ku.error(u"Could not add Playarr to Favourites")
    # No-op when launched via RunPlugin (handle -1); closes the folder cleanly
    # when launched from the root-menu item.
    _end(succeeded=False)


def play(video_id, layout="fullscreen"):
    api = _api()
    try:
        detail = api.get_video(video_id)
    except PlayarrApiError as exc:
        ku.error(str(exc))
        xbmcplugin.setResolvedUrl(ku.handle(), False, xbmcgui.ListItem())
        return
    if not detail:
        ku.error(u"Video {0} not found".format(video_id))
        xbmcplugin.setResolvedUrl(ku.handle(), False, xbmcgui.ListItem())
        return

    # Theatre = the server-composited scrolling artwork-wall stream (always via
    # the API, since it must be re-encoded); fullscreen = native raw/direct.
    if layout == "theatre":
        url = api.theatre_stream_url(video_id)
    else:
        url = _resolve_play_path(api, detail)
    genres = [g.get("name") for g in (detail.get("genres") or []) if g.get("name")]
    qs = detail.get("quality_signature") or {}

    li = xbmcgui.ListItem(path=url)
    _apply_video_info(
        li,
        artist=detail.get("artist"),
        title=detail.get("title"),
        album=detail.get("album"),
        year=detail.get("year"),
        genres=genres,
        plot=detail.get("plot"),
        duration=qs.get("duration_seconds"),
    )
    li.setArt(_video_art(api, video_id,
                         any(a.get("asset_type") == "poster"
                             for a in (detail.get("media_assets") or []))))
    if qs.get("width") and qs.get("height"):
        li.addStreamInfo("video", {
            "codec": qs.get("video_codec") or "",
            "width": int(qs["width"]),
            "height": int(qs["height"]),
            "duration": int(qs.get("duration_seconds") or 0),
        })

    if ku.record_history():
        api.record_history(video_id, qs.get("duration_seconds"))

    xbmcplugin.setResolvedUrl(ku.handle(), True, li)


# ── Dispatch ───────────────────────────────────────────────

def run(argv):
    params = dict(parse_qsl(argv[2][1:])) if len(argv) > 2 and argv[2] else {}
    action = params.get("action")

    if action is None:
        root_menu()
    elif action == "party":
        party_prompt(params.get("layout", "fullscreen"))
    elif action == "play":
        play(int(params["id"]), params.get("layout", "fullscreen"))
    elif action == "ping":
        test_connection()
    elif action == "addfav":
        add_to_favourites()
    elif action == "settings":
        ku.open_settings()
    else:
        ku.log_error("Unknown action: {0}".format(action))
        root_menu()
