# AUTO-SEPARATED from services/file_organizer.py for pipeline_url pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
File Organizer — Manages library directory structure, archive/replace logic.

Library structure:
    Target\Artist Name - Song Title [Resolution]\Artist Name - Song Title [Resolution].<ext>

Archive structure:
    Archive\Artist Name - Song Title [Resolution]_YYYYMMDD_HHMMSS\<files>
"""
import logging
import os
import re
import shutil
import time
from datetime import datetime
from typing import Optional, Tuple

from app.config import get_settings

logger = logging.getLogger(__name__)

# Characters illegal in Windows filenames
ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*]')


def sanitize_filename(name: str) -> str:
    """
    Remove or replace characters illegal in Windows filenames.
    Replace '?' with '-', strip other illegal chars, collapse whitespace.
    """
    name = name.replace("?", "-")
    name = ILLEGAL_CHARS.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def build_folder_name(
    artist: str,
    title: str,
    resolution_label: str,
    version_type: str = "normal",
    alternate_version_label: str = "",
) -> str:
    """
    Build the standard folder/file base name:
        Artist Name - Song Title [Resolution]

    For special version types:
        Cover:     Performer - Title (Cover) [Resolution]
        Live:      Artist - Title (Live) [Resolution]
        Alternate: Artist - Title (Label) [Resolution]
    """
    clean_artist = sanitize_filename(artist)
    clean_title = sanitize_filename(title)

    # Build version suffix
    suffix = ""
    if version_type == "cover":
        suffix = " (Cover)"
    elif version_type == "live":
        suffix = " (Live)"
    elif version_type == "alternate" and alternate_version_label:
        suffix = f" ({sanitize_filename(alternate_version_label)})"
    elif version_type == "alternate":
        suffix = " (Alternate Version)"

    return f"{clean_artist} - {clean_title}{suffix} [{resolution_label}]"


def organize_file(
    source_file: str,
    artist: str,
    title: str,
    resolution_label: str,
    existing_folder: Optional[str] = None,
    version_type: str = "normal",
    alternate_version_label: str = "",
    target_dir: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Move a downloaded video file into the organized library structure.

    If existing_folder is provided, the old version is archived first.
    If target_dir is provided, use it instead of the default library_dir.

    Returns:
        (new_folder_path, new_file_path)
    """
    settings = get_settings()
    library_dir = target_dir or settings.library_dir

    folder_name = build_folder_name(artist, title, resolution_label,
                                    version_type=version_type,
                                    alternate_version_label=alternate_version_label)
    new_folder = os.path.join(library_dir, folder_name)

    # Archive existing version if replacing
    if existing_folder and os.path.isdir(existing_folder):
        archive_folder(existing_folder)

    os.makedirs(new_folder, exist_ok=True)

    # Build new file name
    ext = os.path.splitext(source_file)[1]
    new_filename = f"{folder_name}{ext}"
    new_file_path = os.path.join(new_folder, new_filename)

    # Handle collision
    if os.path.isfile(new_file_path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base, ext = os.path.splitext(new_filename)
        new_filename = f"{base}_{timestamp}{ext}"
        new_file_path = os.path.join(new_folder, new_filename)

    shutil.move(source_file, new_file_path)
    logger.info(f"Organized file: {new_file_path}")

    return new_folder, new_file_path


def archive_folder(folder_path: str) -> str:
    """
    Move an existing library folder to the archive directory.
    Appends timestamp if collision.
    Returns the archive destination path.
    """
    settings = get_settings()
    archive_dir = settings.archive_dir

    folder_name = os.path.basename(folder_path)
    archive_dest = os.path.join(archive_dir, folder_name)

    # Handle collision in archive
    if os.path.exists(archive_dest):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_dest = os.path.join(archive_dir, f"{folder_name}_{timestamp}")

    os.makedirs(archive_dir, exist_ok=True)
    shutil.move(folder_path, archive_dest)
    logger.info(f"Archived: {folder_path} -> {archive_dest}")

    return archive_dest


def write_nfo_file(
    folder_path: str,
    artist: str,
    title: str,
    album: str,
    year: Optional[int],
    genres: list,
    plot: str,
    source_url: str = "",
    resolution_label: str = "",
    version_type: str = "normal",
    alternate_version_label: str = "",
    original_artist: str = "",
    original_title: str = "",
) -> str:
    """
    Write a Kodi-compatible .nfo file for a music video.

    Returns path to the created .nfo file.
    """
    folder_name = (
        build_folder_name(artist, title, resolution_label,
                          version_type=version_type,
                          alternate_version_label=alternate_version_label)
        if resolution_label
        else f"{sanitize_filename(artist)} - {sanitize_filename(title)}"
    )
    nfo_filename = f"{folder_name}.nfo"
    nfo_path = os.path.join(folder_path, nfo_filename)

    genre_elements = "\n    ".join(f"<genre>{g}</genre>" for g in genres) if genres else ""

    year_str = str(year) if year else ""

    # Version metadata tags
    version_tags = ""
    if version_type != "normal":
        version_tags += f"\n    <tag>version:{version_type}</tag>"
    if alternate_version_label:
        version_tags += f"\n    <tag>version_label:{_xml_escape(alternate_version_label)}</tag>"
    if original_artist:
        version_tags += f"\n    <tag>original_artist:{_xml_escape(original_artist)}</tag>"
    if original_title:
        version_tags += f"\n    <tag>original_title:{_xml_escape(original_title)}</tag>"

    nfo_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<musicvideo>
    <title>{_xml_escape(title)}</title>
    <artist>{_xml_escape(artist)}</artist>
    <album>{_xml_escape(album or "")}</album>
    <year>{year_str}</year>
    {genre_elements}
    <plot>{_xml_escape(plot or "")}</plot>
    <source>{_xml_escape(source_url)}</source>{version_tags}
</musicvideo>
"""

    with open(nfo_path, "w", encoding="utf-8") as f:
        f.write(nfo_content)

    logger.info(f"Wrote NFO: {nfo_path}")
    return nfo_path


def _xml_escape(text: str) -> str:
    """Basic XML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def scan_library_directory() -> list:
    """
    Scan all library directories for folders that might contain music videos
    not yet tracked in the database.

    Returns list of dicts: [{folder_path, file_path, folder_name}, ...]
    """
    settings = get_settings()
    video_extensions = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".mpg"}
    results = []

    for library_dir in settings.get_all_library_dirs():
        if not os.path.isdir(library_dir):
            continue

        for entry in os.listdir(library_dir):
            folder_path = os.path.join(library_dir, entry)
            if not os.path.isdir(folder_path):
                continue

            for fname in os.listdir(folder_path):
                ext = os.path.splitext(fname)[1].lower()
                if ext in video_extensions:
                    results.append({
                        "folder_path": folder_path,
                        "file_path": os.path.join(folder_path, fname),
                        "folder_name": entry,
                    })
                    break  # One video per folder expected

    return results


def parse_folder_name(folder_name: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parse an organized folder name back into components.

    Input: "Artist Name - Song Title [1080p]"
    Input: "Artist Name - Song Title (Cover) [1080p]"
    Input: "Artist Name - Song Title (Uncensored) [1080p]"
    Returns: (artist, title, resolution_label)

    Note: The version suffix (Cover/Live/label) is kept as part of the title
    for backward compatibility.  The importer will detect version type separately.
    """
    match = re.match(r"^(.+?)\s*-\s*(.+?)\s*\[(\w+)\]$", folder_name)
    if match:
        return match.group(1).strip(), match.group(2).strip(), match.group(3).strip()
    # Try without resolution
    match = re.match(r"^(.+?)\s*-\s*(.+)$", folder_name)
    if match:
        return match.group(1).strip(), match.group(2).strip(), None
    return None, None, None
