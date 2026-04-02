"""
Kodi Exporter — Materialise canonical metadata + cached assets into
Kodi-compatible filesystem layouts.

Export structure
----------------
::

    <VideoRoot>/
        Artist - Title [1080p]/
            Artist - Title [1080p].mkv
            Artist - Title [1080p].nfo    ← musicvideo NFO
            poster.jpg
            thumb.jpg

    <ArtistRoot>/
        ArtistName/
            poster.jpg
            fanart.jpg                    ← optional
            artist.nfo

    <AlbumRoot>/
        ArtistName/
            AlbumTitle/
                poster.jpg
                album.nfo

Behaviour
---------
- **Idempotent**: safe to run repeatedly; same input → same output.
- **Incremental**: uses ``ExportManifest`` to skip unchanged entities.
- **Stale cleanup**: can remove paths from a previous manifest that are
  no longer referenced.
- **Re-export**: ``export_all()`` regenerates everything; ``export_entity()``
  updates a single artist/album/video.

NFO content
-----------
- ``<musicvideo>``: ``<title>``, ``<artist>``, ``<album>``, ``<year>``,
  ``<genre>`` (repeatable), ``<plot>``, ``<tag>source:URL</tag>``
- ``<artist>``: ``<name>``, ``<biography>``, ``<genre>``, ``<thumb>``,
  ``<fanart>``, ``<musicbrainzartistid>``
- ``<album>``: ``<title>``, ``<artist>``, ``<year>``, ``<genre>``,
  ``<thumb>``, ``<musicbrainzreleasegroupid>``
"""
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.metadata.models import (
    ArtistEntity, AlbumEntity, TrackEntity,
    CachedAsset, ExportManifest,
)

logger = logging.getLogger(__name__)

# Characters illegal in Windows filenames
_ILLEGAL = re.compile(r'[<>:"/\\|?*]')


def _safe(name: str) -> str:
    name = name.replace("?", "-")
    name = _ILLEGAL.sub("", name)
    return re.sub(r"\s+", " ", name).strip() or "Unknown"


def _xml(text: str) -> str:
    """Basic XML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _artist_root() -> str:
    settings = get_settings()
    root = getattr(settings, "artist_root", None)
    if not root:
        root = os.path.join(settings.library_dir, "_artists")
    os.makedirs(root, exist_ok=True)
    return root


def _album_root() -> str:
    settings = get_settings()
    root = getattr(settings, "album_root", None)
    if not root:
        root = os.path.join(settings.library_dir, "_albums")
    os.makedirs(root, exist_ok=True)
    return root


def _video_root() -> str:
    settings = get_settings()
    return settings.library_dir


# ---------------------------------------------------------------------------
# Copy/link helper
# ---------------------------------------------------------------------------

def _place_file(src: str, dest: str, use_symlink: bool = False):
    """Copy (or symlink) src to dest, creating parent dirs."""
    if not src or not os.path.isfile(src):
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    # Skip if dest already exists and is same content
    if os.path.isfile(dest):
        if os.path.getsize(dest) == os.path.getsize(src):
            return  # assume identical

    if use_symlink:
        try:
            if os.path.isfile(dest):
                os.remove(dest)
            os.symlink(src, dest)
            return
        except OSError:
            pass  # fallback to copy
    shutil.copy2(src, dest)


# ---------------------------------------------------------------------------
# NFO generators
# ---------------------------------------------------------------------------

def _musicvideo_nfo(
    title: str, artist: str, album: str, year: Optional[int],
    genres: List[str], plot: str, source_url: str,
) -> str:
    genre_elems = "\n    ".join(f"<genre>{_xml(g)}</genre>" for g in genres) if genres else ""
    year_str = str(year) if year else ""
    tag_source = f"\n    <tag>source:{_xml(source_url)}</tag>" if source_url else ""

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<musicvideo>
    <title>{_xml(title)}</title>
    <artist>{_xml(artist)}</artist>
    <album>{_xml(album or "")}</album>
    <year>{year_str}</year>
    {genre_elems}
    <plot>{_xml(plot or "")}</plot>{tag_source}
</musicvideo>
"""


