"""
URL Utilities — normalize and canonicalize YouTube/Vimeo URLs,
extract provider and video ID, detect playlists.
"""
import re
from urllib.parse import urlparse, parse_qs, urlencode
from typing import Tuple, Optional
from app.models import SourceProvider


def is_playlist_url(url: str) -> bool:
    """
    Detect whether a URL points to a YouTube playlist.

    Returns True for:
    - youtube.com/playlist?list=PLxxxx
    - youtube.com/watch?v=xxx&list=PLxxxx  (video within a playlist context)
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not any(re.search(pat, host) for pat in [r"youtube\.com", r"youtu\.be"]):
        return False
    qs = parse_qs(parsed.query)
    return "list" in qs


def extract_playlist_id(url: str) -> Optional[str]:
    """Extract the playlist ID from a YouTube playlist URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    ids = qs.get("list", [])
    return ids[0] if ids else None


def identify_provider(url: str) -> Tuple[SourceProvider, str]:
    """
    Identify the source provider and extract the video ID from a URL.

    Returns:
        (provider, video_id)

    Raises:
        ValueError if URL is not a recognized provider.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or ""

    # --- YouTube ---
    yt_patterns = [
        r"(?:youtube\.com|youtube-nocookie\.com)",
        r"youtu\.be",
    ]
    if any(re.search(pat, host) for pat in yt_patterns):
        video_id = _extract_youtube_id(parsed, host, path)
        if video_id:
            return SourceProvider.youtube, video_id

    # --- Vimeo ---
    if "vimeo.com" in host:
        video_id = _extract_vimeo_id(path)
        if video_id:
            return SourceProvider.vimeo, video_id

    raise ValueError(f"Unrecognized video URL: {url}")


def _extract_youtube_id(parsed, host: str, path: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    # youtu.be/VIDEO_ID
    if "youtu.be" in host:
        return path.strip("/").split("/")[0] if path.strip("/") else None

    # youtube.com/watch?v=VIDEO_ID
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]

    # youtube.com/embed/VIDEO_ID
    embed_match = re.match(r"/embed/([a-zA-Z0-9_-]+)", path)
    if embed_match:
        return embed_match.group(1)

    # youtube.com/v/VIDEO_ID
    v_match = re.match(r"/v/([a-zA-Z0-9_-]+)", path)
    if v_match:
        return v_match.group(1)

    # youtube.com/shorts/VIDEO_ID
    shorts_match = re.match(r"/shorts/([a-zA-Z0-9_-]+)", path)
    if shorts_match:
        return shorts_match.group(1)

    # playlist-only URL (no video ID) — return the playlist ID prefixed to
    # distinguish from video IDs.  The caller (import endpoint) should have
    # already routed this through the playlist handler, but just in case:
    if "list" in qs:
        return None  # Signal playlist, handled upstream

    return None


def _extract_vimeo_id(path: str) -> Optional[str]:
    """Extract Vimeo video ID from path."""
    match = re.match(r"/(\d+)", path)
    return match.group(1) if match else None


def canonicalize_url(provider: SourceProvider, video_id: str) -> str:
    """Return the canonical URL for a given provider and video ID."""
    if provider == SourceProvider.youtube:
        return f"https://www.youtube.com/watch?v={video_id}"
    elif provider == SourceProvider.vimeo:
        return f"https://vimeo.com/{video_id}"
    else:
        return video_id
