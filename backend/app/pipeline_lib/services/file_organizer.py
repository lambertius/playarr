"""
File Organizer — Manages library directory structure, archive/replace logic.

Uses configured library_naming_pattern and library_folder_structure settings
for consistent naming across all import pipelines.

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
    Also strip trailing dots/spaces (Windows silently strips these).
    """
    name = name.replace("?", "-")
    name = ILLEGAL_CHARS.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(".")
    return name


def _build_version_suffix(
    version_type: str = "normal",
    alternate_version_label: str = "",
) -> str:
    """Build the version suffix string (Cover), (Live), etc."""
    if version_type == "cover":
        return " (Cover)"
    elif version_type == "live":
        return " (Live)"
    elif version_type == "18+":
        return " (18+)"
    elif version_type == "uncensored":
        return " (Uncensored)"
    elif version_type == "alternate" and alternate_version_label:
        return f" ({sanitize_filename(alternate_version_label)})"
    elif version_type == "alternate":
        return " (Alternate Version)"
    return ""


def apply_naming_pattern(
    pattern: str,
    artist: str,
    title: str,
    resolution_label: str = "",
    album: str = "",
    year: Optional[int] = None,
    version_type: str = "normal",
    alternate_version_label: str = "",
) -> str:
    """
    Apply a naming pattern with token substitution.

    Supported tokens: {artist}, {title}, {quality}, {album}, {year}
    The {title} token includes any version suffix (Cover/Live/Alternate).
    Empty tokens are cleaned up (empty brackets removed, double dashes collapsed).
    """
    suffix = _build_version_suffix(version_type, alternate_version_label)
    title_with_version = f"{sanitize_filename(title)}{suffix}"

    result = pattern.replace("{artist}", sanitize_filename(artist))
    result = result.replace("{title}", title_with_version)
    result = result.replace("{quality}", resolution_label or "")
    result = result.replace("{album}", sanitize_filename(album) if album else "")
    result = result.replace("{year}", str(year) if year else "")

    # Clean up empty brackets/separators left by missing tokens
    result = re.sub(r"\[\s*\]", "", result)       # Remove empty []
    result = re.sub(r"\(\s*\)", "", result)        # Remove empty ()
    result = re.sub(r"\s*-\s*-\s*", " - ", result) # Collapse double dashes
    result = re.sub(r"\s+", " ", result).strip()
    result = result.rstrip(" -").strip()

    return sanitize_filename(result)


def build_folder_name(
    artist: str,
    title: str,
    resolution_label: str,
    version_type: str = "normal",
    alternate_version_label: str = "",
) -> str:
    """
    Build the file base name using the configured naming pattern.

    Defaults to: Artist Name - Song Title [Resolution]
    """
    settings = get_settings()
    pattern = getattr(settings, "library_naming_pattern", "{artist} - {title} [{quality}]")
    return apply_naming_pattern(
        pattern, artist, title, resolution_label,
        version_type=version_type,
        alternate_version_label=alternate_version_label,
    )


def build_library_subpath(
    artist: str,
    title: str,
    resolution_label: str,
    album: str = "",
    version_type: str = "normal",
    alternate_version_label: str = "",
) -> str:
    """
    Build the relative path from library root to the video folder,
    using the configured folder structure.

    Default: {artist}/{file_folder}
    e.g. "Foo Fighters/Foo Fighters - Everlong [1080p]"

    Returns the relative path (no trailing separator).
    """
    settings = get_settings()
    folder_structure = getattr(settings, "library_folder_structure", "{artist}/{file_folder}")

    file_folder = build_folder_name(
        artist, title, resolution_label,
        version_type=version_type,
        alternate_version_label=alternate_version_label,
    )

    result = folder_structure.replace("{file_folder}", file_folder)
    result = result.replace("{artist}", sanitize_filename(artist))
    result = result.replace("{album}", sanitize_filename(album) if album else "Unknown Album")

    # Normalise separators and clean up any double slashes
    result = result.replace("\\", "/")
    result = re.sub(r"/+", "/", result).strip("/")

    # Convert to OS path
    return os.path.join(*result.split("/"))


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
    subpath = build_library_subpath(
        artist, title, resolution_label,
        version_type=version_type,
        alternate_version_label=alternate_version_label,
    )
    new_folder = os.path.join(library_dir, subpath)

    # Archive existing version if replacing
    if existing_folder and os.path.isdir(existing_folder):
        archive_folder(existing_folder)

    os.makedirs(new_folder, exist_ok=True)

    # Build new file name
    ext = os.path.splitext(source_file)[1]
    new_filename = f"{folder_name}{ext}"
    new_file_path = os.path.join(new_folder, new_filename)

    # NEVER overwrite existing files — refuse the import
    if os.path.isfile(new_file_path):
        raise FileExistsError(
            f"Target file already exists: {new_file_path}. "
            f"Refusing to overwrite — potential import clash."
        )

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

    Handles both flat structure (library/folder/video.ext) and nested
    artist subfolder structure (library/artist/folder/video.ext).

    Returns list of dicts: [{folder_path, file_path, folder_name}, ...]
    """
    settings = get_settings()
    video_extensions = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".mpg"}
    results = []

    for library_dir in settings.get_all_library_dirs():
        if not os.path.isdir(library_dir):
            continue

        for root, dirs, files in os.walk(library_dir):
            # Skip hidden/internal directories and archive folders
            dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("_") and d.lower() != "archive"]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in video_extensions:
                    results.append({
                        "folder_path": root,
                        "file_path": os.path.join(root, fname),
                        "folder_name": os.path.basename(root),
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
