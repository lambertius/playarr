"""
Settings API — Read/write global and per-user settings.
"""
import json
import logging
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppSetting, FileOperation, NormalizationHistory, ReviewCaseItem, VideoItem
from app.schemas import SettingOut, SettingUpdate, NormalizationHistoryOut

router = APIRouter(prefix="/api/settings", tags=["Settings"])

# Default settings with their types
DEFAULT_SETTINGS = {
    "library_dir": ("./data/library", "string"),
    "library_source_dirs": ("[]", "json"),
    "normalization_target_lufs": ("-14.0", "float"),
    "normalization_lra": ("7.0", "float"),
    "normalization_tp": ("-1.5", "float"),
    "preview_duration_sec": ("8", "int"),
    "preview_start_percent": ("30", "int"),
    "ai_provider": ("none", "string"),
    "auto_normalize_on_import": ("true", "bool"),
    "preferred_container": ("mkv", "string"),
    "transcode_audio_bitrate": ("256", "int"),
    "server.port": ("6969", "int"),
    "ai_source_resolution": ("true", "bool"),
    "ai_final_review": ("true", "bool"),
    "import_scrape_wikipedia": ("true", "bool"),
    "import_scrape_musicbrainz": ("true", "bool"),
    "import_ai_auto": ("false", "bool"),
    "import_ai_only": ("false", "bool"),
    "import_find_source_video": ("false", "bool"),
    "max_concurrent_downloads": ("4", "int"),
    "party_mode_exclusions": ('{"version_types":[],"artists":[],"genres":[],"albums":[],"min_song_rating":null,"min_video_rating":null}', "json"),
    "library_naming_pattern": ("{artist} - {title} [{quality}]", "string"),
    "library_folder_structure": ("{artist}/{file_folder}", "string"),
    # TMVDB integration
    "tmvdb_enabled": ("false", "bool"),
    "tmvdb_api_key": ("", "string"),
    "tmvdb_auto_pull": ("false", "bool"),
    "tmvdb_auto_push": ("false", "bool"),
    "import_scrape_tmvdb": ("false", "bool"),
    # Startup / system
    "startup_with_system": ("false", "bool"),
    "startup_delay_seconds": ("0", "int"),
    "auto_open_browser": ("true", "bool"),
    "minimize_to_tray": ("true", "bool"),
    "startup_duplicate_scan": ("false", "bool"),
}

SECRET_SETTING_KEYS = {
    "tmvdb_api_key", "openai_api_key", "gemini_api_key", "claude_api_key",
}


def _masked_setting_value(key: str, value: str) -> str:
    if key not in SECRET_SETTING_KEYS or not value:
        return value
    return f"••••{value[-4:]}"


def _setting_group(key: str) -> str:
    if key.startswith("tmvdb_") or key == "import_scrape_tmvdb":
        return "tmvdb"
    if key.startswith("ai_") or key.endswith("_api_key"):
        return "ai"
    if key.startswith("import_") or key == "max_concurrent_downloads":
        return "imports"
    if key.startswith("library_") or key in {"library_dir", "library_source_dirs"}:
        return "library_files"
    if key.startswith("party_"):
        return "tv_cast_party"
    if key.startswith("startup_") or key in {"server.port", "auto_open_browser", "minimize_to_tray"}:
        return "system"
    return "video_editor" if key.startswith(("normalization_", "transcode_", "preferred_container")) else "playback"


def _setting_consumers(key: str) -> list[str]:
    if key.startswith("tmvdb_"):
        return ["metadata.tmvdb.provider", "pipeline.tmvdb.policy"]
    if key.startswith("import_"):
        return ["pipeline.import_policy", "frontend.import_defaults"]
    if key.startswith("library_") or key in {"library_dir", "library_source_dirs"}:
        return ["file_organizer", "library_import", "archive_service"]
    if key.startswith("normalization_") or key == "auto_normalize_on_import":
        return ["pipeline.normalize"]
    if key == "max_concurrent_downloads":
        return ["pipeline.acquire.download_scheduler"]
    return ["app.runtime"]


@router.get("/", response_model=List[SettingOut])
def list_settings(user_id: Optional[str] = None, db: Session = Depends(get_db)):
    """List all settings (global or per-user)."""
    query = db.query(AppSetting)
    if user_id:
        query = query.filter(
            (AppSetting.user_id == user_id) | (AppSetting.user_id.is_(None))
        )
    else:
        query = query.filter(AppSetting.user_id.is_(None))

    settings = query.all()

    # Merge defaults for any missing keys
    existing_keys = {s.key for s in settings}
    result = [SettingOut(key=s.key, value=_masked_setting_value(s.key, s.value), value_type=s.value_type) for s in settings]

    for key, (default_val, val_type) in DEFAULT_SETTINGS.items():
        if key not in existing_keys:
            result.append(SettingOut(key=key, value=_masked_setting_value(key, default_val), value_type=val_type))

    return result


