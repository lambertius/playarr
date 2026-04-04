# Re-export from the canonical file_organizer so that pipeline_url obeys
# the same library_naming_pattern / library_folder_structure settings as
# every other pipeline.  Previously this was an independent (hardcoded) copy.
"""
File Organizer — thin re-export for pipeline_url.

All real logic lives in app.services.file_organizer.
"""

from app.services.file_organizer import (  # noqa: F401
    sanitize_filename,
    build_folder_name,
    build_library_subpath,
    apply_naming_pattern,
    organize_file,
    archive_folder,
    write_nfo_file,
    scan_library_directory,
    parse_folder_name,
)
