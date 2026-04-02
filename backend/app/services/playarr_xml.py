"""
Playarr XML Sidecar — portable metadata for reimport / library migration.

Writes a `<basename>.playarr.xml` file alongside each video that captures
the full state Playarr knows about a track.  When Playarr is pointed at an
existing library (fresh install or library-import), it can read these files
and reconstruct the database without re-scraping.

The Kodi .nfo is left untouched — this is a separate, Playarr-specific file.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.etree.ElementTree import Element, SubElement, tostring, indent, parse

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PLAYARR_XML_VERSION = "1"


# ═══════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════

def _txt(parent: Element, tag: str, text: Any) -> Element:
    """Add a text sub-element, converting value to str."""
    el = SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def _opt(parent: Element, tag: str, text: Any) -> Optional[Element]:
    """Add a text sub-element only if the value is truthy."""
    if text:
        return _txt(parent, tag, text)
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel_path(file_path: str, folder_path: str) -> str:
    """Convert an absolute path to a path relative to the video folder."""
    try:
        return os.path.relpath(file_path, folder_path)
    except ValueError:
        # Different drive on Windows
        return file_path


# ═══════════════════════════════════════════════════════════
#  Write
# ═══════════════════════════════════════════════════════════

def build_playarr_xml(video, db: Session) -> Element:
    """
    Build an ElementTree root for a VideoItem.

    `video` must be a fully-loaded VideoItem with relationships:
    genres, sources, media_assets, quality_signature.
    """
    from app.models import MediaAsset

    root = Element("playarr")
    root.set("version", PLAYARR_XML_VERSION)
    root.set("exported", _now_iso())

    folder_path = video.folder_path or ""

    # ── identity ──
    identity = SubElement(root, "identity")
    _txt(identity, "artist", video.artist)
    _txt(identity, "title", video.title)
    _opt(identity, "album", video.album)
    _opt(identity, "year", video.year)
    _opt(identity, "plot", video.plot)

    # ── version info ──
    if video.version_type and video.version_type != "normal":
        ver = SubElement(identity, "version")
        _txt(ver, "type", video.version_type)
        _opt(ver, "label", video.alternate_version_label)
        _opt(ver, "original_artist", video.original_artist)
        _opt(ver, "original_title", video.original_title)

    # ── genres ──
    if video.genres:
        genres_el = SubElement(root, "genres")
        for g in video.genres:
            _txt(genres_el, "genre", g.name)

    # ── musicbrainz ──
    mb = SubElement(root, "musicbrainz")
    _opt(mb, "artist_id", video.mb_artist_id)
    _opt(mb, "recording_id", video.mb_recording_id)
    _opt(mb, "release_id", video.mb_release_id)
    _opt(mb, "release_group_id", video.mb_release_group_id)
    # Remove empty <musicbrainz/> if nothing was added
    if len(mb) == 0:
        root.remove(mb)

    # ── sources (YouTube URLs etc.) ──
    if video.sources:
        sources_el = SubElement(root, "sources")
        for src in video.sources:
            s = SubElement(sources_el, "source")
            _txt(s, "provider", src.provider.value if hasattr(src.provider, "value") else str(src.provider))
            _txt(s, "video_id", src.source_video_id)
            _txt(s, "original_url", src.original_url)
            _txt(s, "canonical_url", src.canonical_url)
            _opt(s, "source_type", src.source_type)
            _opt(s, "provenance", src.provenance)
            _opt(s, "channel_name", src.channel_name)
            _opt(s, "platform_title", src.platform_title)
            _opt(s, "upload_date", src.upload_date)

    # ── quality signature ──
    q = video.quality_signature
    if q:
        qual = SubElement(root, "quality")
        _opt(qual, "width", q.width)
        _opt(qual, "height", q.height)
        _opt(qual, "fps", q.fps)
        _opt(qual, "video_codec", q.video_codec)
        _opt(qual, "video_bitrate", q.video_bitrate)
        _opt(qual, "hdr", q.hdr)
        _opt(qual, "audio_codec", q.audio_codec)
        _opt(qual, "audio_bitrate", q.audio_bitrate)
        _opt(qual, "audio_sample_rate", q.audio_sample_rate)
        _opt(qual, "audio_channels", q.audio_channels)
        _opt(qual, "container", q.container)
        _opt(qual, "duration_seconds", q.duration_seconds)
        _opt(qual, "loudness_lufs", q.loudness_lufs)

    # ── artwork ──
    assets = db.query(MediaAsset).filter(
        MediaAsset.video_id == video.id,
        MediaAsset.status.in_(["valid", "pending"]),
    ).all()
    if assets:
        art_el = SubElement(root, "artwork")
        for asset in assets:
            a = SubElement(art_el, "asset")
            _txt(a, "type", asset.asset_type)
            # Store path relative to video folder so it's portable
            if asset.file_path:
                _txt(a, "file", _rel_path(asset.file_path, folder_path))
            _opt(a, "source_url", asset.source_url)
            _opt(a, "provenance", asset.provenance)
            _opt(a, "source_provider", asset.source_provider)
            _opt(a, "file_hash", asset.file_hash)
            _opt(a, "status", asset.status)
            if asset.width:
                _txt(a, "width", asset.width)
            if asset.height:
                _txt(a, "height", asset.height)

    # ── file info ──
    file_el = SubElement(root, "file")
    _opt(file_el, "resolution_label", video.resolution_label)
    _opt(file_el, "file_size_bytes", video.file_size_bytes)
    _opt(file_el, "import_method", video.import_method)
    _opt(file_el, "audio_fingerprint", video.audio_fingerprint)
    _opt(file_el, "acoustid_id", video.acoustid_id)

    # ── ratings ──
    if video.song_rating_set or video.video_rating_set:
        ratings = SubElement(root, "ratings")
        if video.song_rating_set:
            _txt(ratings, "song_rating", video.song_rating)
        if video.video_rating_set:
            _txt(ratings, "video_rating", video.video_rating)

    # ── processing state ──
    ps = video.processing_state
    if ps:
        state_el = SubElement(root, "processing_state")
        for key, val in ps.items():
            step = SubElement(state_el, "step")
            step.set("name", key)
            if isinstance(val, dict):
                _opt(step, "completed", val.get("completed"))
                _opt(step, "timestamp", val.get("timestamp"))
                _opt(step, "method", val.get("method"))
                _opt(step, "version", val.get("version"))
            else:
                _txt(step, "completed", val)

    # ── flags ──
    flags = SubElement(root, "flags")
    _txt(flags, "exclude_from_editor_scan", video.exclude_from_editor_scan)
    if video.locked_fields:
        import json
        locked = video.locked_fields if isinstance(video.locked_fields, list) else json.loads(video.locked_fields)
        if locked:
            lf = SubElement(flags, "locked_fields")
            for field in locked:
                _txt(lf, "field", field)
    _opt(flags, "review_status", video.review_status if video.review_status != "none" else None)
    _opt(flags, "review_reason", video.review_reason)

    # ── entity references (for cross-referencing on reimport) ──
    if video.artist_entity_id or video.album_entity_id or video.track_id:
        refs = SubElement(root, "entity_refs")
        if video.artist_entity:
            ae = SubElement(refs, "artist")
            _txt(ae, "name", video.artist_entity.canonical_name)
            _opt(ae, "mb_artist_id", video.artist_entity.mb_artist_id)
            # Entity-level provenance
            if getattr(video.artist_entity, "field_provenance", None):
                _prov_el = SubElement(ae, "field_provenance")
                for fk, fv in video.artist_entity.field_provenance.items():
                    p = SubElement(_prov_el, "field")
                    p.set("name", fk)
                    p.text = str(fv)
        if video.album_entity:
            al = SubElement(refs, "album")
            _txt(al, "title", video.album_entity.title)
            _opt(al, "mb_release_id", video.album_entity.mb_release_id)
            _opt(al, "mb_release_group_id", video.album_entity.mb_release_group_id)
            if getattr(video.album_entity, "field_provenance", None):
                _prov_el = SubElement(al, "field_provenance")
                for fk, fv in video.album_entity.field_provenance.items():
                    p = SubElement(_prov_el, "field")
                    p.set("name", fk)
                    p.text = str(fv)
        if video.track_entity:
            te = SubElement(refs, "track")
            _txt(te, "title", video.track_entity.title)
            _opt(te, "mb_recording_id", video.track_entity.mb_recording_id)
            _opt(te, "is_cover", video.track_entity.is_cover)
            _opt(te, "original_artist", video.track_entity.original_artist)
            _opt(te, "original_title", video.track_entity.original_title)
            if getattr(video.track_entity, "field_provenance", None):
                _prov_el = SubElement(te, "field_provenance")
                for fk, fv in video.track_entity.field_provenance.items():
                    p = SubElement(_prov_el, "field")
                    p.set("name", fk)
                    p.text = str(fv)

    # ── field provenance (video-level) ──
    if video.field_provenance:
        prov_el = SubElement(root, "field_provenance")
        for fk, fv in video.field_provenance.items():
            p = SubElement(prov_el, "field")
            p.set("name", fk)
            p.text = str(fv)

    # ── timestamps ──
    ts = SubElement(root, "timestamps")
    _txt(ts, "created_at", video.created_at.isoformat() if video.created_at else "")
    _txt(ts, "updated_at", video.updated_at.isoformat() if video.updated_at else "")
    _txt(ts, "exported_at", _now_iso())

    return root


def write_playarr_xml(video, db: Session) -> Optional[str]:
    """
    Write the .playarr.xml sidecar for a VideoItem.

    Returns the path to the written file, or None if the folder doesn't exist.
    """
    if not video.folder_path or not os.path.isdir(video.folder_path):
        return None

    root = build_playarr_xml(video, db)
    indent(root, space="    ")

    # Name it after the video file: "Artist - Title [1080p].playarr.xml"
    if video.file_path:
        base = os.path.splitext(os.path.basename(video.file_path))[0]
    else:
        base = os.path.basename(video.folder_path)

    xml_filename = f"{base}.playarr.xml"
    xml_path = os.path.join(video.folder_path, xml_filename)

    xml_bytes = tostring(root, encoding="unicode", xml_declaration=True)
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_bytes)

    logger.info(f"Wrote Playarr XML: {xml_path}")
    return xml_path


# ═══════════════════════════════════════════════════════════
#  Read / Parse
# ═══════════════════════════════════════════════════════════

def _text(el: Optional[Element], default: str = "") -> str:
    """Get text content from an element, or default."""
    if el is not None and el.text:
        return el.text.strip()
    return default


def _int(el: Optional[Element], default: Optional[int] = None) -> Optional[int]:
    t = _text(el)
    if t:
        try:
            return int(t)
        except ValueError:
            return default
    return default


def _float(el: Optional[Element], default: Optional[float] = None) -> Optional[float]:
    t = _text(el)
    if t:
        try:
            return float(t)
        except ValueError:
            return default
    return default


def _bool(el: Optional[Element], default: bool = False) -> bool:
    t = _text(el).lower()
    return t in ("true", "1", "yes") if t else default


def parse_playarr_xml(xml_path: str) -> Optional[Dict[str, Any]]:
    """
    Parse a .playarr.xml file into a dict suitable for creating/updating
    a VideoItem and its related records.

    Returns None if the file can't be parsed.
    """
    try:
        tree = parse(xml_path)
    except Exception as e:
        logger.warning(f"Failed to parse Playarr XML {xml_path}: {e}")
        return None

    root = tree.getroot()
    if root.tag != "playarr":
        logger.warning(f"Not a Playarr XML file: {xml_path}")
        return None

    folder_path = os.path.dirname(xml_path)
    result: Dict[str, Any] = {
        "xml_version": root.get("version", "1"),
        "exported_at": root.get("exported", ""),
    }

    # ── identity ──
    identity = root.find("identity")
    if identity is not None:
        result["artist"] = _text(identity.find("artist"))
        result["title"] = _text(identity.find("title"))
        result["album"] = _text(identity.find("album")) or None
        result["year"] = _int(identity.find("year"))
        result["plot"] = _text(identity.find("plot")) or None

        ver = identity.find("version")
        if ver is not None:
            result["version_type"] = _text(ver.find("type"), "normal")
            result["alternate_version_label"] = _text(ver.find("label")) or None
            result["original_artist"] = _text(ver.find("original_artist")) or None
            result["original_title"] = _text(ver.find("original_title")) or None
        else:
            result["version_type"] = "normal"

    # ── genres ──
    genres_el = root.find("genres")
    if genres_el is not None:
        result["genres"] = [_text(g) for g in genres_el.findall("genre") if _text(g)]
    else:
        result["genres"] = []

    # ── musicbrainz ──
    mb = root.find("musicbrainz")
    if mb is not None:
        result["mb_artist_id"] = _text(mb.find("artist_id")) or None
        result["mb_recording_id"] = _text(mb.find("recording_id")) or None
        result["mb_release_id"] = _text(mb.find("release_id")) or None
        result["mb_release_group_id"] = _text(mb.find("release_group_id")) or None

    # ── sources ──
    sources_el = root.find("sources")
    if sources_el is not None:
        result["sources"] = []
        for s in sources_el.findall("source"):
            result["sources"].append({
                "provider": _text(s.find("provider")),
                "source_video_id": _text(s.find("video_id")),
                "original_url": _text(s.find("original_url")),
                "canonical_url": _text(s.find("canonical_url")),
                "source_type": _text(s.find("source_type")) or None,
                "provenance": _text(s.find("provenance")) or None,
                "channel_name": _text(s.find("channel_name")) or None,
                "platform_title": _text(s.find("platform_title")) or None,
                "upload_date": _text(s.find("upload_date")) or None,
            })

    # ── quality ──
    qual = root.find("quality")
    if qual is not None:
        result["quality"] = {
            "width": _int(qual.find("width")),
            "height": _int(qual.find("height")),
            "fps": _float(qual.find("fps")),
            "video_codec": _text(qual.find("video_codec")) or None,
            "video_bitrate": _int(qual.find("video_bitrate")),
            "hdr": _bool(qual.find("hdr")),
            "audio_codec": _text(qual.find("audio_codec")) or None,
            "audio_bitrate": _int(qual.find("audio_bitrate")),
            "audio_sample_rate": _int(qual.find("audio_sample_rate")),
            "audio_channels": _int(qual.find("audio_channels")),
            "container": _text(qual.find("container")) or None,
            "duration_seconds": _float(qual.find("duration_seconds")),
            "loudness_lufs": _float(qual.find("loudness_lufs")),
        }

    # ── artwork ──
    art_el = root.find("artwork")
    if art_el is not None:
        result["artwork"] = []
        for a in art_el.findall("asset"):
            asset_file = _text(a.find("file"))
            # Convert relative path back to absolute
            if asset_file and not os.path.isabs(asset_file):
                asset_file = os.path.normpath(os.path.join(folder_path, asset_file))
            result["artwork"].append({
                "asset_type": _text(a.find("type")),
                "file_path": asset_file,
                "source_url": _text(a.find("source_url")) or None,
                "provenance": _text(a.find("provenance")) or None,
                "source_provider": _text(a.find("source_provider")) or None,
                "file_hash": _text(a.find("file_hash")) or None,
                "status": _text(a.find("status"), "valid"),
                "width": _int(a.find("width")),
                "height": _int(a.find("height")),
            })

    # ── file info ──
    file_el = root.find("file")
    if file_el is not None:
        result["resolution_label"] = _text(file_el.find("resolution_label")) or None
        result["file_size_bytes"] = _int(file_el.find("file_size_bytes"))
        result["import_method"] = _text(file_el.find("import_method")) or None
        result["audio_fingerprint"] = _text(file_el.find("audio_fingerprint")) or None
        result["acoustid_id"] = _text(file_el.find("acoustid_id")) or None

    # ── ratings ──
    ratings = root.find("ratings")
    if ratings is not None:
        sr = ratings.find("song_rating")
        if sr is not None:
            result["song_rating"] = _int(sr, 3)
            result["song_rating_set"] = True
        vr = ratings.find("video_rating")
        if vr is not None:
            result["video_rating"] = _int(vr, 3)
            result["video_rating_set"] = True

    # ── processing state ──
    state_el = root.find("processing_state")
    if state_el is not None:
        result["processing_state"] = {}
        for step in state_el.findall("step"):
            name = step.get("name")
            if not name:
                continue
            result["processing_state"][name] = {
                "completed": _bool(step.find("completed")),
                "timestamp": _text(step.find("timestamp")) or None,
                "method": _text(step.find("method")) or None,
                "version": _text(step.find("version")) or None,
            }

    # ── flags ──
    flags_el = root.find("flags")
    if flags_el is not None:
        result["exclude_from_editor_scan"] = _bool(flags_el.find("exclude_from_editor_scan"))
        lf = flags_el.find("locked_fields")
        if lf is not None:
            result["locked_fields"] = [_text(f) for f in lf.findall("field") if _text(f)]
        review = _text(flags_el.find("review_status"))
        if review:
            result["review_status"] = review
        review_reason = _text(flags_el.find("review_reason"))
        if review_reason:
            result["review_reason"] = review_reason

    # ── entity refs ──
    refs = root.find("entity_refs")
    if refs is not None:
        result["entity_refs"] = {}
        ae = refs.find("artist")
        if ae is not None:
            result["entity_refs"]["artist"] = {
                "name": _text(ae.find("name")),
                "mb_artist_id": _text(ae.find("mb_artist_id")) or None,
            }
        al = refs.find("album")
        if al is not None:
            result["entity_refs"]["album"] = {
                "title": _text(al.find("title")),
                "mb_release_id": _text(al.find("mb_release_id")) or None,
                "mb_release_group_id": _text(al.find("mb_release_group_id")) or None,
            }
        te = refs.find("track")
        if te is not None:
            result["entity_refs"]["track"] = {
                "title": _text(te.find("title")),
                "mb_recording_id": _text(te.find("mb_recording_id")) or None,
                "is_cover": _bool(te.find("is_cover")),
                "original_artist": _text(te.find("original_artist")) or None,
                "original_title": _text(te.find("original_title")) or None,
            }

    # ── timestamps ──
    ts = root.find("timestamps")
    if ts is not None:
        result["original_created_at"] = _text(ts.find("created_at")) or None
        result["original_updated_at"] = _text(ts.find("updated_at")) or None

    return result


def find_playarr_xml(folder_path: str) -> Optional[str]:
    """Find the .playarr.xml file in a video folder, if any."""
    if not os.path.isdir(folder_path):
        return None
    for entry in os.scandir(folder_path):
        if entry.is_file() and entry.name.endswith(".playarr.xml"):
            return entry.path
    return None
