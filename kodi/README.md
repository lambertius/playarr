# Kodi integration

`plugin.video.playarr/` is a streaming-first Kodi video add-on for your Playarr
library — it opens straight into Party Mode and plays a continuous
weighted-shuffle, like Playarr's TV/Cast modes. See
[`plugin.video.playarr/README.md`](plugin.video.playarr/README.md) for the menu,
settings, and the API mapping.

## Backend requirement

**Stream** playback mode uses the raw passthrough endpoint
`GET /api/playback/raw/{video_id}` (added in `backend/app/routers/playback.py`).
It serves the original file with HTTP Range support and **no** remux/transcode,
so Kodi seeks correctly and decodes MKV/any codec natively. **Direct** mode needs
no backend changes.

## Packaging an installable zip

From this `kodi/` directory (PowerShell):

```powershell
# Remove any compiled caches first so they don't end up in the zip
Get-ChildItem plugin.video.playarr -Recurse -Directory -Filter __pycache__ |
  Remove-Item -Recurse -Force
Compress-Archive -Path plugin.video.playarr -DestinationPath plugin.video.playarr-1.0.0.zip -Force
```

The archive's top-level entry must be the `plugin.video.playarr/` folder (the
command above does this). Then in Kodi: **Settings → Add-ons → Install from zip
file**. You may first need **Settings → System → Add-ons → Unknown sources**.

## First run

Open the add-on, go to **Add-on Settings**, enter your Playarr host/port, and use
**Test connection** to confirm reachability before starting the party.
