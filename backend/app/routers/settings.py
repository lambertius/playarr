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
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppSetting, NormalizationHistory
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
    result = [SettingOut(key=s.key, value=s.value, value_type=s.value_type) for s in settings]

    for key, (default_val, val_type) in DEFAULT_SETTINGS.items():
        if key not in existing_keys:
            result.append(SettingOut(key=key, value=default_val, value_type=val_type))

    return result


@router.put("/", response_model=SettingOut)
def update_setting(update: SettingUpdate, user_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Update or create a setting."""
    setting = db.query(AppSetting).filter(
        AppSetting.key == update.key,
        AppSetting.user_id == user_id,
    ).first()

    if setting:
        setting.value = update.value
        setting.value_type = update.value_type
    else:
        setting = AppSetting(
            user_id=user_id,
            key=update.key,
            value=update.value,
            value_type=update.value_type,
        )
        db.add(setting)

    db.commit()
    db.refresh(setting)

    # Sync directory settings to the pydantic config singleton
    _sync_dir_setting_to_config(update.key, update.value)

    # Ensure critical subdirectories when library_dir changes
    if update.key == "library_dir":
        from app.config import ensure_library_subdirs
        ensure_library_subdirs(update.value)

    return SettingOut(key=setting.key, value=setting.value, value_type=setting.value_type)


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
                    qs = QualitySignature(video_id=video_item.id)
                    for k, v in sig.items():
                        if hasattr(qs, k):
                            setattr(qs, k, v)
                    db.add(qs)
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


@router.get("/archive-items", response_model=List[ArchiveItemOut])
def list_archive_items():
    """List all items in the archive directory with manifest metadata."""
    from app.config import get_settings as _get_settings
    from app.routers.video_editor import _MANIFEST_NAME, _VIDEO_EXTS
    _settings = _get_settings()

    results: list[dict] = []
    for lib_root in _settings.get_all_library_dirs():
        archive_dir = os.path.join(lib_root, "_archive")
        if not os.path.isdir(archive_dir):
            continue
        for root, _dirs, fnames in os.walk(archive_dir):
            video_file = None
            for fn in fnames:
                if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS:
                    video_file = os.path.join(root, fn)
                    break
            if not video_file:
                continue
            # Read manifest if present
            manifest_path = os.path.join(root, _MANIFEST_NAME)
            meta: dict = {}
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass
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
                import shutil as _shutil
                _shutil.rmtree(folder)
                deleted += 1
            except OSError as e:
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
            if os.path.isdir(entry_path):
                try:
                    import shutil as _shutil
                    _shutil.rmtree(entry_path)
                    deleted += 1
                except OSError as e:
                    errors.append(f"{entry_path}: {e}")
            elif os.path.isfile(entry_path):
                try:
                    os.remove(entry_path)
                    deleted += 1
                except OSError as e:
                    errors.append(f"{entry_path}: {e}")
    return {"deleted": deleted, "errors": errors}


@router.post("/startup")
def configure_startup(db: Session = Depends(get_db)):
    """Add or remove Playarr from Windows startup based on current settings."""
    if sys.platform != "win32":
        raise HTTPException(status_code=400, detail="Startup management is only supported on Windows")

    import winreg
    enabled = _get_setting_value(db, "startup_with_system") == "true"
    delay = int(_get_setting_value(db, "startup_delay_seconds") or "0")

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    if enabled:
        # Use pythonw.exe (no console window) if available, else python.exe
        python_exe = sys.executable
        pythonw = python_exe.replace("python.exe", "pythonw.exe")
        if os.path.exists(pythonw):
            python_exe = pythonw

        script = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "_start_server.py")
        )
        cmd = f'"{python_exe}" "{script}"'
        if delay > 0:
            cmd += f" --delay {delay}"

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "Playarr", 0, winreg.REG_SZ, cmd)

        logger.info(f"Registered Playarr in Windows startup (delay={delay}s)")
    else:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, "Playarr")
            logger.info("Removed Playarr from Windows startup")
        except OSError:
            pass  # Already absent

    return {"status": "ok", "startup_enabled": enabled, "delay": delay}


# ---------------------------------------------------------------------------
# Genre Blacklist Management
# ---------------------------------------------------------------------------

class GenreBlacklistItem(BaseModel):
    id: int
    name: str
    blacklisted: bool
    video_count: int


class GenreBlacklistUpdate(BaseModel):
    genre_ids: List[int]
    blacklisted: bool


@router.get("/genre-blacklist", response_model=List[GenreBlacklistItem])
def list_genre_blacklist(db: Session = Depends(get_db)):
    """List all genres with their blacklist status and video count."""
    from app.models import Genre, video_genres
    from sqlalchemy import func

    results = (
        db.query(
            Genre.id,
            Genre.name,
            Genre.blacklisted,
            func.count(video_genres.c.video_id),
        )
        .outerjoin(video_genres, Genre.id == video_genres.c.genre_id)
        .group_by(Genre.id, Genre.name, Genre.blacklisted)
        .order_by(Genre.name)
        .all()
    )
    return [
        GenreBlacklistItem(id=r[0], name=r[1], blacklisted=bool(r[2]), video_count=r[3])
        for r in results
    ]


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
    return GenreBlacklistItem(id=genre.id, name=genre.name, blacklisted=False, video_count=0)
