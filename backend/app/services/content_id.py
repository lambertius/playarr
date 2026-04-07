"""
Playarr Content ID Generator — deterministic identity hashes.

Generates two IDs for each video:

playarr_track_id  (PTR-xxxxxxxxxxxx)
    Same musical composition/performance regardless of video.
    Covers and live versions → different track ID.
    Alternate/uncensored/18+ versions → SAME track ID as normal.
    Based on: mb_recording_id (preferred) or normalized artist+title.

playarr_video_id  (PVD-xxxxxxxxxxxx)
    Same visual content regardless of quality/resolution/crop.
    Alternate versions → different video ID.
    Same-quality re-encodes → same video ID.
    Based on: track_id + version_type + video_phash (when available).

Both IDs are 16 chars: 3-char prefix + hyphen + 12-char hex digest.
"""
import hashlib
import logging
import re
import subprocess
import tempfile
from io import BytesIO
from typing import Optional, TYPE_CHECKING

from PIL import Image

from app.subprocess_utils import HIDE_WINDOW
from app.config import get_settings

if TYPE_CHECKING:
    from app.models import VideoItem

logger = logging.getLogger(__name__)

# ── Version types that share a track ID with "normal" ─────────────
# These are the *same song* just with visual/content edits
_SAME_TRACK_VERSIONS = {"normal", "alternate", "uncensored", "18+", "explicit", "clean", "censored"}

# ── Normalization ─────────────────────────────────────────────────
_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^\w\s]")
_MULTI_SPACE_RE = re.compile(r"\s+")
# Unicode hyphens, dashes, quotes → ascii equivalents
_UNICODE_NORMALIZE = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2032": "'", "\u2033": '"',
})


def _normalize(s: str) -> str:
    """Normalize a string for hashing: lowercase, strip articles, collapse whitespace."""
    s = s.strip().lower().translate(_UNICODE_NORMALIZE)
    s = _ARTICLE_RE.sub("", s)
    s = _NON_ALNUM_RE.sub("", s)
    return _MULTI_SPACE_RE.sub(" ", s).strip()


