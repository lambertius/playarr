"""
Library Export Service — bulk export NFOs, Playarr XMLs, and artwork for
every video in the library.

Modes:
- skip_existing : Only write files that don't already exist on disk.
- overwrite_new : Compare file content; only overwrite if the new content differs.
- overwrite_all : Write everything unconditionally.

Videos whose ``locked_fields`` contain ``"_all"`` are excluded from any
overwrite (they are treated as skip_existing regardless of mode).
"""

import filecmp
import hashlib
import logging
import os
import shutil
import tempfile
from typing import Callable, Optional

from sqlalchemy.orm import Session, joinedload

from app.models import VideoItem, MediaAsset

logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────

def _is_locked(video: VideoItem) -> bool:
    """Return True if the video has the master lock (``_all``)."""
    locked = video.locked_fields
    if not locked:
        return False
    if isinstance(locked, str):
        import json
        locked = json.loads(locked)
    return "_all" in locked


def _files_identical(path_a: str, path_b: str) -> bool:
    """Compare two files byte-for-byte (shallow then deep)."""
    return filecmp.cmp(path_a, path_b, shallow=False)


def _content_matches(existing_path: str, new_content: str) -> bool:
    """Return True if *existing_path* already contains *new_content*."""
    try:
        with open(existing_path, "r", encoding="utf-8") as f:
            return f.read() == new_content
    except Exception:
        return False


# ── per-video export ───────────────────────────────────────

def _export_nfo(
    video: VideoItem,
    mode: str,
    log: Callable[[str], None],
) -> dict:
    """Write (or skip) the Kodi ``.nfo`` for *video*.  Returns a stats dict."""
    from app.services.file_organizer import build_folder_name, write_nfo_file, _xml_escape

    stats = {"written": 0, "skipped": 0, "unchanged": 0}
    if not video.folder_path or not os.path.isdir(video.folder_path):
        return stats

    folder_name = (
        build_folder_name(
            video.artist, video.title, video.resolution_label or "",
            version_type=video.version_type or "normal",
            alternate_version_label=video.alternate_version_label or "",
        )
        if video.resolution_label
        else f"{video.artist} - {video.title}"
    )
    nfo_path = os.path.join(video.folder_path, f"{folder_name}.nfo")

    locked = _is_locked(video)

    if mode == "skip_existing" or locked:
        if os.path.isfile(nfo_path):
            stats["skipped"] += 1
            return stats

    # Build NFO content to compare or write
    genres = [g.name for g in video.genres] if video.genres else []
    source_url = ""
    if video.sources:
        source_url = video.sources[0].original_url or ""

    genre_elements = "\n    ".join(f"<genre>{_xml_escape(g)}</genre>" for g in genres) if genres else ""
    year_str = str(video.year) if video.year else ""
    version_tags = ""
    vt = video.version_type or "normal"
    if vt != "normal":
        version_tags += f"\n    <tag>version:{vt}</tag>"
    if video.alternate_version_label:
        version_tags += f"\n    <tag>version_label:{_xml_escape(video.alternate_version_label)}</tag>"
    if video.original_artist:
        version_tags += f"\n    <tag>original_artist:{_xml_escape(video.original_artist)}</tag>"
    if video.original_title:
        version_tags += f"\n    <tag>original_title:{_xml_escape(video.original_title)}</tag>"

    nfo_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<musicvideo>
    <title>{_xml_escape(video.title or "")}</title>
    <artist>{_xml_escape(video.artist or "")}</artist>
    <album>{_xml_escape(video.album or "")}</album>
    <year>{year_str}</year>
    {genre_elements}
    <plot>{_xml_escape(video.plot or "")}</plot>
    <source>{_xml_escape(source_url)}</source>{version_tags}
