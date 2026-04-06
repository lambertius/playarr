"""
Audio Fingerprint Service — Shazam-style track identification fallback.

Uses Chromaprint + AcoustID to identify tracks when metadata is uncertain.

Pipeline:
1. Extract a 20-second audio segment (starting at 30% of video)
2. Generate a fingerprint using `fpcalc` (Chromaprint CLI tool)
3. Query AcoustID API for matching recordings
4. Return candidate matches with MusicBrainz metadata

Requirements:
- fpcalc (Chromaprint) must be installed and accessible
  - Windows: Download from https://acoustid.org/chromaprint
  - Linux: apt install libchromaprint-tools
  - macOS: brew install chromaprint
- Optional: AcoustID API key (free at https://acoustid.org/new-application)

This is a fallback mechanism — only invoked when:
- Mismatch detection flags metadata as suspicious
- User explicitly requests fingerprint identification
- AI confidence is below threshold
"""
import json
import logging
import os
import subprocess

from app.subprocess_utils import HIDE_WINDOW
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# AcoustID API endpoint
ACOUSTID_API_URL = "https://api.acoustid.org/v2/lookup"

# Default AcoustID client API key (free tier, register at acoustid.org)
# Users should provide their own key in settings for production use
DEFAULT_ACOUSTID_KEY = ""

# Audio extraction parameters
EXTRACT_DURATION = 20     # seconds
EXTRACT_START_PERCENT = 30  # start at 30% of video


@dataclass
class FingerprintMatch:
    """A single fingerprint match result."""
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    mb_recording_id: Optional[str] = None
    mb_release_id: Optional[str] = None
    confidence: float = 0.0


@dataclass
class FingerprintResult:
    """Complete fingerprint analysis result."""
    fingerprint: str = ""
    duration: float = 0.0
    matches: List[FingerprintMatch] = field(default_factory=list)
    best_match: Optional[FingerprintMatch] = None
    error: Optional[str] = None
    fpcalc_available: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fingerprint_preview": self.fingerprint[:50] + "..." if self.fingerprint else "",
            "duration": self.duration,
            "match_count": len(self.matches),
            "best_match": {
                "artist": self.best_match.artist,
                "title": self.best_match.title,
                "album": self.best_match.album,
                "year": self.best_match.year,
                "confidence": round(self.best_match.confidence, 3),
                "mb_recording_id": self.best_match.mb_recording_id,
            } if self.best_match else None,
            "error": self.error,
            "fpcalc_available": self.fpcalc_available,
        }


def identify_track(
    file_path: str,
    acoustid_api_key: Optional[str] = None,
    fpcalc_path: str = "fpcalc",
    ffmpeg_path: str = "ffmpeg",
) -> FingerprintResult:
    """
    Identify a track using audio fingerprinting.

    Args:
        file_path: Path to the video/audio file
        acoustid_api_key: AcoustID API key (None = use default)
        fpcalc_path: Path to fpcalc binary
        ffmpeg_path: Path to ffmpeg binary (for audio extraction)

    Returns:
        FingerprintResult with match candidates
    """
    result = FingerprintResult()

    if not os.path.isfile(file_path):
        result.error = f"File not found: {file_path}"
        return result

    # Check if fpcalc is available
    if not _is_fpcalc_available(fpcalc_path):
        result.fpcalc_available = False
        result.error = (
            "fpcalc (Chromaprint) not found. Install from https://acoustid.org/chromaprint"
        )
        logger.warning(result.error)
        return result

    try:
        # Step 1: Extract audio segment
        audio_file = _extract_audio_segment(
            file_path, ffmpeg_path, EXTRACT_DURATION, EXTRACT_START_PERCENT,
        )
        if not audio_file:
            result.error = "Failed to extract audio segment"
            return result

        try:
            # Step 2: Generate fingerprint
            fingerprint, duration = _generate_fingerprint(
                audio_file, fpcalc_path,
            )
            result.fingerprint = fingerprint
            result.duration = duration

            if not fingerprint:
                result.error = "Failed to generate fingerprint"
                return result

            # Step 3: Query AcoustID
            api_key = acoustid_api_key or _get_acoustid_key()
            if not api_key:
                result.error = "AcoustID API key not configured. Set 'acoustid_api_key' in Settings → AI."
                return result

            matches = _query_acoustid(fingerprint, duration, api_key)
            result.matches = matches

            if matches:
                result.best_match = max(matches, key=lambda m: m.confidence)

            logger.info(
                f"Fingerprint: {len(matches)} matches found for {os.path.basename(file_path)}"
                + (f" — best: {result.best_match.artist} - {result.best_match.title} ({result.best_match.confidence:.2f})"
                   if result.best_match else "")
            )

        finally:
            # Clean up temp audio file
            if os.path.isfile(audio_file):
                try:
                    os.unlink(audio_file)
                except OSError:
                    pass

    except Exception as e:
        result.error = str(e)
        logger.error(f"Fingerprint identification failed: {e}")

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_fpcalc_available(fpcalc_path: str) -> bool:
    """Check if fpcalc binary is available."""
    try:
        result = subprocess.run(
            [fpcalc_path, "-version"],
            capture_output=True, text=True, timeout=5,
            **HIDE_WINDOW,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _extract_audio_segment(
    file_path: str,
    ffmpeg_path: str,
    duration: int,
    start_percent: int,
) -> Optional[str]:
    """
    Extract a short audio segment from a video file.

    Returns path to temporary WAV file, or None on failure.
    """
    # Get video duration
    try:
        probe_cmd = [
            ffmpeg_path.replace("ffmpeg", "ffprobe") if "ffmpeg" in ffmpeg_path else "ffprobe",
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "json",
            file_path,
        ]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15, **HIDE_WINDOW)
        if probe.returncode != 0:
            logger.warning(f"ffprobe failed: {probe.stderr[:200]}")
            total_dur = 0
        else:
            data = json.loads(probe.stdout)
            total_dur = float(data.get("format", {}).get("duration", 0))
    except Exception:
        total_dur = 0

    # Calculate start position
    start_sec = max(0, (total_dur * start_percent / 100) if total_dur > 0 else 10)

    # Create temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        cmd = [
            ffmpeg_path, "-y",
            "-ss", str(start_sec),
            "-i", file_path,
            "-t", str(duration),
            "-ac", "1",            # Mono
            "-ar", "44100",        # 44.1kHz
            "-acodec", "pcm_s16le",
            tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, **HIDE_WINDOW)
        if result.returncode != 0:
            logger.warning(f"Audio extraction failed: {result.stderr[:200]}")
            os.unlink(tmp_path)
            return None
        return tmp_path
    except Exception as e:
        logger.error(f"Audio extraction error: {e}")
        if os.path.isfile(tmp_path):
            os.unlink(tmp_path)
        return None