def _short_hash(data: str, prefix: str) -> str:
    """Generate a 16-char ID: prefix + '-' + 12 hex chars from SHA-256."""
    digest = hashlib.sha256(data.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


# ── Track ID ──────────────────────────────────────────────────────

def compute_track_id(
    artist: str,
    title: str,
    version_type: str = "normal",
    mb_recording_id: Optional[str] = None,
    original_artist: Optional[str] = None,
    original_title: Optional[str] = None,
) -> str:
    """Compute a deterministic playarr_track_id.

    Rules:
    - If mb_recording_id exists → hash it directly (most stable).
    - If version_type is cover → use original_artist/original_title (maps
      back to the original song's track ID).
    - If version_type is live → separate ID (different performance).
    - alternate/uncensored/18+/explicit/clean → same as normal.
    - Fallback → hash normalized artist + title.
    """
    vt = (version_type or "normal").lower().strip()

    # Covers: derive track ID from the ORIGINAL song, not the cover artist
    if vt == "cover" and original_artist and original_title:
        seed = f"track:{_normalize(original_artist)}:{_normalize(original_title)}"
        return _short_hash(seed, "PTR")

    # Live versions: include "live" in the seed to make them distinct
    if vt == "live":
        if mb_recording_id:
            seed = f"track:mb:{mb_recording_id}:live"
        else:
            seed = f"track:{_normalize(artist)}:{_normalize(title)}:live"
        return _short_hash(seed, "PTR")

    # Normal / alternate / uncensored / 18+ / explicit / clean:
    # All map to the SAME track ID (same musical composition)
    if mb_recording_id:
        seed = f"track:mb:{mb_recording_id}"
    else:
        seed = f"track:{_normalize(artist)}:{_normalize(title)}"

    return _short_hash(seed, "PTR")


# ── Video ID ──────────────────────────────────────────────────────

def compute_video_id(
    artist: str,
    title: str,
    version_type: str = "normal",
    mb_recording_id: Optional[str] = None,
    video_phash: Optional[str] = None,
) -> str:
    """Compute a deterministic playarr_video_id.

    Rules:
    - Start from the track identity (mb_recording_id or artist+title).
    - Add version_type to distinguish alternate/uncensored from normal.
    - Add video_phash to distinguish visually different videos of the
      same song (if available).
    - Quality/resolution/crops do NOT affect the ID.
    """
    vt = (version_type or "normal").lower().strip()

    if mb_recording_id:
        base = f"video:mb:{mb_recording_id}"
    else:
        base = f"video:{_normalize(artist)}:{_normalize(title)}"

    # Version type distinguishes alternate cuts
    base += f":{vt}"

    # pHash distinguishes visually different videos for same song/version
    if video_phash:
        base += f":{video_phash}"

    return _short_hash(base, "PVD")


# ── Perceptual Hash (pHash) ──────────────────────────────────────

def compute_phash(file_path: str) -> Optional[str]:
    """Extract a representative frame and compute its perceptual hash.

    Uses ffmpeg to grab a frame at 30% of the video, then computes a
    64-bit DCT-based perceptual hash using Pillow.

    Returns a 16-char hex string, or None on failure.
    """
    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg

    try:
        # Get duration first
        ffprobe = settings.resolved_ffprobe
        dur_cmd = [
            ffprobe, "-v", "quiet", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30, **HIDE_WINDOW)
        duration = float(dur_result.stdout.strip()) if dur_result.stdout.strip() else 0

        # Seek to 30% of the video for a representative frame
        seek_time = max(1, duration * 0.3) if duration > 0 else 5

        with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as tmp:
            tmp_path = tmp.name

        cmd = [
            ffmpeg, "-ss", str(seek_time), "-i", file_path,
            "-vframes", "1", "-f", "image2", "-y", tmp_path,
        ]
        subprocess.run(cmd, capture_output=True, timeout=30, **HIDE_WINDOW)

        img = Image.open(tmp_path).convert("L")  # grayscale
        phash = _dct_phash(img)

        # Cleanup
        import os
        os.unlink(tmp_path)

        return phash

    except Exception as e:
        logger.warning(f"pHash extraction failed for {file_path}: {e}")
        return None


def _dct_phash(img: Image.Image, hash_size: int = 8) -> str:
    """Compute a DCT-based perceptual hash (64-bit).

    1. Resize to (hash_size*4) x (hash_size*4)
    2. Compute DCT via simple row/column averages (no numpy needed)
    3. Extract top-left hash_size x hash_size block
    4. Threshold against median → 64-bit hash

    Returns a 16-char hex string.
    """
    # Resize to small square
    size = hash_size * 4  # 32x32
    img = img.resize((size, size), Image.LANCZOS)
    pixels = list(img.getdata())

    # Build 2D array
    matrix = []
    for y in range(size):
        row = [float(pixels[y * size + x]) for x in range(size)]
        matrix.append(row)

    # Simple DCT approximation: compute mean of each hash_size block
    # This is a simplified approach that works well for perceptual hashing
    block_h = size // hash_size
    block_w = size // hash_size
    dct_low = []
    for by in range(hash_size):
        for bx in range(hash_size):
            total = 0.0
            count = 0
            for dy in range(block_h):
                for dx in range(block_w):
                    total += matrix[by * block_h + dy][bx * block_w + dx]
                    count += 1
            dct_low.append(total / count if count else 0)

    # Threshold against median
    sorted_vals = sorted(dct_low)
    median = sorted_vals[len(sorted_vals) // 2]

    # Build 64-bit hash
    bits = 0
    for i, val in enumerate(dct_low):
        if val > median:
            bits |= (1 << (63 - i))

    return f"{bits:016x}"


def phash_hamming_distance(h1: str, h2: str) -> int:
    """Hamming distance between two 16-char hex pHash strings.

    Returns the number of differing bits (0 = identical, 64 = opposite).
    A distance of ≤10 typically indicates the same visual content.
    """
    try:
        v1 = int(h1, 16)
        v2 = int(h2, 16)
        return bin(v1 ^ v2).count("1")
    except (ValueError, TypeError):
        return 64


# ── Convenience: compute both IDs for a VideoItem ────────────────

def compute_ids_for_video(video: "VideoItem") -> dict:
    """Compute both playarr_track_id and playarr_video_id for a VideoItem.

    Returns dict with keys: playarr_track_id, playarr_video_id.
    Does NOT compute phash (that requires file access and should be done
    separately via compute_phash).
    """
    track_id = compute_track_id(
        artist=video.artist,
        title=video.title,
        version_type=video.version_type or "normal",
        mb_recording_id=video.mb_recording_id,
        original_artist=video.original_artist,
        original_title=video.original_title,
    )
    video_id = compute_video_id(
        artist=video.artist,
        title=video.title,
        version_type=video.version_type or "normal",
        mb_recording_id=video.mb_recording_id,
        video_phash=video.video_phash,
    )
    return {"playarr_track_id": track_id, "playarr_video_id": video_id}
