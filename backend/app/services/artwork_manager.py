"""
Artwork Manager — Kodi-compatible Artist/Album folder and artwork generation.

Manages the folder hierarchy Kodi expects for music-video library artwork:

    LibraryRoot/
    ├── _artists/
    │   └── ArtistName/
    │       ├── poster.jpg
    │       ├── fanart.jpg
    │       └── artist.nfo
    ├── _albums/
    │   └── ArtistName/
    │       └── AlbumName/
    │           ├── poster.jpg
    │           └── album.nfo
    └── ArtistName - Title [1080p]/           ← existing video folders
        ├── ArtistName - Title [1080p].mkv
        ├── ArtistName - Title [1080p].nfo
        ├── poster.jpg
        └── thumb.jpg

The _artists and _albums directories live inside the library_dir.
Kodi doesn't scan these directly — the artist/album info is picked up
from the <artist> and <album> tags in music-video .nfo files, but Kodi
also looks for artwork via its internal artist/album info manager.
The system writes artist.nfo and album.nfo to help Kodi associate artwork.

This module handles:
- Directory creation
- Artwork downloading (with caching / skip-if-exists)
- Image resizing via Pillow
- NFO generation for artist and album
"""
import logging
import os
import re
from typing import Optional, Dict, Any, Tuple

from app.config import get_settings
logger = logging.getLogger(__name__)

# Characters illegal in Windows filenames
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*]')


def _safe_name(name: str) -> str:
    """Sanitise a name for use as a folder name on Windows/Linux."""
    cleaned = name.replace("?", "-")
    cleaned = _ILLEGAL_CHARS.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Unknown"