@router.put("/", response_model=SettingOut)
def update_setting(update: SettingUpdate, user_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Update or create a setting."""
    setting = db.query(AppSetting).filter(
        AppSetting.key == update.key,
        AppSetting.user_id == user_id,
    ).first()

    if update.key in SECRET_SETTING_KEYS and update.value.startswith("••••"):
        if setting:
            return SettingOut(
                key=setting.key,
                value=_masked_setting_value(setting.key, setting.value),
                value_type=setting.value_type,
            )
        raise HTTPException(422, "A masked value cannot be used as a new secret")
    if update.key == "max_concurrent_downloads":
        try:
            if not 1 <= int(update.value) <= 16:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "max_concurrent_downloads must be between 1 and 16")
    if update.key == "server.port":
        try:
            if not 1 <= int(update.value) <= 65535:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "server.port must be between 1 and 65535")
    if update.key == "tmvdb_auto_push" and update.value == "true":
        enabled = db.query(AppSetting).filter(AppSetting.key == "tmvdb_enabled", AppSetting.user_id == user_id).first()
        api_key = db.query(AppSetting).filter(AppSetting.key == "tmvdb_api_key", AppSetting.user_id == user_id).first()
        if not enabled or enabled.value != "true" or not api_key or not api_key.value:
            raise HTTPException(422, "TMVDB auto-push requires TMVDB to be enabled with an API key")

    if setting:
        setting.value = update.value
        setting.value_type = update.value_type
        setting.revision = (setting.revision or 1) + 1
    else:
        setting = AppSetting(
            user_id=user_id,
            key=update.key,
            value=update.value,
            value_type=update.value_type,
        )
        db.add(setting)

    # Keep mutually exclusive import modes valid for old and new clients.
    exclusive_updates = {}
    if update.key == "import_ai_auto" and update.value == "true":
        exclusive_updates["import_ai_only"] = "false"
    elif update.key == "import_ai_only" and update.value == "true":
        exclusive_updates.update({
            "import_ai_auto": "false", "import_scrape_wikipedia": "false",
            "import_scrape_musicbrainz": "false",
        })
    elif update.key in {"import_scrape_wikipedia", "import_scrape_musicbrainz"} and update.value == "true":
        exclusive_updates["import_ai_only"] = "false"
    for key, value in exclusive_updates.items():
        row = db.query(AppSetting).filter(AppSetting.key == key, AppSetting.user_id == user_id).first()
        if row:
            row.value = value
            row.revision = (row.revision or 1) + 1

    db.commit()
    db.refresh(setting)

    # Sync directory settings to the pydantic config singleton
    _sync_dir_setting_to_config(update.key, update.value)

    # Ensure critical subdirectories when library_dir changes
    if update.key == "library_dir":
        from app.config import ensure_library_subdirs
        ensure_library_subdirs(update.value)

    return SettingOut(
        key=setting.key,
        value=_masked_setting_value(setting.key, setting.value),
        value_type=setting.value_type,
    )


@router.get("/catalogue")
def settings_catalogue(db: Session = Depends(get_db)):
    """Typed registry plus a consumer/orphan audit for Diagnostics."""
    definitions = []
    for key, (default, value_type) in sorted(DEFAULT_SETTINGS.items()):
        definitions.append({
            "key": key,
            "value_type": value_type,
            "default": None if key in SECRET_SETTING_KEYS else default,
            "group": _setting_group(key),
            "scope": "instance",
            "restart_required": key in {"server.port", "startup_with_system"},
            "secret": key in SECRET_SETTING_KEYS,
            "dependencies": {
                "tmvdb_auto_push": ["tmvdb_enabled", "tmvdb_api_key"],
                "tmvdb_auto_pull": ["tmvdb_enabled"],
            }.get(key, []),
            "deprecated": False,
            "consumers": _setting_consumers(key),
        })
    known = set(DEFAULT_SETTINGS)
    database_keys = {key for (key,) in db.query(AppSetting.key).all()}
    return {
        "definitions": definitions,
        "audit": {
            "orphaned_database_keys": sorted(database_keys - known),
            "visible_without_consumers": [row["key"] for row in definitions if not row["consumers"]],
        },
    }


@router.get("/defaults")
def get_defaults():
    """Return platform-appropriate default directory values."""
    from app.runtime_dirs import RuntimeDirs
    rdirs = RuntimeDirs()
    return {
        "library_dir": str(rdirs.library_dir),
    }


def _sync_dir_setting_to_config(key: str, value: str) -> None:
    """Keep the cached pydantic Settings in sync with DB for directory/naming keys."""
    from app.config import get_settings
    _sync_keys = {"library_dir", "library_source_dirs",
                  "library_naming_pattern", "library_folder_structure"}
    if key in _sync_keys:
        settings = get_settings()
        setattr(settings, key, value)


# ---------------------------------------------------------------------------
# Source directories — save, auto-import new, auto-clean removed
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


class SourceDirsUpdate(BaseModel):
    dirs: List[str]


class SourceDirsResponse(BaseModel):
    saved: bool
    added_dirs: List[str]
    removed_dirs: List[str]
    import_job_id: Optional[int] = None
    cleaned_count: int = 0


@router.put("/source-directories", response_model=SourceDirsResponse)
def update_source_directories(body: SourceDirsUpdate, db: Session = Depends(get_db)):
    """
    Save source directories, auto-import videos from newly added dirs,
    and auto-clean videos from removed dirs.
    """
    from app.config import get_settings
    from app.models import ProcessingJob, JobStatus, VideoItem, MediaAsset, Source, Genre
    from app.services.file_organizer import parse_folder_name
    from app.services.nfo_parser import find_nfo_for_video, parse_nfo_file, find_artwork_for_video
    from app.tasks import extract_quality_signature, derive_resolution_label, _get_or_create_genre
    from app.models import QualitySignature
    from datetime import datetime, timezone

    new_dirs = [d.strip() for d in body.dirs if d.strip()]

    # Read the old value from DB
    old_setting = db.query(AppSetting).filter(
        AppSetting.key == "library_source_dirs",
        AppSetting.user_id.is_(None),
    ).first()
    old_dirs: List[str] = []
    if old_setting:
        try:
            old_dirs = json.loads(old_setting.value)
        except (json.JSONDecodeError, TypeError):
            pass

    # Persist to DB
    new_value = json.dumps(new_dirs)
    if old_setting:
        old_setting.value = new_value
        old_setting.value_type = "json"
    else:
        old_setting = AppSetting(
            user_id=None,
            key="library_source_dirs",
            value=new_value,
            value_type="json",
        )
        db.add(old_setting)
    db.flush()

    # Sync to the pydantic config singleton so get_all_library_dirs() works
    settings = get_settings()
    settings.library_source_dirs = new_value

    # Compute diffs
    old_set = set(os.path.normcase(os.path.normpath(d)) for d in old_dirs)
    new_set = set(os.path.normcase(os.path.normpath(d)) for d in new_dirs)
    added = [d for d in new_dirs if os.path.normcase(os.path.normpath(d)) not in old_set]
    removed = [d for d in old_dirs if os.path.normcase(os.path.normpath(d)) not in new_set]

    import_job_id = None
    cleaned_count = 0

    # --- Ensure critical subdirectories for newly added source dirs ---
    if added:
        from app.config import ensure_library_subdirs
        for add_dir in added:
            if os.path.isdir(add_dir):
                ensure_library_subdirs(add_dir)

    # --- Auto-import from newly added directories ---
    if added:
        video_extensions = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".mpg"}
        new_count = 0
        for add_dir in added:
            if not os.path.isdir(add_dir):
                logger.warning(f"Source directory not found: {add_dir}")
                continue
            for entry_name in os.listdir(add_dir):
                folder_path = os.path.join(add_dir, entry_name)
                if not os.path.isdir(folder_path):
                    continue
                # Already tracked?
                existing = db.query(VideoItem).filter(
                    VideoItem.folder_path == folder_path,
                ).first()
                if existing:
                    continue
                # Find a video file in the folder
                video_file = None
                for fname in os.listdir(folder_path):
                    if os.path.splitext(fname)[1].lower() in video_extensions:
                        video_file = os.path.join(folder_path, fname)
                        break
                if not video_file:
                    continue
                # Parse folder name for metadata (baseline)
                artist, title, res_label = parse_folder_name(entry_name)
                if not artist:
                    artist = "Unknown Artist"
                if not title:
                    title = entry_name

                # Enrich from local NFO if available
                album = None
                year = None
                genres: List[str] = []
                plot = None
                source_url = None
                nfo_path = find_nfo_for_video(video_file)
                if nfo_path:
                    nfo = parse_nfo_file(nfo_path)
                    if nfo:
                        if nfo.artist:
                            artist = nfo.artist
                        if nfo.title:
                            title = nfo.title
                        album = nfo.album
                        year = nfo.year
                        genres = nfo.genres or []
                        plot = nfo.plot
                        source_url = nfo.source_url
                        logger.info(f"Enriched from NFO: {nfo_path}")

                # Create VideoItem (local-only, no scraping)
                video_item = VideoItem(
                    artist=artist,
                    title=title,
                    album=album,
                    year=year,
                    plot=plot,
                    folder_path=folder_path,
                    file_path=video_file,
                    resolution_label=res_label,
                    file_size_bytes=os.path.getsize(video_file) if os.path.isfile(video_file) else None,
                    import_method="scanned",
                    song_rating=3,
                    video_rating=3,
                )
                db.add(video_item)
                db.flush()

                # Genres
                for g in genres:
                    genre_obj = _get_or_create_genre(db, g)
                    if genre_obj not in video_item.genres:
                        video_item.genres.append(genre_obj)

                # Source link from NFO
                if source_url:
                    try:
                        from app.services.url_utils import identify_provider, canonicalize_url
                        provider, vid_id = identify_provider(source_url)
                        canonical = canonicalize_url(provider, vid_id)
                        existing_source = db.query(Source).filter(
                            Source.provider == provider,
                            Source.source_video_id == vid_id,
                        ).first()
                        if not existing_source:
                            db.add(Source(
                                video_id=video_item.id,
                                provider=provider,
                                source_video_id=vid_id,
                                original_url=source_url,
                                canonical_url=canonical,
                                provenance="nfo_import",
                                source_type="single",
                            ))
                    except Exception:
                        pass  # URL may not be a recognised provider

                # Local artwork → MediaAsset records
                artwork = find_artwork_for_video(video_file)
                for asset_type in ("poster", "thumb"):
                    art_path = artwork.get(asset_type)
                    if art_path and os.path.isfile(art_path):
                        db.add(MediaAsset(
                            video_id=video_item.id,
                            asset_type=asset_type,
                            file_path=art_path,
                            provenance="local_file",
                            status="valid",
                        ))

                # Analyze quality via ffprobe
                try:
                    sig = extract_quality_signature(video_file)
                    qs = db.query(QualitySignature).filter(
                        QualitySignature.video_id == video_item.id
                    ).first()
                    if not qs:
                        qs = QualitySignature(video_id=video_item.id)
                        db.add(qs)
                    for k, v in sig.items():
                        if hasattr(qs, k):
                            setattr(qs, k, v)
                    video_item.resolution_label = derive_resolution_label(sig.get("height"))
                except Exception as e:
                    logger.warning(f"Quality analysis failed for {entry_name}: {e}")
                new_count += 1
                logger.info(f"Auto-imported from new source dir: {artist} - {title}")
        if new_count:
            logger.info(f"Auto-imported {new_count} video(s) from {len(added)} new source dir(s)")

    # --- Auto-clean videos from removed directories ---
    if removed:
        from app.routers.library import _robust_rmtree, _delete_video_thumbnail_dir, _delete_video_previews
        norm_removed = [os.path.normcase(os.path.normpath(d)) for d in removed]
        # Find all videos whose folder_path is inside a removed dir
        all_videos = db.query(VideoItem).all()
        to_delete = []
        for v in all_videos:
            if not v.folder_path:
                continue
            norm_fp = os.path.normcase(os.path.normpath(v.folder_path))
            for nr in norm_removed:
                if norm_fp.startswith(nr + os.sep) or norm_fp == nr:
                    to_delete.append(v)
                    break
        for v in to_delete:
            vid = v.id
            folder = v.folder_path
            file_base = os.path.splitext(os.path.basename(v.file_path))[0] if v.file_path else None
            db.delete(v)
            db.flush()
            _delete_video_thumbnail_dir(vid)
            _delete_video_previews(vid, file_base)
            logger.info(f"Auto-cleaned video {vid} from removed source dir: {folder}")
        cleaned_count = len(to_delete)
        if cleaned_count:
            logger.info(f"Auto-cleaned {cleaned_count} video(s) from {len(removed)} removed source dir(s)")

    db.commit()
    return SourceDirsResponse(
        saved=True,
        added_dirs=added,
        removed_dirs=removed,
        cleaned_count=cleaned_count,
    )


@router.get("/normalization-history", response_model=List[NormalizationHistoryOut])
def get_all_normalization_history(db: Session = Depends(get_db)):
    """Get all normalization history records (most recent first)."""
    records = (
        db.query(NormalizationHistory)
        .order_by(NormalizationHistory.created_at.desc())
        .limit(100)
        .all()
    )
    return records


@router.get("/normalization-history/{video_id}", response_model=List[NormalizationHistoryOut])
def get_normalization_history(video_id: int, db: Session = Depends(get_db)):
    """Get normalization history for a video item."""
    records = (
        db.query(NormalizationHistory)
        .filter(NormalizationHistory.video_id == video_id)
        .order_by(NormalizationHistory.created_at.desc())
        .all()
    )
    return records


@router.get("/browse-directories")
def browse_directories():
    """Open a native OS folder picker dialog and return the selected path."""
    if sys.platform == "win32":
        selected = _win32_browse_folder()
    else:
        selected = _tkinter_browse_folder()

    if not selected:
        return {"path": ""}
    return {"path": os.path.normpath(selected)}


def _win32_browse_folder() -> str:
    """Modern Windows folder picker using IFileOpenDialog (Explorer-style)."""
    # Use PowerShell to invoke IFileOpenDialog via .NET COM interop.
    # This gives the full Explorer window with navigation pane, breadcrumbs, etc.
    ps_script = r'''
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
public class FileOpenDialogCOM { }

[ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IFileDialog {
    [PreserveSig] int Show(IntPtr hwndOwner);
    void SetFileTypes(uint c, IntPtr f);
    void SetFileTypeIndex(uint i);
    void GetFileTypeIndex(out uint i);
    void Advise(IntPtr e, out uint c);
    void Unadvise(uint c);
    void SetOptions(uint o);
    void GetOptions(out uint o);
    void SetDefaultFolder(IShellItem f);
    void SetFolder(IShellItem f);
    void GetFolder(out IShellItem f);
    void GetCurrentSelection(out IShellItem s);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string n);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string n);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string t);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string t);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string t);
    void GetResult(out IShellItem i);
    void AddPlace(IShellItem s, int a);
    void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string e);
    void Close(int hr);
    void SetClientGuid(ref Guid g);
    void ClearClientData();
    void SetFilter(IntPtr f);
}

[ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IShellItem {
    void BindToHandler(IntPtr p, ref Guid b, ref Guid r, out IntPtr v);
    void GetParent(out IShellItem i);
    void GetDisplayName(uint n, [MarshalAs(UnmanagedType.LPWStr)] out string s);
    void GetAttributes(uint m, out uint a);
    void Compare(IShellItem i, uint h, out int o);
}

public static class FolderPicker {
    public static string Pick() {
        IFileDialog d = (IFileDialog)new FileOpenDialogCOM();
        d.SetOptions(0x20 | 0x40);
        d.SetTitle("Select Directory");
        if (d.Show(IntPtr.Zero) != 0) return "";
        IShellItem r; d.GetResult(out r);
        string p; r.GetDisplayName(0x80058000u, out p);
        return p ?? "";
    }
}
"@ -ReferencedAssemblies System.Runtime.InteropServices

Write-Output ([FolderPicker]::Pick())
'''
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        path = result.stdout.strip()
        if path:
            return path
    except Exception:
        pass
    # Final fallback
    return _powershell_browse_folder()


def _powershell_browse_folder() -> str:
    """Fallback Explorer-style folder picker via PowerShell .NET dialog."""
    try:
        ps_script = (
            "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.Description = 'Select Directory'; "
            "$d.ShowNewFolderButton = $true; "
            "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath } else { '' }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _tkinter_browse_folder() -> str:
    """Tkinter folder picker fallback (for non-Windows or dev mode)."""
    script = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "path = filedialog.askdirectory(title='Select Directory')\n"
        "print(path)\n"
        "root.destroy()\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return result.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Naming convention preview
# ---------------------------------------------------------------------------

class NamingPreviewRequest(BaseModel):
    naming_pattern: str = "{artist} - {title} [{quality}]"
    folder_structure: str = "{artist}/{file_folder}"


class NamingPreviewResponse(BaseModel):
    examples: List[dict]


@router.post("/naming-preview", response_model=NamingPreviewResponse)
def naming_preview(body: NamingPreviewRequest):
    """Generate example paths using the given naming pattern and folder structure."""
    from app.services.file_organizer import apply_naming_pattern, sanitize_filename

    sample_videos = [
        {"artist": "Foo Fighters", "title": "Everlong", "album": "The Colour and the Shape",
         "quality": "1080p", "year": 1997, "version_type": "normal", "ext": ".mkv"},
        {"artist": "Daft Punk", "title": "Around the World", "album": "Homework",
         "quality": "720p", "year": 1997, "version_type": "normal", "ext": ".mp4"},
        {"artist": "Johnny Cash", "title": "Hurt", "album": "American IV",
         "quality": "1080p", "year": 2002, "version_type": "cover", "ext": ".mkv"},
    ]

    examples = []
    for v in sample_videos:
        file_base = apply_naming_pattern(
            body.naming_pattern, v["artist"], v["title"], v["quality"],
            album=v["album"], year=v["year"], version_type=v["version_type"],
        )

        folder_structure = body.folder_structure.replace("{file_folder}", file_base)
        folder_structure = folder_structure.replace("{artist}", sanitize_filename(v["artist"]))
        folder_structure = folder_structure.replace("{album}", sanitize_filename(v["album"]) if v["album"] else "Unknown Album")
        folder_structure = folder_structure.replace("\\", "/")

        full_path = f"{folder_structure}/{file_base}{v['ext']}"

        examples.append({
            "artist": v["artist"],
            "title": v["title"],
            "version_type": v["version_type"],
            "path": full_path,
        })


RESTART_EXIT_CODE = 75  # Special exit code that _start_server.py interprets as "restart"


@router.post("/restart")
def restart_server():
    """Restart the Playarr server process."""
    logger.info("Server restart requested via API")

    def _do_exit():
        import time
        time.sleep(0.5)  # Allow response to flush
        os._exit(RESTART_EXIT_CODE)

    threading.Thread(target=_do_exit, daemon=True).start()
    return {"status": "restarting"}


# Exit code the supervisor treats as a clean stop (do not relaunch).
SHUTDOWN_EXIT_CODE = 0


@router.post("/shutdown")
def shutdown_server():
    """Cleanly stop Playarr (used by the installer to release the executable
    before an update, and available for a graceful stop).  Exits with code 0 so
    the supervisor stops rather than relaunching."""
    logger.info("Server shutdown requested via API")

    def _do_exit():
        import time
        time.sleep(0.5)  # Allow response to flush
        os._exit(SHUTDOWN_EXIT_CODE)

    threading.Thread(target=_do_exit, daemon=True).start()
    return {"status": "shutting down"}

    return NamingPreviewResponse(examples=examples)


# ─── Open a directory in the OS file manager ─────────────────────

class OpenDirectoryRequest(BaseModel):
    path: str


@router.post("/open-directory")
def open_directory(body: OpenDirectoryRequest):
    """Open a directory in the OS file manager.

    Only allows opening directories that actually exist.
    """
    target = os.path.normpath(os.path.abspath(body.path))
    if not os.path.isdir(target):
        raise HTTPException(404, f"Directory does not exist: {target}")

    if sys.platform == "win32":
        subprocess.Popen(["explorer", target])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])

    return {"ok": True, "path": target}


# ---------------------------------------------------------------------------
# Windows Startup Management
# ---------------------------------------------------------------------------

def _get_setting_value(db: Session, key: str) -> str | None:
    """Read a single setting value from the DB, falling back to defaults."""
    row = db.query(AppSetting).filter(AppSetting.key == key, AppSetting.user_id.is_(None)).first()
    if row:
        return row.value
    default = DEFAULT_SETTINGS.get(key)
    return default[0] if default else None


@router.get("/startup")
def get_startup_status():
    """Check if Playarr is registered in the Windows startup registry."""
    if sys.platform != "win32":
        return {"registered": False, "command": None, "platform": sys.platform}
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Playarr")
            return {"registered": True, "command": value}
    except OSError:
        return {"registered": False, "command": None}


# ---------------------------------------------------------------------------
# Archive Management
# ---------------------------------------------------------------------------

class ArchiveItemOut(BaseModel):
    path: str
    folder: str
    reason: str = "edit"
    artist: str = ""
    title: str = ""
    video_id: Optional[int] = None
    archived_at: str = ""
    file_size_bytes: int = 0
    original_path: Optional[str] = None
    checksum_md5: Optional[str] = None
    checksum_sha256: Optional[str] = None; playarr_video_id: Optional[str] = None
    operation_id: Optional[str] = None
    manifest_schema_version: Optional[int] = None
    restore_eligible: bool = True
    integrity_status: str = "unchecked"

def _archive_roots() -> list[tuple[str, str]]:
    from app.config import get_settings as _get_settings
    return [
        (os.path.normpath(root), os.path.normpath(os.path.join(root, "_archive")))
        for root in _get_settings().get_all_library_dirs()
    ]

def _validate_archive_folder(folder: str) -> tuple[str, str]:
    candidate = os.path.normcase(os.path.normpath(folder))
    for library_root, archive_root in _archive_roots():
        allowed = os.path.normcase(archive_root)
        if candidate == allowed or candidate.startswith(allowed + os.sep):
            return library_root, archive_root
    raise HTTPException(403, "Path is not inside archive directory")

def _archive_plan(folder: str, db: Session) -> dict:
    from app.routers.video_editor import (
        _MANIFEST_NAME, _VIDEO_EXTS, _file_checksum,
        _manifest_video_path, _read_folder_manifest,
    )
    from app.services.archive_identity import manifest_checksum, manifest_video_stable_id, resolve_manifest_video
    library_root, _archive_root = _validate_archive_folder(folder)
    if not os.path.isdir(folder):
        raise HTTPException(404, "Archive folder not found")
    manifest_path = os.path.join(folder, _MANIFEST_NAME)
    manifest = _read_folder_manifest(folder) or {}
    archive_file = _manifest_video_path(folder, manifest) if manifest else None
    if archive_file is None:
        candidates = [
            os.path.join(folder, name) for name in os.listdir(folder)
            if os.path.splitext(name)[1].lower() in _VIDEO_EXTS
        ]
        archive_file = candidates[0] if candidates else None
    if archive_file is None:
        raise HTTPException(404, "No video file found in archive folder")

    video = resolve_manifest_video(db, manifest)
    relative = manifest.get("original_relative_path")
    original_path = os.path.join(library_root, relative) if relative else None
    current_path = video.file_path if video and video.file_path else original_path
    checksum_algorithm, expected_checksum = manifest_checksum(manifest)
    actual_checksum = _file_checksum(archive_file, checksum_algorithm) if expected_checksum else None
    checksum_matches = actual_checksum == expected_checksum if expected_checksum else None
    review_cases = []
    if video:
        review_cases = [
            row.case_id for row in db.query(ReviewCaseItem).filter(
                ReviewCaseItem.video_id == video.id,
            ).all()
        ]
    companions = [
        name for name in os.listdir(folder)
        if os.path.join(folder, name) not in (archive_file, manifest_path)
    ]
    return {
        "folder": folder,
        "archive_path": archive_file,
        "original_path": original_path,
        "current_path": current_path,
        "current_exists": bool(current_path and os.path.isfile(current_path)),
        "archive_checksum": actual_checksum or expected_checksum,
        "archive_checksum_algorithm": checksum_algorithm,
        "archive_checksum_md5": (actual_checksum or expected_checksum) if checksum_algorithm == "md5" else None,
        "archive_checksum_sha256": (actual_checksum or expected_checksum) if checksum_algorithm == "sha256" else None,
        "checksum_matches_manifest": checksum_matches,
        "manifest_schema_version": manifest.get("schema_version", 1 if manifest else None),
        "video_id": video.id if video else manifest.get("video_id"),
        "playarr_video_id": video.playarr_video_id if video else manifest_video_stable_id(manifest),
        "video_stable_id": video.stable_id if video else manifest_video_stable_id(manifest),
        "archive_operation_id": manifest.get("operation_id"),
        "expected_video_revision": video.revision if video else None,
        "metadata_revision_consequence": "video revision increments after restored media is re-analysed",
        "companion_files": companions,
        "related_review_case_ids": review_cases,
        "conflict_choices": ["archive_current", "replace_current"] if current_path and os.path.isfile(current_path) else [],
        "restore_eligible": checksum_matches is not False,
    }

@router.get("/archive-items", response_model=List[ArchiveItemOut])
def list_archive_items(db: Session = Depends(get_db)):
    """List all items in the archive directory with manifest metadata."""
    from app.config import get_settings as _get_settings
    from app.routers.video_editor import (
        _MANIFEST_NAME, _VIDEO_EXTS, _read_folder_manifest, _manifest_video_path)
    from app.services.archive_identity import manifest_video_stable_id, resolve_manifest_video
    _settings = _get_settings()

    results: list[dict] = []
    for lib_root in _settings.get_all_library_dirs():
        archive_dir = os.path.join(lib_root, "_archive")
        if not os.path.isdir(archive_dir):
            continue
        for root, _dirs, fnames in os.walk(archive_dir):
            # Read manifest if present
            meta: dict = _read_folder_manifest(root) or {}
            # Prefer the manifest-recorded TRUE original; only fall back to the
            # first video file for legacy manifest-less folders.  Picking the
            # first os.walk entry could surface a re-encode intermediate that
            # was timestamp-archived alongside the original.
            video_file = _manifest_video_path(root, meta) if meta else None
            if not video_file:
                for fn in fnames:
                    if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS:
                        video_file = os.path.join(root, fn)
                        break
            if not video_file:
                continue
            video = resolve_manifest_video(db, meta)
            relative = meta.get("original_relative_path")
            original_path = video.file_path if video else (
                os.path.join(lib_root, relative) if relative else None
            )
            schema_version = meta.get("schema_version", 1 if meta else None)
            stable_identity = bool((video and video.stable_id) or manifest_video_stable_id(meta))
            integrity_status = (
                "orphaned_owner" if meta.get("video_id") and not video
                else "ok" if meta and stable_identity
                else "legacy_manifest" if meta
                else "missing_manifest"
            )
            results.append({
                "path": video_file,
                "folder": root,
                "reason": meta.get("archive_reason", "edit"),
                "artist": meta.get("artist", ""),
                "title": meta.get("title", ""),
                "video_id": meta.get("video_id"),
                "archived_at": meta.get("archived_at", ""),
                "file_size_bytes": meta.get("file_size_bytes", 0)
                                   or (os.path.getsize(video_file) if os.path.isfile(video_file) else 0),
                "original_path": original_path,
                "checksum_md5": meta.get("checksum_md5"),
                "checksum_sha256": meta.get("checksum_sha256"),
                "playarr_video_id": manifest_video_stable_id(meta),
                "operation_id": meta.get("operation_id"),
                "manifest_schema_version": schema_version,
                "restore_eligible": bool(os.path.isfile(video_file)),
                "integrity_status": integrity_status,
            })
    results.sort(key=lambda r: r.get("archived_at", ""), reverse=True)
    return results

class DeleteArchiveRequest(BaseModel):
    folders: List[str]

@router.post("/archive-delete")
def delete_archive_items(body: DeleteArchiveRequest):
    """Delete specific archive folders."""
    deleted = 0
    errors: list[str] = []
    from app.config import get_settings as _get_settings
    _settings = _get_settings()
    # Build set of allowed archive roots for path traversal protection
    allowed_roots = set()
    for lib_root in _settings.get_all_library_dirs():
        allowed_roots.add(os.path.normcase(os.path.normpath(os.path.join(lib_root, "_archive"))))

    for folder in body.folders:
        norm_folder = os.path.normcase(os.path.normpath(folder))
        # Validate the folder is inside an archive directory
        if not any(norm_folder.startswith(ar + os.sep) or norm_folder == ar for ar in allowed_roots):
            errors.append(f"Not inside archive: {folder}")
            continue
        if os.path.isdir(folder):
            try:
                from app.safe_delete import safe_delete, NetworkDeleteError
                try:
                    safe_delete(folder)
                except NetworkDeleteError:
                    safe_delete(folder, force_permanent=True)
                deleted += 1
            except Exception as e:
                errors.append(f"{folder}: {e}")
        else:
            errors.append(f"Not found: {folder}")
    return {"deleted": deleted, "errors": errors}

@router.post("/archive-clear")
def clear_archive():
    """Delete ALL items in the archive directory."""
    from app.config import get_settings as _get_settings
    _settings = _get_settings()
    deleted = 0
    errors: list[str] = []
    for lib_root in _settings.get_all_library_dirs():
        archive_dir = os.path.join(lib_root, "_archive")
        if not os.path.isdir(archive_dir):
            continue
        for entry in os.listdir(archive_dir):
            entry_path = os.path.join(archive_dir, entry)
            try:
                from app.safe_delete import safe_delete, NetworkDeleteError
                try:
                    safe_delete(entry_path)
                except NetworkDeleteError:
                    safe_delete(entry_path, force_permanent=True)
                deleted += 1
            except Exception as e:
                errors.append(f"{entry_path}: {e}")
    return {"deleted": deleted, "errors": errors}


@router.post("/archive-clean-stale")
def clean_stale_archives(body: DeleteArchiveRequest):
    """Delete archive folders that no longer contain a video file."""
    from app.config import get_settings as _get_settings
    from app.routers.video_editor import _VIDEO_EXTS
    _settings = _get_settings()
    allowed_roots = set()
    for lib_root in _settings.get_all_library_dirs():
        allowed_roots.add(os.path.normcase(os.path.normpath(os.path.join(lib_root, "_archive"))))

    deleted = 0
    errors: list[str] = []
    for folder in body.folders:
        norm_folder = os.path.normcase(os.path.normpath(folder))
        if not any(norm_folder.startswith(ar + os.sep) or norm_folder == ar for ar in allowed_roots):
            errors.append(f"Not inside archive: {folder}")
            continue
        if not os.path.isdir(folder):
            errors.append(f"Not found: {folder}")
            continue
        # Only delete if the folder truly has no video file
        has_video = any(
            os.path.splitext(f.name)[1].lower() in _VIDEO_EXTS
            for f in os.scandir(folder) if f.is_file()
        )
        if has_video:
            errors.append(f"Still has video: {folder}")
            continue
        try:
            from app.safe_delete import safe_delete, NetworkDeleteError
            try:
                safe_delete(folder)
            except NetworkDeleteError:
                safe_delete(folder, force_permanent=True)
            deleted += 1
        except Exception as e:
            errors.append(f"{folder}: {e}")
    return {"deleted": deleted, "errors": errors}

class RestoreArchiveRequest(BaseModel):
    folder: str
    operation_id: Optional[str] = None
    conflict_choice: Optional[Literal["archive_current", "replace_current"]] = None

class ArchiveRestorePreviewRequest(BaseModel):
    folder: str
@router.post("/archive-restore-preview")
def preview_archive_restore(body: ArchiveRestorePreviewRequest, db: Session = Depends(get_db)):
    """Persist and return the exact restore plan before any file is changed."""
    plan = _archive_plan(body.folder, db)
    operation = FileOperation(
        entity_stable_id=plan.get("playarr_video_id") or f"archive:{os.path.basename(body.folder)}",
        operation_type="archive_restore",
        expected_revision=plan.get("expected_video_revision"),
        plan_json=plan,
        rollback_json={"current_path": plan.get("current_path")},
        status="planned",
    )
    db.add(operation)
    db.commit()
    db.refresh(operation)
    return {"operation_id": operation.id, **plan}
def _archive_current_restore_conflict(video: VideoItem, current_path: str, operation_id: str) -> str:
    """Move the newer current file aside so restore remains reversible."""
    from datetime import datetime, timezone
    from app.routers.video_editor import write_archive_manifest

    library_root, archive_root = next(
        (pair for pair in _archive_roots()
         if os.path.normcase(os.path.normpath(current_path)).startswith(
             os.path.normcase(pair[0]) + os.sep)),
        _archive_roots()[0],
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    conflict_folder = os.path.join(archive_root, "_restore_conflicts", video.stable_id, stamp)
    os.makedirs(conflict_folder, exist_ok=False)
    destination = os.path.join(conflict_folder, os.path.basename(current_path))
    import shutil
    shutil.move(current_path, destination)
    from app.services.content_id import compute_ids_for_video
    portable_id = video.playarr_video_id or compute_ids_for_video(video)["playarr_video_id"]
    try:
        write_archive_manifest(
            destination,
            current_path,
            library_root,
            video_id=video.id,
            playarr_video_id=portable_id,
            operation_id=operation_id,
            artist=video.artist or "",
            title=video.title or "",
            archive_reason="restore_conflict",
        )
    except Exception:
        shutil.move(destination, current_path)
        raise
    if not os.path.isfile(os.path.join(conflict_folder, ".playarr-archive.json")):
        import shutil
        shutil.move(destination, current_path)
        raise RuntimeError("Could not journal the current file before restore")
    return destination
def _execute_restore_archive_item(body: RestoreArchiveRequest, db: Session):
    """Restore an archived video back to its library location."""
    from app.config import get_settings as _get_settings
    from app.routers.video_editor import (
        _MANIFEST_NAME, _VIDEO_EXTS, _read_folder_manifest, _manifest_video_path)
    from app.services.media_analyzer import extract_quality_signature
    from app.models import QualitySignature as QualitySigModel, VideoItem
    from app.services.archive_identity import resolve_manifest_video

    _settings = _get_settings()

    folder = body.folder
    norm_folder = os.path.normcase(os.path.normpath(folder))
    allowed = False
    for lib_root in _settings.get_all_library_dirs():
        archive_root = os.path.normcase(os.path.normpath(os.path.join(lib_root, "_archive")))
        if norm_folder.startswith(archive_root + os.sep) or norm_folder == archive_root:
            allowed = True
            break
    if not allowed:
        raise HTTPException(403, "Path is not inside archive directory")
    if not os.path.isdir(folder):
        raise HTTPException(404, "Archive folder not found")

    # Read manifest and select the TRUE original it records.  Falling back to
    # the first listed video file (legacy manifest-less folders) is only safe
    # when there is no manifest — otherwise a re-encode intermediate archived
    # alongside the original (timestamp-suffixed) could be restored instead,
    # and the rmtree below would then delete the real original.
    manifest_path = os.path.join(folder, _MANIFEST_NAME)
    meta: dict = _read_folder_manifest(folder) or {}

    archive_file = _manifest_video_path(folder, meta) if meta else None
    if not archive_file:
        for fn in os.listdir(folder):
            if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS:
                archive_file = os.path.join(folder, fn)
                break
    if not archive_file:
        raise HTTPException(404, "No video file found in archive folder")

    video = resolve_manifest_video(db, meta)
    video_id = video.id if video else meta.get("video_id")

    if video and video.file_path:
        # Kill any active streaming processes holding the file
        from app.routers.playback import kill_streams_for_file
        kill_streams_for_file(video.file_path)

        # Delete the current encoded file
        if os.path.isfile(video.file_path):
            import time
            for attempt in range(5):
                try:
                    os.remove(video.file_path)
                    break
                except PermissionError:
                    if attempt < 4:
                        time.sleep(0.5)
                    else:
                        raise HTTPException(
                            409,
                            "Cannot delete current file — it is currently in use. "
                            "Stop playback and try again."
                        )

        # Determine restored file path
        archive_ext = os.path.splitext(archive_file)[1]
        current_ext = os.path.splitext(video.file_path)[1]
        if archive_ext.lower() != current_ext.lower():
            restored_path = os.path.splitext(video.file_path)[0] + archive_ext
        else:
            restored_path = video.file_path

        import shutil as _shutil
        os.makedirs(os.path.dirname(restored_path), exist_ok=True)
        _shutil.move(archive_file, restored_path)

        # Update DB
        if restored_path != video.file_path:
            video.file_path = restored_path
            video.folder_path = os.path.dirname(restored_path)

        # Re-analyze quality
        new_sig = None
        try:
            new_sig = extract_quality_signature(video.file_path)
            qs = db.query(QualitySigModel).filter(QualitySigModel.video_id == video_id).first()
            if qs:
                for k, val in new_sig.items():
                    setattr(qs, k, val)
        except Exception as e:
            logger.warning(f"Post-restore analysis failed: {e}")
        video.file_size_bytes = os.path.getsize(video.file_path)
        if new_sig and new_sig.get("height"):
            video.resolution_label = f"{new_sig['height']}p"
        video.revision = (video.revision or 1) + 1
        db.commit()
    else:
        # No linked video — just move the file back to library root
        import shutil as _shutil
        lib_dir = _settings.library_dir
        artist = meta.get("artist", "Unknown Artist")
        title = meta.get("title", os.path.splitext(os.path.basename(archive_file))[0])
        dest_folder = os.path.join(lib_dir, artist, f"{artist} - {title}")
        os.makedirs(dest_folder, exist_ok=True)
        _shutil.move(archive_file, os.path.join(dest_folder, os.path.basename(archive_file)))

    # Clean up archive subfolder.  The restored file has been moved out; remove
    # the manifest and any timestamp-suffixed re-encode intermediates of the
    # SAME stem (only the collision handler creates those, always after the
    # canonical original), then remove the folder ONLY if it is now empty.
    # A blind rmtree here would destroy any unrelated original that happened to
    # share the folder — the exact data-loss this restore is meant to prevent.
    if os.path.isfile(manifest_path):
        try:
            os.remove(manifest_path)
        except OSError:
            pass
    import re as _re
    _restored_stem = os.path.splitext(os.path.basename(archive_file))[0]
    _suffixed_pat = _re.compile(
        _re.escape(_restored_stem) + r"_\d{8}_\d{6}$", _re.IGNORECASE)
    if os.path.isdir(folder):
        try:
            for fn in os.listdir(folder):
                stem, fext = os.path.splitext(fn)
                fpath = os.path.join(folder, fn)
                if (os.path.isfile(fpath) and fext.lower() in _VIDEO_EXTS
                        and _suffixed_pat.fullmatch(stem)):
                    try:
                        os.remove(fpath)
                        logger.info(f"Removed archived re-encode intermediate: {fpath}")
                    except OSError:
                        pass
        except OSError:
            pass
        try:
            os.rmdir(folder)  # only succeeds if now empty
        except OSError:
            pass

    return {"message": "Restored from archive", "video_id": video_id}


@router.post("/archive-restore")
def restore_archive_item(body: RestoreArchiveRequest, db: Session = Depends(get_db)):
    """Commit a previously previewed restore plan with an explicit conflict choice."""
    if not body.operation_id:
        raise HTTPException(409, "Restore preview is required before commit")
    operation = db.get(FileOperation, body.operation_id)
    if not operation or operation.operation_type != "archive_restore":
        raise HTTPException(404, "Restore operation not found")
    if operation.status != "planned":
        raise HTTPException(409, f"Restore operation is {operation.status}, not planned")
    plan = operation.plan_json or {}
    if os.path.normcase(os.path.normpath(plan.get("folder", ""))) != os.path.normcase(os.path.normpath(body.folder)):
        raise HTTPException(409, "Restore folder does not match the previewed operation")
    video = db.get(VideoItem, plan.get("video_id")) if plan.get("video_id") else None
    if video and operation.expected_revision is not None and video.revision != operation.expected_revision:
        raise HTTPException(409, "Video changed after restore preview; preview again")
    current_path = plan.get("current_path")
    if current_path and os.path.isfile(current_path) and not body.conflict_choice:
        raise HTTPException(409, {
            "message": "A current file exists; choose archive_current or replace_current",
            "operation_id": operation.id,
            "choices": ["archive_current", "replace_current"],
        })

    operation.status = "running"
    operation.started_at = datetime.now(timezone.utc)
    db.commit()
    conflict_archive = None
    try:
        if current_path and os.path.isfile(current_path) and body.conflict_choice == "archive_current":
            if not video:
                raise HTTPException(409, "Cannot safely archive the current file without a linked video")
            conflict_archive = _archive_current_restore_conflict(video, current_path, operation.id)
            operation.rollback_json = {
                "current_path": current_path,
                "conflict_archive_path": conflict_archive,
            }
            db.commit()
        result = _execute_restore_archive_item(body, db)
        operation = db.get(FileOperation, body.operation_id)
        operation.status = "succeeded"
        operation.current_step = 1
        operation.completed_at = datetime.now(timezone.utc)
        db.commit()
        return {**result, "operation_id": operation.id}
    except Exception as exc:
        db.rollback()
        # If the original restore never reached the current path, put the
        # conflict copy back so a failed commit does not leave the library empty.
        if conflict_archive and current_path and os.path.isfile(conflict_archive) and not os.path.isfile(current_path):
            import shutil
            os.makedirs(os.path.dirname(current_path), exist_ok=True)
            shutil.move(conflict_archive, current_path)
        operation = db.get(FileOperation, body.operation_id)
        if operation:
            operation.status = "failed"
            operation.error_json = {
                "code": "archive_restore_failed",
                "message": str(exc),
                "retryable": True,
            }
            operation.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise


@router.post("/archive-integrity")
def archive_integrity_report(db: Session = Depends(get_db)):
    """Verify manifests/checksums and report orphans without deleting anything."""
    from app.routers.video_editor import _MANIFEST_NAME, _file_checksum, _manifest_video_path, _read_folder_manifest
    from app.services.archive_identity import manifest_checksum, manifest_video_stable_id, resolve_manifest_video
    records = []
    for _library_root, archive_root in _archive_roots():
        if not os.path.isdir(archive_root):
            continue
        for root, _dirs, names in os.walk(archive_root):
            if _MANIFEST_NAME not in names:
                continue
            manifest = _read_folder_manifest(root)
            problems = []
            if not manifest:
                problems.append("invalid_manifest")
                records.append({"folder": root, "status": "invalid", "problems": problems})
                continue
            archive_file = _manifest_video_path(root, manifest)
            if not archive_file:
                problems.append("missing_archive_file")
            checksum_algorithm, expected = manifest_checksum(manifest)
            if archive_file and expected and _file_checksum(archive_file, checksum_algorithm) != expected:
                problems.append("checksum_mismatch")
            video = resolve_manifest_video(db, manifest)
            stable_id = manifest_video_stable_id(manifest)
            if video and stable_id and video.playarr_video_id != stable_id:
                problems.append("stable_identity_mismatch")
            if not video:
                problems.append("orphaned_owner")
            if manifest.get("schema_version") != 2:
                problems.append("legacy_manifest")
            records.append({
                "folder": root,
                "video_id": manifest.get("video_id"),
                "playarr_video_id": stable_id,
                "operation_id": manifest.get("operation_id"),
                "status": "ok" if not problems else "attention",
                "problems": problems,
            })
    return {
        "checked": len(records),
        "ok": sum(1 for row in records if row["status"] == "ok"),
        "attention": sum(1 for row in records if row["status"] != "ok"),
        "items": records,
        "deleted": 0,
    }



@router.post("/startup")
def configure_startup(db: Session = Depends(get_db)):
    """Add or remove Playarr from Windows startup based on current settings."""
    if sys.platform != "win32":
        raise HTTPException(status_code=400, detail="Startup management is only supported on Windows")

    import winreg
    enabled = _get_setting_value(db, "startup_with_system") == "true"
    delay = int(_get_setting_value(db, "startup_delay_seconds") or "0")

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    try:
        if enabled:
            cmd = _startup_command(delay)
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, "Playarr", 0, winreg.REG_SZ, cmd)
            logger.info("Registered Playarr in Windows startup (delay=%ss): %s", delay, cmd)
        else:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE,
                ) as key:
                    winreg.DeleteValue(key, "Playarr")
                logger.info("Removed Playarr from Windows startup")
            except FileNotFoundError:
                pass  # Value already absent — nothing to remove
    except OSError as exc:
        logger.exception("Failed to configure Windows startup")
        raise HTTPException(status_code=500, detail=f"Could not update Windows startup entry: {exc}")

    return {"status": "ok", "startup_enabled": enabled, "delay": delay}


def _startup_command(delay: int) -> str:
    """Build the command line written to the HKCU Run key.

    In an installed (PyInstaller-frozen) build ``sys.executable`` is Playarr.exe,
    which is the real launcher — invoke it directly.  When running from source we
    invoke ``run_playarr.py`` (the same production launcher) with pythonw.exe so
    no console window appears.  Either way the target is the supervised launcher
    in ``run_playarr.py`` — never the dev-only ``_start_server.py``, which is not
    shipped in the installer.
    """
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:
        python_exe = sys.executable
        pythonw = python_exe.replace("python.exe", "pythonw.exe")
        if os.path.exists(pythonw):
            python_exe = pythonw
        # settings.py -> app -> backend -> repo root (where run_playarr.py lives)
        launcher = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "run_playarr.py")
        )
        cmd = f'"{python_exe}" "{launcher}"'
    if delay > 0:
        cmd += f" --delay {delay}"
    return cmd


# ---------------------------------------------------------------------------
# Genre Blacklist Management
# ---------------------------------------------------------------------------

class GenreBlacklistItem(BaseModel):
    id: int
    name: str
    blacklisted: bool
    video_count: int
    master_genre_id: Optional[int] = None
    alias_count: int = 0


class GenreBlacklistUpdate(BaseModel):
    genre_ids: List[int]
    blacklisted: bool


@router.get("/genre-blacklist", response_model=List[GenreBlacklistItem])
def list_genre_blacklist(include_unused: bool = False, db: Session = Depends(get_db)):
    """List resolved mask genres; aliases and unused rows are maintenance data."""
    from app.models import Genre, video_genres
    from sqlalchemy import func

    results = (
        db.query(
            Genre.id,
            Genre.name,
            Genre.blacklisted,
            func.count(video_genres.c.video_id),
            Genre.master_genre_id,
        )
        .outerjoin(video_genres, Genre.id == video_genres.c.genre_id)
        .group_by(Genre.id, Genre.name, Genre.blacklisted, Genre.master_genre_id)
        .order_by(Genre.name)
        .all()
    )

    by_id = {r[0]: r for r in results}
    aggregate_counts: dict[int, int] = {}
    alias_counts: dict[int, int] = {}
    for r in results:
        mid = r[4] or r[0]
        aggregate_counts[mid] = aggregate_counts.get(mid, 0) + int(r[3] or 0)
        if mid is not None:
            if r[4] is not None:
                alias_counts[mid] = alias_counts.get(mid, 0) + 1

    output = []
    for genre_id, count in aggregate_counts.items():
        master = by_id.get(genre_id)
        if master is None or master[4] is not None:
            continue
        if count == 0 and not include_unused:
            continue
        output.append(GenreBlacklistItem(
            id=master[0], name=master[1], blacklisted=bool(master[2]), video_count=count,
            master_genre_id=None, alias_count=alias_counts.get(master[0], 0),
        ))
    return sorted(output, key=lambda item: item.name.casefold())


@router.put("/genre-blacklist")
def update_genre_blacklist(body: GenreBlacklistUpdate, db: Session = Depends(get_db)):
    """Bulk update blacklist status for genres."""
    from app.models import Genre

    updated = (
        db.query(Genre)
        .filter(Genre.id.in_(body.genre_ids))
        .update({Genre.blacklisted: body.blacklisted}, synchronize_session="fetch")
    )
    db.commit()
    return {"updated": updated}


class GenreCreateRequest(BaseModel):
    name: str


@router.post("/genre-blacklist", response_model=GenreBlacklistItem)
def create_genre(body: GenreCreateRequest, db: Session = Depends(get_db)):
    """Create a new genre (visible by default)."""
    from app.models import Genre

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Genre name cannot be empty")
    existing = db.query(Genre).filter(Genre.name == name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Genre already exists")
    genre = Genre(name=name, blacklisted=False)
    db.add(genre)
    db.commit()
    db.refresh(genre)
    return GenreBlacklistItem(id=genre.id, name=genre.name, blacklisted=False, video_count=0, master_genre_id=None, alias_count=0)
