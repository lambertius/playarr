# AUTO-SEPARATED from services/downloader.py for pipeline_url pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Downloader Service — yt-dlp based video download with format selection.
"""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import tempfile
from typing import Callable, Dict, Any, Optional, Tuple, List

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── yt-dlp output parsing helpers ─────────────────────────────────────────

_SPEED_RE = re.compile(
    r"([\d.]+)\s*(KiB|MiB|GiB|B)/s"
)
_ETA_RE = re.compile(
    r"ETA\s+(\d+):(\d+)(?::(\d+))?"
)
_BYTES_RE = re.compile(
    r"of\s+~?([\d.]+)(KiB|MiB|GiB|B)"
)
_FRAG_RE = re.compile(
    r"\(frag\s+(\d+)/(\d+)\)"
)
_DOWNLOADED_RE = re.compile(
    r"([\d.]+)(KiB|MiB|GiB|B)\s+of"
)

_UNIT_MULTIPLIER = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024 ** 2,
    "GiB": 1024 ** 3,
}


def parse_ytdlp_progress(line: str) -> Dict[str, Any]:
    """
    Parse a yt-dlp progress line into structured metrics.

    Returns dict with optional keys:
        percent, speed_bytes, eta_seconds, downloaded_bytes,
        total_bytes, fragments_done, fragments_total
    """
    result: Dict[str, Any] = {}

    # Percent
    if "%" in line:
        try:
            pct_str = line.split("%")[0].split()[-1]
            result["percent"] = float(pct_str)
        except (ValueError, IndexError):
            pass

    # Speed
    m = _SPEED_RE.search(line)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        result["speed_bytes"] = val * _UNIT_MULTIPLIER.get(unit, 1)

    # ETA
    m = _ETA_RE.search(line)
    if m:
        hours = int(m.group(3)) if m.group(3) else 0
        mins = int(m.group(1))
        secs = int(m.group(2))
        if m.group(3):
            hours = int(m.group(1))
            mins = int(m.group(2))
            secs = int(m.group(3))
        result["eta_seconds"] = hours * 3600 + mins * 60 + secs

    # Total bytes (from "of ~XXX MiB" or "of XXX MiB")
    m = _BYTES_RE.search(line)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        result["total_bytes"] = int(val * _UNIT_MULTIPLIER.get(unit, 1))

    # Downloaded bytes
    m = _DOWNLOADED_RE.search(line)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        result["downloaded_bytes"] = int(val * _UNIT_MULTIPLIER.get(unit, 1))

    # Fragment progress
    m = _FRAG_RE.search(line)
    if m:
        result["fragments_done"] = int(m.group(1))
        result["fragments_total"] = int(m.group(2))

    return result

# ── Subprocess isolation ──────────────────────────────────────────────────
# On Windows, child processes inherit the parent's console by default.
# When multiple yt-dlp processes share a console, CTRL_C_EVENT propagates
# to ALL of them — causing "ERROR: Interrupted by user" failures.
# Fix: redirect stdin to DEVNULL and isolate each process into its own
# process group so console signals don't cascade.

def _subprocess_kwargs() -> dict:
    """Return platform-specific kwargs for subprocess calls.

    Ensures yt-dlp child processes:
    - Don't inherit parent stdin (prevents shared-console interrupt issues)
    - Run in their own process group on Windows (blocks CTRL_C propagation)
    """
    kwargs: dict = {"stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP (0x200) isolates the child from the parent's
        # console control-handler chain so Ctrl+C in the server terminal won't
        # kill running downloads.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    return kwargs


# ── Download concurrency limiter ──────────────────────────────────────────
# When running without Celery (thread-based dispatch), playlist imports
# fire all child downloads simultaneously.  Concerns addressed:
#  • Windows console CTRL_C_EVENT: mitigated by CREATE_NEW_PROCESS_GROUP.
#  • YouTube rate-limiting: keep concurrent downloads low to avoid
#    per-IP throttling; configurable via Settings → Import.
#  • SQLite write contention: handled by _pipeline_lock (only DB-write
#    phases are serialised, not downloads).
_download_lock = threading.Lock()
_download_semaphore: threading.Semaphore | None = None
_download_semaphore_size: int = 0


def _get_download_semaphore() -> threading.Semaphore:
    """Return a semaphore sized to the current DB setting.

    If the user changes the setting, the semaphore is rebuilt so subsequent
    acquires respect the new limit.  In-flight downloads keep their existing
    permits.
    """
    global _download_semaphore, _download_semaphore_size
    try:
        from app.database import SessionLocal
        from app.models import AppSetting
        db = SessionLocal()
        try:
            row = db.query(AppSetting).filter(
                AppSetting.key == "max_concurrent_downloads",
                AppSetting.user_id.is_(None),
            ).first()
            desired = int(row.value) if row else 4
        finally:
            db.close()
    except Exception:
        desired = 4
    desired = max(1, min(desired, 16))  # clamp 1–16

    with _download_lock:
        if _download_semaphore is None or _download_semaphore_size != desired:
            _download_semaphore = threading.Semaphore(desired)
            _download_semaphore_size = desired
            logger.info(f"Download semaphore (re)created with max={desired}")
        return _download_semaphore


def extract_playlist_entries(url: str) -> List[Dict[str, str]]:
    """
    Use yt-dlp --flat-playlist to extract individual video URLs from a playlist.

    Returns a list of dicts with keys: url, title, id

    Retries automatically (up to 3 times) when the result count lands exactly
    on a YouTube pagination boundary (100, 200, …) — a common sign that YouTube
    returned a truncated response.  Keeps retrying while the count is still
    growing and still a multiple of 100.
    """
    settings = get_settings()
    ytdlp = settings.resolved_ytdlp
    ffmpeg_dir = os.path.dirname(settings.resolved_ffmpeg)

    def _run_extraction() -> List[Dict[str, str]]:
        cmd = [
            ytdlp,
            "--ffmpeg-location", ffmpeg_dir,
            "--flat-playlist",
            "--dump-json",
            "--no-warnings",
            url,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                                **_subprocess_kwargs())
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp playlist extraction failed: {result.stderr}")

        entries = []
        seen_ids: set = set()
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                info = json.loads(line)
                video_id = info.get("id", "")
                if video_id in seen_ids:
                    continue
                seen_ids.add(video_id)
                video_url = info.get("url") or info.get("webpage_url") or ""
                if not video_url and video_id:
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                title = info.get("title", "")
                entries.append({"url": video_url, "title": title, "id": video_id})
            except json.JSONDecodeError:
                continue
        return entries

    logger.info(f"Extracting playlist entries for: {url}")
    entries = _run_extraction()
    count = len(entries)

    # YouTube sometimes returns truncated results that land exactly on a
    # pagination boundary (100, 200, …).  Retry while the count keeps growing
    # and remains on a boundary, up to a hard cap of 3 retries.
    max_retries = 3
    attempt = 0
    while count > 0 and count % 100 == 0 and attempt < max_retries:
        attempt += 1
        logger.warning(
            f"Playlist returned exactly {count} entries (possible YouTube "
            f"pagination truncation) — retry {attempt}/{max_retries}"
        )
        retry_entries = _run_extraction()
        retry_count = len(retry_entries)
        if retry_count > count:
            logger.info(
                f"Retry returned {retry_count} entries "
                f"(up from {count}), using retry result"
            )
            entries = retry_entries
            count = retry_count
            # Loop continues if new count is still a multiple of 100
        else:
            logger.info(
                f"Retry returned {retry_count} entries (no increase) — "
                f"accepting {count} as the true playlist size"
            )
            break

    logger.info(f"Found {len(entries)} playlist entries")
    return entries


def get_available_formats(url: str) -> List[Dict[str, Any]]:
    """
    Query yt-dlp for available formats without downloading.
    Returns list of format dicts.
    """
    settings = get_settings()
    ytdlp = settings.resolved_ytdlp

    # Point yt-dlp at our portable ffmpeg so it can probe all formats
    ffmpeg_dir = os.path.dirname(settings.resolved_ffmpeg)
    cmd = [ytdlp, "--ffmpeg-location", ffmpeg_dir, "--js-runtimes", "node", "--dump-json", "--no-download", "--no-playlist", url]
    logger.info(f"Fetching formats for: {url}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                            **_subprocess_kwargs())
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp format query failed: {result.stderr}")

    info = json.loads(result.stdout)
    return info.get("formats", []), info


def extract_metadata_from_ytdlp(info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract useful metadata from yt-dlp info dict.
    """
    return {
        "title": info.get("title", ""),
        "uploader": info.get("uploader", ""),
        "channel": info.get("channel", ""),
        "artist": info.get("artist") or info.get("creator") or "",
        "track": info.get("track") or "",
        "album": info.get("album"),
        "release_year": info.get("release_year") or _extract_year(info.get("upload_date")),
        "description": info.get("description", ""),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "thumbnails": info.get("thumbnails", []),
        "categories": info.get("categories", []),
        "tags": info.get("tags", []),
        "upload_date": info.get("upload_date"),
        "webpage_url": info.get("webpage_url", ""),
    }