</musicvideo>
"""

    if mode == "overwrite_new" and os.path.isfile(nfo_path):
        if _content_matches(nfo_path, nfo_content):
            stats["unchanged"] += 1
            return stats

    with open(nfo_path, "w", encoding="utf-8") as f:
        f.write(nfo_content)
    stats["written"] += 1
    return stats


def _export_xml(
    video: VideoItem,
    db: Session,
    mode: str,
    log: Callable[[str], None],
) -> dict:
    """Write (or skip) the Playarr ``.playarr.xml`` sidecar."""
    from app.services.playarr_xml import build_playarr_xml, write_playarr_xml
    from xml.etree.ElementTree import tostring, indent

    stats = {"written": 0, "skipped": 0, "unchanged": 0}
    if not video.folder_path or not os.path.isdir(video.folder_path):
        return stats

    if video.file_path:
        base = os.path.splitext(os.path.basename(video.file_path))[0]
    else:
        base = os.path.basename(video.folder_path)
    xml_path = os.path.join(video.folder_path, f"{base}.playarr.xml")

    locked = _is_locked(video)

    if mode == "skip_existing" or locked:
        if os.path.isfile(xml_path):
            stats["skipped"] += 1
            return stats

    # Build XML content
    root = build_playarr_xml(video, db)
    indent(root, space="    ")
    xml_bytes = tostring(root, encoding="unicode", xml_declaration=True)

    if mode == "overwrite_new" and os.path.isfile(xml_path):
        if _content_matches(xml_path, xml_bytes):
            stats["unchanged"] += 1
            return stats

    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_bytes)
    stats["written"] += 1
    return stats


def _export_artwork(
    video: VideoItem,
    db: Session,
    mode: str,
    log: Callable[[str], None],
) -> dict:
    """Copy artwork assets into the video folder (poster, thumb, etc.)."""
    stats = {"written": 0, "skipped": 0, "unchanged": 0}
    if not video.folder_path or not os.path.isdir(video.folder_path):
        return stats

    locked = _is_locked(video)

    assets = (
        db.query(MediaAsset)
        .filter(
            MediaAsset.video_id == video.id,
            MediaAsset.status.in_(["valid", "pending"]),
        )
        .all()
    )

    for asset in assets:
        if not asset.file_path or not os.path.isfile(asset.file_path):
            continue

        # The asset file_path is the canonical location (may already be in
        # the video folder).  We only need to ensure it exists there.
        dest = asset.file_path
        if not dest.startswith(video.folder_path):
            # Asset lives outside the video folder — copy it in
            dest = os.path.join(video.folder_path, os.path.basename(asset.file_path))

        if mode == "skip_existing" or locked:
            if os.path.isfile(dest):
                stats["skipped"] += 1
                continue

        if os.path.isfile(dest) and dest == asset.file_path:
            # Already in-place
            stats["unchanged"] += 1
            continue

        if mode == "overwrite_new" and os.path.isfile(dest):
            if _files_identical(asset.file_path, dest):
                stats["unchanged"] += 1
                continue

        # Copy the file
        try:
            shutil.copy2(asset.file_path, dest)
            stats["written"] += 1
        except OSError as exc:
            log(f"  Failed to copy artwork {asset.file_path} → {dest}: {exc}")

    return stats


# ── entity artwork (artist/album folders) ──────────────────

def _export_entity_artwork(
    db: Session,
    mode: str,
    log: Callable[[str], None],
) -> dict:
    """Re-export artist and album NFOs and artwork under _artists/ and _albums/."""
    from app.metadata.models import ArtistEntity, AlbumEntity
    from app.services.artwork_manager import (
        ensure_artist_artwork, ensure_album_artwork,
        artist_folder, album_folder,
        _write_artist_nfo, _write_album_nfo,
    )

    stats = {"written": 0, "skipped": 0, "unchanged": 0}

    # ── Artist entities ──
    artists = db.query(ArtistEntity).all()
    for ae in artists:
        folder = artist_folder(ae.canonical_name)
        nfo_path = os.path.join(folder, "artist.nfo")

        if mode == "skip_existing" and os.path.isfile(nfo_path):
            stats["skipped"] += 1
        else:
            genres = []
            # Grab genres from any video belonging to this artist
            sample = (
                db.query(VideoItem)
                .filter(VideoItem.artist_entity_id == ae.id)
                .options(joinedload(VideoItem.genres))
                .first()
            )
            if sample and sample.genres:
                genres = [g.name for g in sample.genres]

            _write_artist_nfo(nfo_path, ae.canonical_name, ae.bio, genres, ae.mb_artist_id)
            stats["written"] += 1

    # ── Album entities ──
    albums = db.query(AlbumEntity).all()
    for al in albums:
        artist_name = al.artist.canonical_name if al.artist else "Unknown Artist"
        folder = album_folder(artist_name, al.title)
        nfo_path = os.path.join(folder, "album.nfo")

        if mode == "skip_existing" and os.path.isfile(nfo_path):
            stats["skipped"] += 1
        else:
            _write_album_nfo(nfo_path, al.title, artist_name, al.year, [], al.mb_release_id)
            stats["written"] += 1

    return stats


# ── main entry point ───────────────────────────────────────

def export_library(
    db: Session,
    mode: str,
    log: Callable[[str], None],
    progress: Optional[Callable[[int], None]] = None,
) -> dict:
    """
    Export NFOs, Playarr XMLs, and artwork for every video in the library.

    Args:
        db: SQLAlchemy session.
        mode: ``"skip_existing"`` | ``"overwrite_new"`` | ``"overwrite_all"``.
        log: Callable that accepts a log-line string.
        progress: Optional callable that accepts a percent (0-100).

    Returns a summary dict with counts.
    """
    videos = (
        db.query(VideoItem)
        .options(
            joinedload(VideoItem.genres),
            joinedload(VideoItem.sources),
            joinedload(VideoItem.quality_signature),
        )
        .filter(VideoItem.folder_path.isnot(None))
        .all()
    )

    total = len(videos)
    log(f"Exporting {total} videos (mode={mode})")

    totals = {
        "nfo_written": 0, "nfo_skipped": 0, "nfo_unchanged": 0,
        "xml_written": 0, "xml_skipped": 0, "xml_unchanged": 0,
        "art_written": 0, "art_skipped": 0, "art_unchanged": 0,
        "entity_written": 0, "entity_skipped": 0, "entity_unchanged": 0,
        "locked_skipped": 0,
        "total": total,
    }

    for i, video in enumerate(videos):
        if progress:
            progress(int((i / max(total, 1)) * 100))

        label = f"{video.artist} - {video.title}"
        locked = _is_locked(video)
        if locked and mode != "skip_existing":
            totals["locked_skipped"] += 1
            log(f"  [{i+1}/{total}] {label} — locked, skipping overwrites")

        # NFO
        nfo = _export_nfo(video, mode, log)
        totals["nfo_written"] += nfo["written"]
        totals["nfo_skipped"] += nfo["skipped"]
        totals["nfo_unchanged"] += nfo["unchanged"]

        # Playarr XML
        xml = _export_xml(video, db, mode, log)
        totals["xml_written"] += xml["written"]
        totals["xml_skipped"] += xml["skipped"]
        totals["xml_unchanged"] += xml["unchanged"]

        # Artwork
        art = _export_artwork(video, db, mode, log)
        totals["art_written"] += art["written"]
        totals["art_skipped"] += art["skipped"]
        totals["art_unchanged"] += art["unchanged"]

    # Entity artwork (artist/album folders)
    log("Exporting entity artwork (artists/albums)...")
    ent = _export_entity_artwork(db, mode, log)
    totals["entity_written"] += ent["written"]
    totals["entity_skipped"] += ent["skipped"]
    totals["entity_unchanged"] += ent["unchanged"]

    if progress:
        progress(100)

    log(
        f"Export complete — NFO: {totals['nfo_written']} written / {totals['nfo_skipped']} skipped / {totals['nfo_unchanged']} unchanged  |  "
        f"XML: {totals['xml_written']} written / {totals['xml_skipped']} skipped / {totals['xml_unchanged']} unchanged  |  "
        f"Art: {totals['art_written']} written / {totals['art_skipped']} skipped / {totals['art_unchanged']} unchanged  |  "
        f"Entity: {totals['entity_written']} written / {totals['entity_skipped']} skipped  |  "
        f"Locked: {totals['locked_skipped']}"
    )
    return totals
