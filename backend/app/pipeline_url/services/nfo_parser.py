# AUTO-SEPARATED from services/nfo_parser.py for pipeline_url pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
NFO Parser — Parse Kodi-style musicvideo NFO files for library import.

Handles the standard Kodi <musicvideo> XML format with fields:
    <title>, <artist>, <album>, <year>, <genre>, <plot>, <runtime>,
    <fileinfo>/<streamdetails>, <actor>, <dateadded>, etc.

Also handles the simpler Playarr-generated NFO format (subset of above).
"""
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedNFO:
    """Parsed metadata from a Kodi musicvideo .nfo file."""
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    genres: List[str] = field(default_factory=list)
    plot: Optional[str] = None
    runtime_minutes: Optional[int] = None
    source_url: Optional[str] = None
    date_added: Optional[str] = None
    # Stream details
    video_codec: Optional[str] = None
    video_width: Optional[int] = None
    video_height: Optional[int] = None
    audio_codec: Optional[str] = None
    audio_channels: Optional[int] = None


def parse_nfo_file(nfo_path: str) -> Optional[ParsedNFO]:
    """
    Parse a Kodi-style .nfo file and return structured metadata.

    Returns None if the file cannot be parsed.
    """
    if not os.path.isfile(nfo_path):
        return None

    try:
        with open(nfo_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Failed to read NFO file {nfo_path}: {e}")
        return None

    return parse_nfo_content(content, source_path=nfo_path)


def parse_nfo_content(content: str, source_path: str = "") -> Optional[ParsedNFO]:
    """Parse NFO XML content string into a ParsedNFO dataclass."""
    # Strip BOM and whitespace
    content = content.strip().lstrip("\ufeff")

    # Some NFOs have bare URLs (IMDB links) — skip those
    if content.startswith("http"):
        return None

    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.warning(f"XML parse error in {source_path}: {e}")
        return None

    if root.tag != "musicvideo":
        logger.debug(f"NFO root tag is <{root.tag}>, expected <musicvideo> — skipping {source_path}")
        return None

    result = ParsedNFO()

    result.title = _text(root, "title")
    result.artist = _text(root, "artist")
    result.album = _text(root, "album")

    year_str = _text(root, "year")
    if year_str and year_str.isdigit():
        result.year = int(year_str)

    # Genres — may be multiple <genre> elements or comma-separated in one
    for genre_el in root.findall("genre"):
        if genre_el.text:
            for g in genre_el.text.split(","):
                g = g.strip()
                if g:
                    result.genres.append(g)

    result.plot = _text(root, "plot")

    runtime_str = _text(root, "runtime")
    if runtime_str and runtime_str.isdigit():
        result.runtime_minutes = int(runtime_str)

    # Source URL — Playarr uses <source>, Kodi doesn't have a standard one
    result.source_url = _text(root, "source")

    result.date_added = _text(root, "dateadded")

    # Stream details
    stream = root.find(".//streamdetails")
    if stream is not None:
        video_el = stream.find("video")
        if video_el is not None:
            result.video_codec = _text(video_el, "codec")
            w = _text(video_el, "width")
            h = _text(video_el, "height")
            if w and w.isdigit():
                result.video_width = int(w)
            if h and h.isdigit():
                result.video_height = int(h)

        audio_el = stream.find("audio")
        if audio_el is not None:
            result.audio_codec = _text(audio_el, "codec")
            ch = _text(audio_el, "channels")
            if ch and ch.isdigit():
                result.audio_channels = int(ch)

    return result


def find_nfo_for_video(video_path: str) -> Optional[str]:
    """
    Find the NFO file associated with a video file.

    Looks for:
    1. Same name as the video with .nfo extension
    2. Any .nfo file in the same directory
    """
    folder = os.path.dirname(video_path)
    base = os.path.splitext(os.path.basename(video_path))[0]

    # Exact match first
    nfo_exact = os.path.join(folder, f"{base}.nfo")
    if os.path.isfile(nfo_exact):
        return nfo_exact

    # Fallback: any .nfo in the directory
    for f in os.listdir(folder):
        if f.lower().endswith(".nfo"):
            return os.path.join(folder, f)

    return None


def find_artwork_for_video(video_path: str) -> dict:
    """
    Find poster and thumb images associated with a video file.

    Returns dict with keys: poster, thumb (values are file paths or None).
    """
    folder = os.path.dirname(video_path)
    base = os.path.splitext(os.path.basename(video_path))[0]
    result = {"poster": None, "thumb": None}

    # Check for prefixed artwork (Kodi-style: basename-poster.jpg)
    for suffix, key in [("-poster", "poster"), ("-thumb", "thumb")]:
        for ext in (".jpg", ".jpeg", ".png"):
            candidate = os.path.join(folder, f"{base}{suffix}{ext}")
            if os.path.isfile(candidate):
                result[key] = candidate
                break

    # Check for generic artwork in folder
    if not result["poster"]:
        for name in ("poster.jpg", "poster.png", "folder.jpg", "folder.png"):
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                result["poster"] = candidate
                break

    if not result["thumb"]:
        for name in ("thumb.jpg", "thumb.png"):
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                result["thumb"] = candidate
                break

    return result


def _text(element: ET.Element, tag: str) -> Optional[str]:
    """Get text content of a child element, or None."""
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None