def _extract_year(upload_date: Optional[str]) -> Optional[int]:
    """Extract year from yt-dlp upload_date (YYYYMMDD format)."""
    if upload_date and len(upload_date) >= 4:
        try:
            return int(upload_date[:4])
        except ValueError:
            return None
    return None


def select_best_format(formats: List[Dict[str, Any]], container: str = "mkv",
                       max_height: int | None = None) -> str:
    """
    Select the best format string for yt-dlp.

    Container-aware strategy:
    - MKV: accepts all codecs — grab absolute best audio & video
    - MP4: only supports AAC/MP3/AC3 audio; prefer m4a to avoid transcode
    - WebM: only supports VP8/VP9 video + Opus/Vorbis audio
    - Other: unrestricted, same as MKV

    When max_height is set (e.g. 1080), video streams are capped at that height.
    Browser-incompatible audio is transcoded on-the-fly at playback.
    """
    container = (container or "mkv").lower()
    h = f"[height<={max_height}]" if max_height else ""
    if container == "mp4":
        return f"bestvideo{h}[ext=mp4]+bestaudio[ext=m4a]/bestvideo{h}+bestaudio/best"
    if container == "webm":
        return f"bestvideo{h}[ext=webm]+bestaudio[ext=webm]/bestvideo{h}+bestaudio/best"
    # MKV, AVI, MOV — no restrictions, best quality
    return f"bestvideo{h}+bestaudio/best"


