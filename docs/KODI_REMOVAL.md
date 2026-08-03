# Kodi add-on removal

Playarr 1.11 removes the Playarr Kodi add-on, its download, export and dedicated
raw/theatre playback APIs, compositor, and bundled plug-in assets. This does not delete media, NFO sidecars or artwork;
those remain usable by ordinary media-library software.

If the old `plugin.video.playarr` add-on is installed in Kodi, uninstall it from
Kodi's add-on manager. Remove any shortcut that opens the add-on. Playarr's TV
and Cast browser surfaces now own Party Mode playback and shared preferences.

No database action is required. Deprecated server settings and routes are
ignored during upgrade and are no longer returned by the settings UI or API.