def _generate_fingerprint(
    audio_path: str,
    fpcalc_path: str,
) -> tuple:
    """
    Generate a Chromaprint fingerprint from an audio file.

    Returns (fingerprint_string, duration) or ("", 0) on failure.
    """
    try:
        cmd = [fpcalc_path, "-json", audio_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **HIDE_WINDOW)

        if result.returncode != 0:
            logger.warning(f"fpcalc failed: {result.stderr[:200]}")
            return "", 0

        data = json.loads(result.stdout)
        return data.get("fingerprint", ""), float(data.get("duration", 0))

    except json.JSONDecodeError:
        # Fallback: parse non-JSON output
        lines = result.stdout.strip().split("\n")
        fingerprint = ""
        duration = 0
        for line in lines:
            if line.startswith("FINGERPRINT="):
                fingerprint = line.split("=", 1)[1]
            elif line.startswith("DURATION="):
                duration = float(line.split("=", 1)[1])
        return fingerprint, duration
    except Exception as e:
        logger.error(f"fpcalc error: {e}")
        return "", 0


def _query_acoustid(
    fingerprint: str,
    duration: float,
    api_key: str,
) -> List[FingerprintMatch]:
    """
    Query AcoustID API with a fingerprint.

    Returns list of FingerprintMatch candidates, sorted by confidence.
    """
    try:
        resp = httpx.post(
            ACOUSTID_API_URL,
            data={
                "client": api_key,
                "duration": str(int(duration)),
                "fingerprint": fingerprint,
                "meta": "recordings+releases+releasegroups",
            },
            timeout=15,
        )

        if resp.status_code != 200:
            logger.warning(f"AcoustID API error {resp.status_code}: {resp.text[:200]}")
            return []

        data = resp.json()
        if data.get("status") != "ok":
            logger.warning(f"AcoustID error: {data.get('error', {}).get('message', 'Unknown')}")
            return []

        results = data.get("results", [])
        matches = []

        for r in results:
            score = float(r.get("score", 0))
            if score < 0.3:
                continue  # Skip very low confidence

            recordings = r.get("recordings", [])
            for rec in recordings:
                match = FingerprintMatch(
                    confidence=score,
                    mb_recording_id=rec.get("id"),
                )

                # Artist
                artists = rec.get("artists", [])
                if artists:
                    match.artist = " & ".join(a.get("name", "") for a in artists)

                # Title
                match.title = rec.get("title")

                # Release (album + year)
                releases = rec.get("releasegroups", []) or rec.get("releases", [])
                if releases:
                    rel = releases[0]
                    match.album = rel.get("title")
                    match.mb_release_id = rel.get("id")

                    # Year from release date
                    date_str = rel.get("first-release-date") or rel.get("date", "")
                    if date_str:
                        try:
                            match.year = int(date_str[:4])
                        except (ValueError, IndexError):
                            pass

                if match.artist or match.title:
                    matches.append(match)

        # Sort by confidence, deduplicate by recording ID
        seen_ids = set()
        unique_matches = []
        for m in sorted(matches, key=lambda x: x.confidence, reverse=True):
            key = m.mb_recording_id or f"{m.artist}-{m.title}"
            if key not in seen_ids:
                seen_ids.add(key)
                unique_matches.append(m)

        return unique_matches[:10]  # Top 10 matches

    except Exception as e:
        logger.error(f"AcoustID query failed: {e}")
        return []


