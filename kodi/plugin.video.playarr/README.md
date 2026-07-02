# Playarr for Kodi (`plugin.video.playarr`)

A deliberately **minimal, streaming-first** Kodi add-on for your
[Playarr](../../README.md) library. It does one thing — **play Party Mode** — in
either Theatre or Fullscreen. It does **not** change any Playarr settings; to
shape what plays (exclusions, era, etc.) you open Playarr on the server. Browsing,
search, playlists and the on-device exclusion editor were intentionally removed.

## Root menu

- **▶ Play in Theatre** — plays Party Mode with the video centred over a
  **scrolling artwork wall** (composited server-side, since Kodi can't draw the
  wall natively).
- **▶ Play Fullscreen** — plays Party Mode full-screen via Kodi's native decoder.

  Either entry first shows a **read-only summary of the server's exclusions** with
  a note that they're changed on the server, then builds a weighted-random queue
  via `/api/library/party-mode` (honouring whatever exclusions and era the server
  has saved) and plays it.
- **Test connection** — pings `/api/version` and reports the server version.
- **Add Playarr to Favourites** — a home/favourites shortcut to the add-on.
- **Add-on Settings** — server host/port, playback source, path remap, etc.
  (connection settings only — not Playarr's library/party settings).

## Playback modes

The root-menu entry picks Theatre vs Fullscreen; **Fullscreen** then uses the
playback source below, while **Theatre** always streams the composited
`/api/playback/theatre/{id}` (it must be re-encoded server-side).

- **Stream via Playarr API** (default) — plays `/api/playback/raw/{id}`, the
  untouched file with HTTP Range support. Works on any device on the network,
  zero setup, correct seeking, and **no server-side transcode** — Kodi decodes
  MKV/HEVC/everything natively (the compatibility transcode is only for
  browsers).
- **Direct file path / share** — Kodi reads the original file off disk or an
  SMB/NFS share. Zero server CPU and the fastest seeking, but Kodi must be able
  to reach the files. Use the path-remap settings to rewrite the server's local
  path (e.g. `D:\Playarr\library`) to a Kodi-reachable one
  (e.g. `smb://nas/Playarr/library`).

> **Theatre** is the artwork-wall experience: the server overlays the centred
> video onto a **scrolling** poster-wall backdrop (a tall image rendered once,
> cached, and regenerated as your library grows; the scroll is done per-frame at
> composite time) and streams it as one H.264 file. It costs server CPU (a full
> re-encode per track), so use **Fullscreen** when you want native, zero-overhead
> playback.

## Requirements

- Kodi 19 (Matrix) or newer — Python 3.
- A running Playarr server reachable from the Kodi device.
- For **stream** mode, the Playarr backend must include `/api/playback/raw/{id}`
  (Playarr ≥ the release that ships this add-on). **Direct** mode works against
  any Playarr version.

## Install

1. Zip the `plugin.video.playarr` folder so the archive's top-level entry is the
   `plugin.video.playarr/` directory (see `../README.md` for a one-liner).
2. In Kodi: **Settings → Add-ons → Install from zip file**, then pick the zip.
   (You may need to enable *Unknown sources* in **Settings → System → Add-ons**.)
3. Open the add-on; on first run set your server details in **Add-on Settings**.

## Adding Playarr to the home screen

Kodi add-ons can't insert their own item into the skin's main menu — the **skin**
owns that menu — so the option depends on which skin you run:

- **Quickest (any skin):** open the add-on and pick **Add Playarr to
  Favourites**. This creates a Kodi favourite that jumps straight to Playarr;
  skins that surface favourites on the home screen will show it there.
- **Estuary (default skin):** use the favourite above, or pin Playarr via the
  favourites widget.
- **Skins with custom menus** (Arctic Horizon, Aeon Nox, etc.): in the skin's
  *Main menu* settings choose **Add menu item → Add-on → Playarr**.

## Settings

| Setting | Default | Notes |
|---|---|---|
| Playarr host or IP | `127.0.0.1` | Hostname/IP, or a full `http(s)://host:port` URL. |
| Port | `6969` | Ignored if the host field is a full URL. |
| Use HTTPS | off | Enable if Playarr is behind TLS. |
| Test connection | — | Pings `/api/version` and reports the server version. |
| Playback source | Stream via Playarr API | `Stream` or `Direct file path / share`. |
| Replace path prefix / with prefix | empty | Direct mode only — path remap for shares. |
| Party Mode: max videos to queue | `200` | Caps the shuffle queue so a large library doesn't build a huge playlist (which can crash Kodi). |
| Record playback history | on | POSTs to `/api/playback/history/{id}` on play. |
| Add Playarr to Kodi Favourites | — | Creates a home/favourites shortcut to the add-on. |

## How it maps to Playarr's API

| Add-on action | Endpoint |
|---|---|
| Party Mode queue | `GET /api/library/party-mode` (with `party_year`, `exclude_*`, `min_*_rating`) |
| Read shared exclusions | `GET /api/preferences/` (`partyExclusions` group) |
| Save shared exclusions | `PUT /api/preferences/partyExclusions` |
| Playback (fullscreen, stream) | `/api/playback/raw/{id}` |
| Playback (fullscreen, direct) | `file_path` from `/api/library/{id}` (optionally remapped) |
| Playback (theatre) | `/api/playback/theatre/{id}` (video composited over the artwork wall) |
| Artwork | `/api/playback/poster/{id}`, `/api/playback/thumb/{id}` |
| History | `POST /api/playback/history/{id}` |
| Version check | `GET /api/version` |

The add-on uses only the Python standard library — no extra Kodi modules needed.
