"""
Playarr Metadata Subsystem
===========================
Canonical metadata store with provider plugins, asset caching,
revision tracking, and Kodi export.

Modules:
    models      — Entity graph (Artist, Album, Track, CachedAsset, etc.)
    providers/  — Wikipedia, MusicBrainz, CoverArtArchive plugin adapters
    resolver    — Matching, confidence scoring, merge strategy
    assets      — Central asset cache (download, deduplicate, resize)
    revisions   — Snapshot / rollback for any entity
    exporters/  — Kodi NFO + artwork exporter
"""
