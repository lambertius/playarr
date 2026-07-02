# -*- coding: utf-8 -*-
"""
Playarr REST API client.

Dependency-free: uses only the Python standard library so the add-on needs no
extra Kodi modules.  All endpoints map onto Playarr's FastAPI routes
(prefix /api/...).  Network/JSON errors raise PlayarrApiError, which the
caller turns into a Kodi notification.
"""
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


class PlayarrApiError(Exception):
    """Raised on any transport- or server-level failure talking to Playarr."""


class PlayarrApi(object):
    def __init__(self, base_url, timeout=20):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    # ── low-level ──────────────────────────────────────────

    def _get(self, path, params=None):
        url = self.base + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url += "?" + urlencode(clean, doseq=True)
        req = Request(url, headers={"Accept": "application/json",
                                    "User-Agent": "PlayarrKodi/1.0"})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except HTTPError as exc:
            raise PlayarrApiError("Server returned HTTP {0} for {1}".format(exc.code, path))
        except URLError as exc:
            raise PlayarrApiError("Cannot reach Playarr at {0} ({1})".format(self.base, exc.reason))
        except Exception as exc:  # noqa: BLE001 — surface anything else cleanly
            raise PlayarrApiError(str(exc))
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise PlayarrApiError("Invalid JSON from {0}".format(path))

    def _write(self, method, path, params=None, body=None):
        url = self.base + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url += "?" + urlencode(clean, doseq=True)
        data = None
        headers = {"Accept": "application/json", "User-Agent": "PlayarrKodi/1.0"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except (HTTPError, URLError) as exc:
            raise PlayarrApiError(str(exc))
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return None

    def _post(self, path, params=None, body=None):
        return self._write("POST", path, params=params, body=body)

    # ── library browsing ───────────────────────────────────

    def ping(self):
        """Lightweight reachability check — returns the version payload."""
        return self._get("/api/version")

    def list_videos(self, page=1, page_size=50, **filters):
        params = {"page": page, "page_size": page_size}
        params.update(filters)
        return self._get("/api/library/", params)

    def get_video(self, video_id):
        return self._get("/api/library/{0}".format(video_id))

    def list_artists(self, **filters):
        return self._get("/api/library/artists", filters) or []

    def list_albums(self, **filters):
        return self._get("/api/library/albums", filters) or []

    def list_years(self, **filters):
        return self._get("/api/library/years", filters) or []

    def list_genres(self, **filters):
        return self._get("/api/library/genres", filters) or []

    def party_mode(self, **filters):
        return self._get("/api/library/party-mode", filters) or {"tracks": []}

    # ── shared preferences (party exclusions, …) ───────────

    def get_preferences(self):
        """All server-stored preference groups as {name: value}."""
        return self._get("/api/preferences/") or {}

    def get_exclusions(self):
        """The shared partyExclusions group, with defaults filled in (read-only —
        the add-on only displays these; they're edited on the server)."""
        prefs = self.get_preferences()
        ex = prefs.get("partyExclusions") or {}
        return {
            "version_types": ex.get("version_types") or [],
            "artists": ex.get("artists") or [],
            "genres": ex.get("genres") or [],
            "albums": ex.get("albums") or [],
            "min_song_rating": ex.get("min_song_rating"),
            "min_video_rating": ex.get("min_video_rating"),
        }

    # ── playlists ──────────────────────────────────────────

    def list_playlists(self):
        return self._get("/api/playlists/") or []

    def get_playlist(self, playlist_id):
        return self._get("/api/playlists/{0}".format(playlist_id))

    # ── playback side-effects ──────────────────────────────

    def record_history(self, video_id, duration_watched=None):
        try:
            self._post("/api/playback/history/{0}".format(video_id),
                       params={"duration_watched": duration_watched})
        except PlayarrApiError:
            pass  # history is best-effort; never block playback

    # ── URL builders (consumed directly by Kodi) ───────────

    def raw_stream_url(self, video_id):
        """Untouched file with Range support — ideal for Kodi (no remux)."""
        return "{0}/api/playback/raw/{1}".format(self.base, video_id)

    def theatre_stream_url(self, video_id):
        """Server-composited stream: video centred over the artwork-wall backdrop
        (Kodi's theatre mode — it can't draw the wall natively)."""
        return "{0}/api/playback/theatre/{1}".format(self.base, video_id)

    def poster_url(self, video_id):
        return "{0}/api/playback/poster/{1}".format(self.base, video_id)

    def thumb_url(self, video_id):
        return "{0}/api/playback/thumb/{1}".format(self.base, video_id)

    def artwork_url(self, video_id, asset_type):
        return "{0}/api/playback/artwork/{1}/{2}".format(self.base, video_id, asset_type)