def _artist_nfo(
    name: str,
    biography: Optional[str] = None,
    genres: Optional[List[str]] = None,
    mb_artist_id: Optional[str] = None,
) -> str:
    genre_elems = ""
    if genres:
        genre_elems = "\n    ".join(f"<genre>{_xml(g)}</genre>" for g in genres)
    bio_elem = f"\n    <biography>{_xml(biography)}</biography>" if biography else ""
    mb_elem = f"\n    <musicbrainzartistid>{_xml(mb_artist_id)}</musicbrainzartistid>" if mb_artist_id else ""

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<artist>
    <name>{_xml(name)}</name>
    {genre_elems}{bio_elem}{mb_elem}
    <thumb aspect="poster">poster.jpg</thumb>
    <fanart>
        <thumb>fanart.jpg</thumb>
    </fanart>
</artist>
"""


def _album_nfo(
    title: str, artist: str,
    year: Optional[int] = None,
    genres: Optional[List[str]] = None,
    mb_release_id: Optional[str] = None,
) -> str:
    genre_elems = ""
    if genres:
        genre_elems = "\n    ".join(f"<genre>{_xml(g)}</genre>" for g in genres)
    year_elem = f"\n    <year>{year}</year>" if year else ""
    mb_elem = f"\n    <musicbrainzreleasegroupid>{_xml(mb_release_id)}</musicbrainzreleasegroupid>" if mb_release_id else ""

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<album>
    <title>{_xml(title)}</title>
    <artist>{_xml(artist)}</artist>{year_elem}
    {genre_elems}{mb_elem}
    <thumb>poster.jpg</thumb>
</album>
"""


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _get_or_create_manifest(db: Session, target: str, entity_type: str,
                            entity_id: int, root: str) -> ExportManifest:
    m = db.query(ExportManifest).filter(
        ExportManifest.export_target == target,
        ExportManifest.entity_type == entity_type,
        ExportManifest.entity_id == entity_id,
    ).first()
    if not m:
        m = ExportManifest(
            export_target=target,
            export_root=root,
            entity_type=entity_type,
            entity_id=entity_id,
            exported_paths=[],
            exported_version=0,
        )
        db.add(m)
        db.flush()
    return m


def _update_manifest(db: Session, manifest: ExportManifest, paths: List[str]):
    from sqlalchemy.orm.attributes import flag_modified
    manifest.exported_paths = paths
    manifest.exported_version += 1
    manifest.last_exported_at = datetime.now(timezone.utc)
    flag_modified(manifest, "exported_paths")


# ---------------------------------------------------------------------------
# Export: Artist
# ---------------------------------------------------------------------------

def export_artist(db: Session, artist: ArtistEntity,
                  use_symlink: bool = False) -> List[str]:
    """
    Export artist artwork + NFO to the artist root.

    Returns list of written file paths.
    """
    root = _artist_root()
    folder = os.path.join(root, _safe(artist.canonical_name))
    os.makedirs(folder, exist_ok=True)
    written: List[str] = []

    # Poster from cache
    poster_cache = db.query(CachedAsset).filter(
        CachedAsset.entity_type == "artist",
        CachedAsset.entity_id == artist.id,
        CachedAsset.kind == "poster",
    ).first()
    if poster_cache and os.path.isfile(poster_cache.local_cache_path):
        dest = os.path.join(folder, "poster.jpg")
        _place_file(poster_cache.local_cache_path, dest, use_symlink)
        written.append(dest)
        # folder.jpg — Kodi uses this as the directory thumbnail
        folder_jpg = os.path.join(folder, "folder.jpg")
        _place_file(poster_cache.local_cache_path, folder_jpg, use_symlink)
        written.append(folder_jpg)

    # Fanart from cache
    fanart_cache = db.query(CachedAsset).filter(
        CachedAsset.entity_type == "artist",
        CachedAsset.entity_id == artist.id,
        CachedAsset.kind == "fanart",
    ).first()
    if fanart_cache and os.path.isfile(fanart_cache.local_cache_path):
        dest = os.path.join(folder, "fanart.jpg")
        _place_file(fanart_cache.local_cache_path, dest, use_symlink)
        written.append(dest)

    # Artist NFO
    genres = [g.name for g in artist.genres] if artist.genres else []
    nfo_content = _artist_nfo(
        artist.canonical_name,
        biography=artist.biography,
        genres=genres,
        mb_artist_id=artist.mb_artist_id,
    )
    nfo_path = os.path.join(folder, "artist.nfo")
    with open(nfo_path, "w", encoding="utf-8") as f:
        f.write(nfo_content)
    written.append(nfo_path)

    # Update manifest
    manifest = _get_or_create_manifest(db, "kodi", "artist", artist.id, root)
    _update_manifest(db, manifest, written)

    logger.info(f"Exported artist: {artist.canonical_name} ({len(written)} files)")
    return written