def download_video(
    url: str,
    output_dir: str,
    output_template: str = "%(title)s.%(ext)s",
    format_spec: Optional[str] = None,
    progress_callback=None,
    cancel_check=None,
    container: str = "mkv",
    max_height: int | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Download a video using yt-dlp.

    Args:
        url: Video URL
        output_dir: Directory to save the file
        output_template: yt-dlp output template
        format_spec: yt-dlp format string (None = auto best)
        progress_callback: Optional callable(percent, status_msg)
        cancel_check: Optional callable() that raises if the job was cancelled
        container: Merge output container (mkv, mp4, webm, avi, mov)
        max_height: Cap video height (e.g. 1080). None = maximum available.

    Returns:
        (downloaded_file_path, metadata_dict)
    """
    settings = get_settings()
    ytdlp = settings.resolved_ytdlp
    container = (container or "mkv").lower()

    if format_spec is None:
        format_spec = select_best_format([], container=container, max_height=max_height)

    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, output_template)

    # Point yt-dlp at our portable ffmpeg so it can merge video+audio
    ffmpeg_dir = os.path.dirname(settings.resolved_ffmpeg)
    cmd = [
        ytdlp,
        "--ffmpeg-location", ffmpeg_dir,
        "--js-runtimes", "node",
        "-f", format_spec,
        "--merge-output-format", container,
        "--write-info-json",
        "--no-playlist",                   # Never download full playlist; handled at API level
        "--no-write-playlist-metafiles",
        "--no-overwrites",
        "-o", output_path,
        "--newline",  # For progress parsing
        url,
    ]

    logger.info(f"Downloading: {url}")
    logger.debug(f"Command: {' '.join(cmd)}")

    logger.info(f"Waiting for download semaphore ...")
    sem = _get_download_semaphore()
    sem.acquire()
    logger.info(f"Semaphore acquired — starting download for: {url}")
    try:
        return _run_download(cmd, output_dir, url, progress_callback,
                             cancel_check=cancel_check, output_lines=[])
    finally:
        sem.release()
        logger.info(f"Semaphore released after download: {url}")


def _run_download(
    cmd: list,
    output_dir: str,
    url: str,
    progress_callback,
    cancel_check=None,
    output_lines: list | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """Execute the yt-dlp download subprocess (called under semaphore).

    Includes a stall watchdog: if yt-dlp produces no stdout for
    STALL_TIMEOUT_SECONDS, the subprocess is killed and a RuntimeError
    is raised so the pipeline can retry or fail cleanly.
    """
    STALL_TIMEOUT_SECONDS = 300  # 5 min with no output → kill

    if output_lines is None:
        output_lines = []

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **_subprocess_kwargs(),
    )

    downloaded_file = None
    info_dict = {}

    # Watchdog: tracks last output time; a daemon thread kills the process
    # if it stalls for too long.
    import time as _time
    _last_output = [_time.monotonic()]
    _watchdog_stop = threading.Event()

    def _watchdog():
        while not _watchdog_stop.is_set():
            if _time.monotonic() - _last_output[0] > STALL_TIMEOUT_SECONDS:
                logger.error(
                    f"yt-dlp stall detected (no output for {STALL_TIMEOUT_SECONDS}s) "
                    f"— killing subprocess for: {url}"
                )
                try:
                    process.kill()
                except Exception:
                    pass
                return
            _watchdog_stop.wait(10)  # check every 10s

    _wd_thread = threading.Thread(target=_watchdog, daemon=True)
    _wd_thread.start()

    try:
        for line in process.stdout:
            line = line.strip()
            _last_output[0] = _time.monotonic()
            output_lines.append(line)
            logger.debug(f"yt-dlp: {line}")

            # Check for cancellation while download is in progress
            if cancel_check is not None:
                try:
                    cancel_check()
                except Exception:
                    # Kill the subprocess and propagate
                    process.kill()
                    process.wait()
                    raise

        # Parse progress — extract rich metrics from yt-dlp output
        if "[download]" in line and "%" in line:
            metrics = parse_ytdlp_progress(line)
            pct = metrics.get("percent", 0)
            if progress_callback:
                progress_callback(int(pct), line, metrics)

        # Detect output file from various yt-dlp post-processor tags
        if ('"' in line) and any(tag in line for tag in (
            "[Merger] Merging formats into",
            "[ExtractAudio] Destination:",
            "[Fixup", "Destination:",
        )):
            parts = line.split('"')
            if len(parts) >= 2 and os.path.splitext(parts[1])[1]:
                downloaded_file = parts[1]

        if "has already been downloaded" in line:
            # Try to extract filename
            parts = line.split('"') if '"' in line else line.split("'")
            if len(parts) >= 2:
                downloaded_file = parts[1]

    finally:
        _watchdog_stop.set()

    process.wait()

    # Detect watchdog kill (returncode is negative on SIGKILL / forced termination)
    if process.returncode != 0 and _time.monotonic() - _last_output[0] > STALL_TIMEOUT_SECONDS - 15:
        raise RuntimeError(
            f"yt-dlp stalled (no output for >{STALL_TIMEOUT_SECONDS}s) and was killed"
        )

    if process.returncode != 0:
        raise RuntimeError(f"yt-dlp download failed:\n{''.join(output_lines[-20:])}")

    # Primary method: scan the output directory for the actual video file.
    # This is more reliable than parsing yt-dlp output which may contain
    # sanitized/unicode-modified filenames.
    scanned = _find_downloaded_file(output_dir, output_lines)
    if scanned and os.path.isfile(scanned):
        downloaded_file = scanned

    # Fallback: If we caught the filename from merger/extractor output, try that
    if not downloaded_file or not os.path.isfile(downloaded_file):
        if downloaded_file:
            # Try relative to output_dir
            candidate = os.path.join(output_dir, os.path.basename(downloaded_file))
            if os.path.isfile(candidate):
                downloaded_file = candidate

    if not downloaded_file or not os.path.isfile(downloaded_file):
        raise RuntimeError(
            f"Download appeared to succeed but output file not found.\n"
            f"Checked: {downloaded_file}\n"
            f"Directory contents: {os.listdir(output_dir)}"
        )

    # Load info json if it exists
    info_json_path = os.path.splitext(downloaded_file)[0] + ".info.json"
    if not os.path.isfile(info_json_path):
        # yt-dlp may write info.json with template name rather than final merged name
        # Scan the output dir for any .info.json file
        for f in os.listdir(output_dir):
            if f.endswith(".info.json"):
                info_json_path = os.path.join(output_dir, f)
                break
    if os.path.isfile(info_json_path):
        with open(info_json_path, "r", encoding="utf-8") as f:
            info_dict = json.load(f)
        # Clean up info json
        os.remove(info_json_path)

    logger.info(f"Downloaded: {downloaded_file}")
    return downloaded_file, info_dict


def _find_downloaded_file(output_dir: str, log_lines: list) -> Optional[str]:
    """Try to find the downloaded file by scanning the output directory."""
    video_exts = {".mkv", ".mp4", ".webm", ".avi", ".mov"}
    files = []
    for f in os.listdir(output_dir):
        ext = os.path.splitext(f)[1].lower()
        if ext in video_exts:
            full = os.path.join(output_dir, f)
            files.append((os.path.getmtime(full), full))

    if files:
        files.sort(reverse=True)
        return files[0][1]

    return None


def get_best_thumbnail_url(info: Dict[str, Any]) -> Optional[str]:
    """
    Get the highest quality thumbnail URL from yt-dlp metadata.
    """
    thumbnails = info.get("thumbnails", [])
    if not thumbnails:
        return info.get("thumbnail")

    # Sort by preference/resolution
    best = None
    best_size = 0
    for thumb in thumbnails:
        w = thumb.get("width", 0) or 0
        h = thumb.get("height", 0) or 0
        size = w * h
        if size >= best_size:
            best_size = size
            best = thumb.get("url")

    return best or info.get("thumbnail")
