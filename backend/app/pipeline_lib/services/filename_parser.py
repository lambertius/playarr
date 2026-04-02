# AUTO-SEPARATED from services/filename_parser.py for pipeline_lib pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Filename Parser — Extract artist/title from video filenames using regex patterns.

Provides built-in patterns for common naming conventions and supports
user-defined custom regex patterns with named capture groups.
"""
import logging
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Video file extensions recognized during import scanning
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".mpg", ".mpeg", ".m4v", ".flv", ".wmv"}


@dataclass
class ParsedFilename:
    """Metadata extracted from a video filename."""
    artist: Optional[str] = None
    title: Optional[str] = None
    resolution: Optional[str] = None
    year: Optional[int] = None
    # Which pattern was used
    pattern_name: Optional[str] = None


# Built-in patterns, ordered from most to least specific.
# Each pattern must use named groups: (?P<artist>...) and (?P<title>...)
# Optional groups: (?P<resolution>...), (?P<year>...)
BUILTIN_PATTERNS: List[Tuple[str, str]] = [
    # "Artist - Title [1080p]"  (Playarr / Kodi standard)
    ("artist_title_res", r"^(?P<artist>.+?)\s*-\s*(?P<title>.+?)\s*\[(?P<resolution>\w+)\]$"),
    # "Artist - Title (2020) [1080p]"
    ("artist_title_year_res", r"^(?P<artist>.+?)\s*-\s*(?P<title>.+?)\s*\((?P<year>\d{4})\)\s*\[(?P<resolution>\w+)\]$"),
    # "Artist - Title (1080p)"
    ("artist_title_res_paren", r"^(?P<artist>.+?)\s*-\s*(?P<title>.+?)\s*\((?P<resolution>\d{3,4}p)\)$"),
    # "Artist - Title"
    ("artist_title", r"^(?P<artist>.+?)\s*-\s*(?P<title>.+)$"),
    # "Title" (no artist separator — treat whole name as title)
    ("title_only", r"^(?P<title>.+)$"),
]


def parse_filename(filename: str, custom_pattern: Optional[str] = None) -> ParsedFilename:
    """
    Extract artist/title from a video filename.

    Args:
        filename: The filename (with or without extension).
        custom_pattern: Optional user-defined regex with named groups.

    Returns:
        ParsedFilename with whatever could be extracted.
    """
    # Strip extension if present
    base = os.path.splitext(filename)[0]
    base = base.strip()

    # Try custom pattern first
    if custom_pattern:
        try:
            m = re.match(custom_pattern, base)
            if m:
                gd = m.groupdict()
                return ParsedFilename(
                    artist=_clean(gd.get("artist")),
                    title=_clean(gd.get("title")),
                    resolution=gd.get("resolution"),
                    year=_parse_year(gd.get("year")),
                    pattern_name="custom",
                )
        except re.error as e:
            logger.warning(f"Invalid custom regex pattern: {e}")

    # Try built-in patterns
    for name, pattern in BUILTIN_PATTERNS:
        m = re.match(pattern, base)
        if m:
            gd = m.groupdict()
            result = ParsedFilename(
                artist=_clean(gd.get("artist")),
                title=_clean(gd.get("title")),
                resolution=gd.get("resolution"),
                year=_parse_year(gd.get("year")),
                pattern_name=name,
            )
            # Skip the title_only pattern if artist_title matches might work
            # (it's a fallback)
            if name == "title_only" and not result.artist:
                return result
            return result

    return ParsedFilename(title=base, pattern_name="fallback")


def scan_directory_for_videos(
    directory: str,
    recursive: bool = True,
) -> List[dict]:
    """
    Scan a directory for video files.

    Returns a list of dicts:
        [{
            "file_path": str,
            "folder_path": str,
            "folder_name": str,
            "filename": str,
            "file_size_bytes": int,
        }, ...]
    """
    results = []

    if not os.path.isdir(directory):
        return results

    if recursive:
        for root, dirs, files in os.walk(directory):
            # Skip hidden directories and Playarr internal directories
            dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("_")]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    full_path = os.path.join(root, fname)
                    results.append({
                        "file_path": full_path,
                        "folder_path": root,
                        "folder_name": os.path.basename(root),
                        "filename": fname,
                        "file_size_bytes": os.path.getsize(full_path),
                    })
    else:
        # Non-recursive: only look one level deep (folder/video pattern)
        for entry in os.listdir(directory):
            entry_path = os.path.join(directory, entry)
            if os.path.isdir(entry_path):
                for fname in os.listdir(entry_path):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in VIDEO_EXTENSIONS:
                        full_path = os.path.join(entry_path, fname)
                        results.append({
                            "file_path": full_path,
                            "folder_path": entry_path,
                            "folder_name": entry,
                            "filename": fname,
                            "file_size_bytes": os.path.getsize(full_path),
                        })
                        break  # one video per folder
            elif os.path.isfile(entry_path):
                ext = os.path.splitext(entry)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    results.append({
                        "file_path": entry_path,
                        "folder_path": directory,
                        "folder_name": os.path.basename(directory),
                        "filename": entry,
                        "file_size_bytes": os.path.getsize(entry_path),
                    })

    return results


def _clean(value: Optional[str]) -> Optional[str]:
    """Clean whitespace from extracted values."""
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def _parse_year(value: Optional[str]) -> Optional[int]:
    """Parse a year string to int."""
    if value and value.isdigit():
        y = int(value)
        if 1900 <= y <= 2099:
            return y
    return None