# ---------------------------------------------------------------------------
# Export: Album
# ---------------------------------------------------------------------------

def export_album(db: Session, album: AlbumEntity,
                 use_symlink: bool = False) -> List[str]:
    """Export album artwork + NFO to the album root."""
    root = _album_root()
    artist_name = album.artist.canonical_name if album.artist else "Unknown Artist"
    folder = os.path.join(root, _safe(artist_name), _safe(album.title))
    os.makedirs(folder, exist_ok=True)
    written: List[str] = []

    # Poster from cache
    poster_cache = db.query(CachedAsset).filter(
        CachedAsset.entity_type == "album",
        CachedAsset.entity_id == album.id,
        CachedAsset.kind == "poster",
    ).first()
    if poster_cache and os.path.isfile(poster_cache.local_cache_path):
        dest = os.path.join(folder, "poster.jpg")
        _place_file(poster_cache.local_cache_path, dest, use_symlink)
        written.append(dest)
        # folder.jpg — Kodi uses this as the directory thumbnail
        folder_jpg = os.path.join(folder, "folder.jpg")
        _place_file(poster_cache.local_cache_path, folder_jpg, use_symlink)
        written.append(folder_jpg)

    # Album NFO
    genres = [g.name for g in album.genres] if album.genres else []
    nfo_content = _album_nfo(
        album.title,
        artist=artist_name,
        year=album.year,
        genres=genres,
        mb_release_id=album.mb_release_id,
    )
    nfo_path = os.path.join(folder, "album.nfo")
    with open(nfo_path, "w", encoding="utf-8") as f:
        f.write(nfo_content)
    written.append(nfo_path)

    # Manifest
    manifest = _get_or_create_manifest(db, "kodi", "album", album.id, root)
    _update_manifest(db, manifest, written)

    logger.info(f"Exported album: {album.title} ({len(written)} files)")
    return written


# ---------------------------------------------------------------------------
# Export: MusicVideo (single video item)
# ---------------------------------------------------------------------------

def export_video(
    db: Session,
    video_id: int,
    artist: str,
    title: str,
    album: str = "",
    year: Optional[int] = None,
    genres: Optional[List[str]] = None,
    plot: str = "",
    source_url: str = "",
    folder_path: Optional[str] = None,
    resolution_label: str = "",
    use_symlink: bool = False,
) -> List[str]:
    """
    Export music video NFO + artwork into its library folder.

    This replaces the old ``write_nfo_file()`` for new code paths.
    Existing folders are preserved — only NFO and artwork files are
    written/overwritten.
    """
    if not folder_path or not os.path.isdir(folder_path):
        logger.warning(f"Video folder not found: {folder_path}")
        return []

    written: List[str] = []

    # NFO
    folder_name = os.path.basename(folder_path)
    nfo_path = os.path.join(folder_path, f"{folder_name}.nfo")
    nfo_content = _musicvideo_nfo(
        title=title, artist=artist, album=album,
        year=year, genres=genres or [], plot=plot,
        source_url=source_url,
    )
    with open(nfo_path, "w", encoding="utf-8") as f:
        f.write(nfo_content)
    written.append(nfo_path)

    # Poster from cache
    poster_cache = db.query(CachedAsset).filter(
        CachedAsset.entity_type == "video",
        CachedAsset.entity_id == video_id,
        CachedAsset.kind == "poster",
    ).first()
    if poster_cache and os.path.isfile(poster_cache.local_cache_path):
        dest = os.path.join(folder_path, "poster.jpg")
        _place_file(poster_cache.local_cache_path, dest, use_symlink)
        written.append(dest)

    # Thumb from cache
    thumb_cache = db.query(CachedAsset).filter(
        CachedAsset.entity_type == "video",
        CachedAsset.entity_id == video_id,
        CachedAsset.kind == "thumb",
    ).first()
    if thumb_cache and os.path.isfile(thumb_cache.local_cache_path):
        dest = os.path.join(folder_path, "thumb.jpg")
        _place_file(thumb_cache.local_cache_path, dest, use_symlink)
        written.append(dest)

    # Manifest
    manifest = _get_or_create_manifest(db, "kodi", "video", video_id, _video_root())
    _update_manifest(db, manifest, written)

    logger.info(f"Exported video: {artist} - {title} ({len(written)} files)")
    return written