def _xml_escape(text: str) -> str:
    """Basic XML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def get_artists_dir() -> str:
    """Return the root _artists directory inside library_dir."""
    settings = get_settings()
    p = os.path.join(settings.library_dir, "_artists")
    os.makedirs(p, exist_ok=True)
    return p


def get_albums_dir() -> str:
    """Return the root _albums directory inside library_dir."""
    settings = get_settings()
    p = os.path.join(settings.library_dir, "_albums")
    os.makedirs(p, exist_ok=True)
    return p


def artist_folder(artist: str) -> str:
    """Return and ensure the artist artwork folder exists."""
    p = os.path.join(get_artists_dir(), _safe_name(artist))
    os.makedirs(p, exist_ok=True)
    return p


def album_folder(artist: str, album: str) -> str:
    """Return and ensure the album artwork folder exists."""
    p = os.path.join(get_albums_dir(), _safe_name(artist), _safe_name(album))
    os.makedirs(p, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Image downloading + resizing
# ---------------------------------------------------------------------------

def download_and_save(url: str, dest_path: str,
                      max_width: int = 1000,
                      max_height: int = 1500,
                      overwrite: bool = False) -> bool:
    """
    Download an image, validate, convert to JPEG, resize, and save.

    Delegates to artwork_service.download_and_validate() for robust
    validation (Content-Type, magic bytes, PIL verify).  If the image
    is invalid, it is NOT persisted.

    Returns True on success.
    """
    from app.services.artwork_service import download_and_validate

    if not url:
        return False

    result = download_and_validate(
        url, dest_path,
        max_width=max_width, max_height=max_height,
        overwrite=overwrite,
    )
    if not result.success:
        logger.warning(f"Artwork download/validation failed for {url}: {result.error}")
    return result.success


# ---------------------------------------------------------------------------
# Artist artwork pipeline
# ---------------------------------------------------------------------------

def ensure_artist_artwork(
    artist: str,
    image_url: Optional[str] = None,
    fanart_url: Optional[str] = None,
    bio: Optional[str] = None,
    genres: Optional[list] = None,
    mb_artist_id: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """
    Download artist artwork and write artist.nfo for Kodi.

    Returns dict with paths: poster_path, fanart_path, nfo_path (or None).
    """
    result: Dict[str, Any] = {"poster_path": None, "fanart_path": None, "nfo_path": None}
    if not artist or artist == "Unknown Artist":
        return result

    folder = artist_folder(artist)
    poster_path = os.path.join(folder, "poster.jpg")
    fanart_path = os.path.join(folder, "fanart.jpg")
    nfo_path = os.path.join(folder, "artist.nfo")

    # Poster (1000×1500)
    if download_and_save(image_url, poster_path,
                         max_width=1000, max_height=1500,
                         overwrite=overwrite):
        result["poster_path"] = poster_path

    # Fanart (1920×1080)
    if download_and_save(fanart_url, fanart_path,
                         max_width=1920, max_height=1080,
                         overwrite=overwrite):
        result["fanart_path"] = fanart_path

    # Write artist.nfo
    if not os.path.isfile(nfo_path) or overwrite:
        _write_artist_nfo(nfo_path, artist, bio, genres, mb_artist_id)
        result["nfo_path"] = nfo_path

    return result


def _write_artist_nfo(
    path: str,
    artist: str,
    bio: Optional[str] = None,
    genres: Optional[list] = None,
    mb_artist_id: Optional[str] = None,
):
    """Write a Kodi-compatible artist.nfo file."""
    genre_elems = ""
    if genres:
        genre_elems = "\n    ".join(f"<genre>{_xml_escape(g)}</genre>" for g in genres)

    mb_elem = ""
    if mb_artist_id:
        mb_elem = f"\n    <musicbrainzartistid>{_xml_escape(mb_artist_id)}</musicbrainzartistid>"

    bio_elem = ""
    if bio:
        bio_elem = f"\n    <biography>{_xml_escape(bio)}</biography>"

    content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<artist>
    <name>{_xml_escape(artist)}</name>
    {genre_elems}{mb_elem}{bio_elem}
    <thumb aspect="poster">poster.jpg</thumb>
    <fanart>
        <thumb>fanart.jpg</thumb>
    </fanart>
</artist>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Wrote artist NFO: {path}")


# ---------------------------------------------------------------------------
# Album artwork pipeline
# ---------------------------------------------------------------------------

def ensure_album_artwork(
    artist: str,
    album: str,
    image_url: Optional[str] = None,
    year: Optional[int] = None,
    genres: Optional[list] = None,
    mb_release_id: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """
    Download album cover and write album.nfo for Kodi.

    Returns dict with paths: poster_path, nfo_path (or None).
    """
    result: Dict[str, Any] = {"poster_path": None, "nfo_path": None}
    if not album:
        return result

    folder = album_folder(artist, album)
    poster_path = os.path.join(folder, "poster.jpg")
    nfo_path = os.path.join(folder, "album.nfo")

    # Album poster (1000×1000 — square)
    if download_and_save(image_url, poster_path,
                         max_width=1000, max_height=1000,
                         overwrite=overwrite):
        result["poster_path"] = poster_path

    # Write album.nfo
    if not os.path.isfile(nfo_path) or overwrite:
        _write_album_nfo(nfo_path, album, artist, year, genres, mb_release_id)
        result["nfo_path"] = nfo_path

    return result


def _write_album_nfo(
    path: str,
    album: str,
    artist: str,
    year: Optional[int] = None,
    genres: Optional[list] = None,
    mb_release_id: Optional[str] = None,
):
    """Write a Kodi-compatible album.nfo file."""
    genre_elems = ""
    if genres:
        genre_elems = "\n    ".join(f"<genre>{_xml_escape(g)}</genre>" for g in genres)

    year_elem = f"\n    <year>{year}</year>" if year else ""

    mb_elem = ""
    if mb_release_id:
        mb_elem = f"\n    <musicbrainzreleasegroupid>{_xml_escape(mb_release_id)}</musicbrainzreleasegroupid>"

    content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<album>
    <title>{_xml_escape(album)}</title>
    <artist>{_xml_escape(artist)}</artist>{year_elem}
    {genre_elems}{mb_elem}
    <thumb>poster.jpg</thumb>
</album>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Wrote album NFO: {path}")


# ---------------------------------------------------------------------------
# High-level: run full artist + album artwork pipeline for a video
# ---------------------------------------------------------------------------

def process_artist_album_artwork(
    artist: str,
    album: Optional[str],
    mb_artist_id: Optional[str] = None,
    mb_release_id: Optional[str] = None,
    mb_album_release_id: Optional[str] = None,
    mb_album_release_group_id: Optional[str] = None,
    log_callback=None,
    overwrite: bool = False,
    source: str = "all",
    wiki_album_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the complete artist + album artwork pipeline for a single video import.

    This is the main entry point called from the import task.

    Args:
        artist: Artist name
        album: Album name (may be None)
        mb_artist_id: Pre-resolved MusicBrainz artist ID (optional)
        mb_release_id: Pre-resolved MusicBrainz release ID for the single (optional)
        mb_album_release_id: Pre-resolved MusicBrainz release ID for the parent album (optional)
        mb_album_release_group_id: Pre-resolved MusicBrainz release-group ID for the parent album (optional)
        log_callback: Optional callable(message) for logging progress
        source: "all" (MusicBrainz + Wikipedia), "wikipedia" (Wikipedia only),
                or "musicbrainz" (MusicBrainz/CAA only)

    Returns dict:
        artist_poster, artist_fanart, artist_nfo,
        album_poster, album_nfo
    """
    from app.services.artist_album_scraper import (
        get_artist_artwork, get_album_artwork,
        get_artist_artwork_wikipedia, get_artist_artwork_musicbrainz,
        get_album_artwork_wikipedia, get_album_artwork_musicbrainz,
    )

    # Select the right functions based on source
    if source == "wikipedia":
        _get_artist = get_artist_artwork_wikipedia
        _get_album = get_album_artwork_wikipedia
    elif source == "musicbrainz":
        _get_artist = get_artist_artwork_musicbrainz
        _get_album = get_album_artwork_musicbrainz
    else:
        _get_artist = get_artist_artwork
        _get_album = get_album_artwork

    result = {
        "artist_poster": None,
        "artist_fanart": None,
        "artist_nfo": None,
        "album_poster": None,
        "album_nfo": None,
        "artist_image_url": None,
        "album_image_url": None,
    }

    def _log(msg):
        if log_callback:
            log_callback(msg)
        logger.info(msg)

    # --- Artist ---
    _log(f"Fetching artist artwork for: {artist} (source={source})")
    try:
        artist_data = _get_artist(artist, mb_artist_id=mb_artist_id)
        if not artist_data.get("image_url"):
            _log(f"No artist image found for: {artist}")

        artist_result = ensure_artist_artwork(
            artist=artist,
            image_url=artist_data.get("image_url"),
            fanart_url=artist_data.get("fanart_url"),
            bio=artist_data.get("bio"),
            genres=artist_data.get("genres"),
            mb_artist_id=mb_artist_id or artist_data.get("mb_artist_id"),
            overwrite=overwrite,
        )

        # If primary image failed to download, try fallback (e.g. Wikipedia)
        if not artist_result.get("poster_path") and artist_data.get("fallback_image_url"):
            _log(f"Primary artist image failed, trying fallback for: {artist}")
            artist_result = ensure_artist_artwork(
                artist=artist,
                image_url=artist_data["fallback_image_url"],
                fanart_url=artist_data.get("fanart_url"),
                bio=artist_data.get("bio"),
                genres=artist_data.get("genres"),
                mb_artist_id=mb_artist_id or artist_data.get("mb_artist_id"),
                overwrite=True,
            )

        result["artist_poster"] = artist_result.get("poster_path")
        result["artist_fanart"] = artist_result.get("fanart_path")
        result["artist_nfo"] = artist_result.get("nfo_path")
        result["artist_image_url"] = artist_data.get("image_url")

        if result["artist_poster"]:
            _log(f"Artist poster saved: {result['artist_poster']}")
        if result["artist_nfo"]:
            _log(f"Artist NFO saved: {result['artist_nfo']}")
    except Exception as e:
        _log(f"Artist artwork failed (non-fatal): {e}")

    # --- Album ---
    if album:
        _log(f"Fetching album artwork for: {album} by {artist} (source={source})")
        try:
            album_data = _get_album(album, artist, wiki_url=wiki_album_url) if source != "musicbrainz" and wiki_album_url else _get_album(album, artist)

            # CoverArtArchive direct ID lookup as FALLBACK only — the name-based
            # search above is authoritative (matches the scraper test pathway).
            # Only use pre-resolved MB IDs when the name search found no artwork.
            caa_url = None
            if not album_data.get("image_url") and source != "wikipedia":
                if mb_album_release_group_id:
                    from app.metadata.providers.coverartarchive import _fetch_front_cover_by_release_group
                    caa_url = _fetch_front_cover_by_release_group(mb_album_release_group_id)
                    if caa_url:
                        _log(f"CoverArtArchive fallback (release-group): {mb_album_release_group_id}")
                if not caa_url and mb_album_release_id:
                    from app.metadata.providers.coverartarchive import _fetch_front_cover
                    caa_url = _fetch_front_cover(mb_album_release_id)
                    if caa_url:
                        _log(f"CoverArtArchive fallback (release): {mb_album_release_id}")
                if caa_url:
                    album_data["image_url"] = caa_url
            if not album_data.get("image_url"):
                _log(f"No album cover found for: {album} by {artist}")

            album_overwrite = overwrite or bool(caa_url)

            album_result = ensure_album_artwork(
                artist=artist,
                album=album,
                image_url=album_data.get("image_url"),
                year=album_data.get("year"),
                genres=album_data.get("genres"),
                mb_release_id=mb_album_release_id or album_data.get("mb_release_id"),
                overwrite=album_overwrite,
            )
            result["album_poster"] = album_result.get("poster_path")
            result["album_nfo"] = album_result.get("nfo_path")
            result["album_image_url"] = album_data.get("image_url")

            if result["album_poster"]:
                _log(f"Album poster saved: {result['album_poster']}")
            if result["album_nfo"]:
                _log(f"Album NFO saved: {result['album_nfo']}")
        except Exception as e:
            _log(f"Album artwork failed (non-fatal): {e}")
    else:
        _log("No album info — skipping album artwork")

    return result