def _get_acoustid_key() -> Optional[str]:
    """Retrieve the AcoustID API key from settings."""
    try:
        from app.database import SessionLocal
        from app.models import AppSetting

        db = SessionLocal()
        try:
            setting = db.query(AppSetting).filter(
                AppSetting.key == "acoustid_api_key",
                AppSetting.user_id.is_(None),
            ).first()
            return setting.value if setting else DEFAULT_ACOUSTID_KEY or None
        finally:
            db.close()
    except Exception:
        return DEFAULT_ACOUSTID_KEY or None


# ---------------------------------------------------------------------------
# Chromaprint fingerprint decoding & comparison
# ---------------------------------------------------------------------------

class _BitReader:
    """Read individual bits from a byte buffer (LSB-first)."""
    __slots__ = ("_data", "_pos", "_total")

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0
        self._total = len(data) * 8

    def read(self, n: int = 1) -> int:
        val = 0
        for i in range(n):
            if self._pos >= self._total:
                return val
            byte_idx = self._pos >> 3
            bit_idx = self._pos & 7
            val |= ((self._data[byte_idx] >> bit_idx) & 1) << i
            self._pos += 1
        return val

    def has_bits(self, n: int = 1) -> bool:
        return (self._total - self._pos) >= n


def decode_chromaprint(encoded: str) -> List[int]:
    """Decode a base64-encoded compressed Chromaprint fingerprint to a list
    of 32-bit integers.

    Uses the Chromaprint packed format:
      Header (4 bytes): algorithm (1 byte) + num_values (3 bytes big-endian)
      Body: 16 groups of 2 bits each, delta-encoded in gray code

    Returns an empty list if decoding fails.
    """
    import base64

    try:
        # Chromaprint uses URL-safe base64
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4))
    except Exception:
        return []

    if len(raw) < 4:
        return []

    # Header: algorithm byte + 3-byte big-endian count
    num_values = (raw[1] << 16) | (raw[2] << 8) | raw[3]

    if num_values <= 0 or num_values > 200000:
        return []

    reader = _BitReader(raw[4:])
    values = [0] * num_values

    # Decode 16 groups (each controls 2 bits of the 32-bit fingerprint)
    for group in range(16):
        # Phase 1: read "changed" bits (which positions have non-zero delta)
        changed = [False] * num_values
        for i in range(num_values):
            if not reader.has_bits():
                break
            changed[i] = reader.read() == 1

        # Phase 2: read delta values for changed positions
        prev_value = 0
        for i in range(num_values):
            if not changed[i]:
                # Value unchanged, carry forward
                values[i] |= (prev_value & 3) << (group * 2)
                continue
            if not reader.has_bits():
                values[i] |= (prev_value & 3) << (group * 2)
                continue

            exceptional = reader.read()
            if exceptional:
                # Read unary-coded value (count of 1-bits before 0 terminator)
                v = 0
                while reader.has_bits() and reader.read() == 1:
                    v += 1
                delta = v + 2
            else:
                delta = 1

            # Zigzag decode: odd -> negative, even -> positive
            if delta & 1:
                prev_value -= (delta + 1) >> 1
            else:
                prev_value += delta >> 1

            values[i] |= (prev_value & 3) << (group * 2)

    # Convert from gray code to binary
    result = []
    for v in values:
        # Unsigned 32-bit mask
        v &= 0xFFFFFFFF
        v ^= (v >> 1)
        result.append(v)

    return result


def fingerprint_similarity(fp1_encoded: str, fp2_encoded: str) -> float:
    """Compute similarity between two Chromaprint fingerprint strings.

    Decodes the compressed fingerprints, then computes bit-level similarity
    using Hamming distance on the overlapping portion of the integer arrays.
    Applies a length penalty when arrays differ significantly in size (which
    accounts for different video durations).

    Returns a float from 0.0 (completely different) to 1.0 (identical).
    A threshold of ~0.7 is appropriate for detecting duplicates with some
    tolerance for quality differences and length variations.
    """
    fp1 = decode_chromaprint(fp1_encoded)
    fp2 = decode_chromaprint(fp2_encoded)

    if not fp1 or not fp2:
        return 0.0

    # Compare the overlapping portion
    compare_len = min(len(fp1), len(fp2))
    if compare_len == 0:
        return 0.0

    # Length ratio penalty -- very different lengths unlikely to be same song
    length_ratio = compare_len / max(len(fp1), len(fp2))
    if length_ratio < 0.4:
        return 0.0

    matching_bits = 0
    total_bits = compare_len * 32

    for i in range(compare_len):
        xor = fp1[i] ^ fp2[i]
        matching_bits += 32 - bin(xor & 0xFFFFFFFF).count("1")

    raw_similarity = matching_bits / total_bits if total_bits > 0 else 0.0
    return raw_similarity * length_ratio