# ---------------------------------------------------------------------------
# Full library export
# ---------------------------------------------------------------------------

def export_all(use_symlink: bool = False) -> Dict[str, int]:
    """
    Re-export ALL entities' Kodi outputs.

    Returns counts: {"artists": N, "albums": N, "videos": N}
    """
    from app.models import VideoItem

    db = SessionLocal()
    try:
        counts = {"artists": 0, "albums": 0, "videos": 0}

        # Artists
        for artist in db.query(ArtistEntity).all():
            try:
                export_artist(db, artist, use_symlink=use_symlink)
                counts["artists"] += 1
            except Exception as e:
                logger.error(f"Export artist failed ({artist.canonical_name}): {e}")

        # Albums
        for album in db.query(AlbumEntity).all():
            try:
                export_album(db, album, use_symlink=use_symlink)
                counts["albums"] += 1
            except Exception as e:
                logger.error(f"Export album failed ({album.title}): {e}")

        # Videos
        for video in db.query(VideoItem).filter(VideoItem.folder_path.isnot(None)).all():
            try:
                source_url = ""
                if video.sources:
                    source_url = video.sources[0].canonical_url
                genres = [g.name for g in video.genres]
                export_video(
                    db, video.id,
                    artist=video.artist, title=video.title,
                    album=video.album or "", year=video.year,
                    genres=genres, plot=video.plot or "",
                    source_url=source_url,
                    folder_path=video.folder_path,
                    resolution_label=video.resolution_label or "",
                    use_symlink=use_symlink,
                )
                counts["videos"] += 1
            except Exception as e:
                logger.error(f"Export video failed ({video.artist} - {video.title}): {e}")

        db.commit()
        logger.info(f"Full export complete: {counts}")
        return counts

    except Exception as e:
        db.rollback()
        logger.error(f"Full export failed: {e}")
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Clean stale outputs
# ---------------------------------------------------------------------------

def clean_stale_exports(db: Session, export_target: str = "kodi"):
    """
    Remove exported files that are no longer referenced by any entity.

    Compares current manifests against existing files and deletes orphans.
    """
    manifests = db.query(ExportManifest).filter(
        ExportManifest.export_target == export_target,
    ).all()

    referenced_paths: set = set()
    for m in manifests:
        for p in (m.exported_paths or []):
            referenced_paths.add(os.path.normpath(p))

    removed = 0
    for m in manifests:
        for p in (m.exported_paths or []):
            norm = os.path.normpath(p)
            if not os.path.isfile(norm):
                continue
            # Check if the entity still exists
            cls_map = {
                "artist": ArtistEntity,
                "album": AlbumEntity,
            }
            cls = cls_map.get(m.entity_type)
            if cls:
                entity = db.query(cls).get(m.entity_id)
                if not entity:
                    try:
                        os.remove(norm)
                        removed += 1
                        logger.info(f"Removed stale export: {norm}")
                    except OSError as e:
                        logger.warning(f"Failed to remove {norm}: {e}")

    if removed:
        logger.info(f"Cleaned {removed} stale export files")
