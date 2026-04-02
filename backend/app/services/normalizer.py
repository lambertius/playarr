"""
Audio Normalizer — LUFS-based loudness normalization for music videos.

Inspired by VVN (Video Volume Normalization) script behavior:
1. Demux audio from video container
2. Measure integrated loudness (LUFS) using ffmpeg ebur128 filter
3. Apply gain adjustment (or loudnorm filter) to reach target LUFS
4. Remux adjusted audio back into the video container
5. Keep audit trail for undo/reprocessing
"""
import logging
import os
import subprocess
import tempfile
from typing import Optional, Tuple

from app.config import get_settings

logger = logging.getLogger(__name__)


def normalize_video(
    video_path: str,
    target_lufs: Optional[float] = None,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Normalize the audio of a video file to target LUFS.

    Steps:
    1. Extract audio to temp WAV
    2. Measure current LUFS
    3. Calculate required gain
    4. Apply gain and remux
    5. Replace original file

    Args:
        video_path: Path to the video file
        target_lufs: Target integrated loudness. None = use settings default.

    Returns:
        (measured_before, measured_after, gain_applied_db) or (None, None, None) on failure
    """
    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg

    if target_lufs is None:
        target_lufs = settings.normalization_target_lufs

    logger.info(f"Normalizing {video_path} to {target_lufs} LUFS")

    temp_dir = tempfile.mkdtemp(prefix="playarr_norm_")

    try:
        # Step 1: Extract audio to WAV
        audio_wav = os.path.join(temp_dir, "audio_original.wav")
        _extract_audio(ffmpeg, video_path, audio_wav)

        # Step 2: Measure current loudness
        measured_before = _measure_lufs(ffmpeg, audio_wav)
        if measured_before is None:
            logger.error("Failed to measure loudness")
            return None, None, None

        logger.info(f"Current loudness: {measured_before:.1f} LUFS")

        # Step 3: Calculate gain
        gain_db = target_lufs - measured_before
        logger.info(f"Gain to apply: {gain_db:.2f} dB")

        # Skip if already within 0.5 dB of target
        if abs(gain_db) < 0.5:
            logger.info("Already within target range, skipping normalization")
            return measured_before, measured_before, 0.0

        # Step 4: Apply gain to audio
        adjusted_audio = os.path.join(temp_dir, "audio_adjusted.wav")
        _apply_gain(ffmpeg, audio_wav, adjusted_audio, gain_db)

        # Step 5: Verify new loudness
        measured_after = _measure_lufs(ffmpeg, adjusted_audio)
        logger.info(f"New loudness: {measured_after:.1f} LUFS" if measured_after else "Could not verify")

        # Step 6: Remux adjusted audio back into video
        remuxed_video = os.path.join(temp_dir, "remuxed" + os.path.splitext(video_path)[1])
        _remux_audio(ffmpeg, video_path, adjusted_audio, remuxed_video)

        # Step 7: Replace original with remuxed version
        # Keep backup approach: rename original, move remuxed, delete original
        backup_path = video_path + ".prenorm"
        os.rename(video_path, backup_path)
        try:
            import shutil
            shutil.move(remuxed_video, video_path)
            os.remove(backup_path)
        except Exception:
            # Restore original if remux replacement fails
            if os.path.isfile(backup_path):
                os.rename(backup_path, video_path)
            raise

        logger.info(f"Normalization complete: {video_path}")
        return measured_before, measured_after, gain_db

    finally:
        # Cleanup temp directory
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def normalize_with_loudnorm(
    video_path: str,
    target_lufs: Optional[float] = None,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Alternative normalization using ffmpeg's loudnorm filter (two-pass).
    Provides better quality than simple gain adjustment.

    Uses the EBU R128 loudnorm filter parameters:
    - I: integrated loudness target
    - LRA: loudness range target
    - TP: true peak target
    """
    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg

    if target_lufs is None:
        target_lufs = settings.normalization_target_lufs

    lra = settings.normalization_lra
    tp = settings.normalization_tp

    logger.info(f"Normalizing (loudnorm) {video_path} to I={target_lufs}, LRA={lra}, TP={tp}")

    temp_dir = tempfile.mkdtemp(prefix="playarr_norm_")

    try:
        # Measure before
        measured_before = _measure_lufs(ffmpeg, video_path)

        # Skip if already within range
        if measured_before is not None and abs(target_lufs - measured_before) < 0.5:
            logger.info("Already within target range")
            return measured_before, measured_before, 0.0

        # Apply loudnorm via ffmpeg in one pass (simpler, slightly less precise)
        remuxed_video = os.path.join(temp_dir, "normalized" + os.path.splitext(video_path)[1])

        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-c:v", "copy",
            "-af", f"loudnorm=I={target_lufs}:LRA={lra}:TP={tp}",
            "-c:a", "aac", "-b:a", "192k",
            remuxed_video,
        ]

        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            logger.error(f"loudnorm failed: {result.stderr[-500:]}")
            return None, None, None

        # Measure after
        measured_after = _measure_lufs(ffmpeg, remuxed_video)
        gain_db = (target_lufs - measured_before) if measured_before else None

        # Replace original
        backup_path = video_path + ".prenorm"
        os.rename(video_path, backup_path)
        try:
            import shutil
            shutil.move(remuxed_video, video_path)
            os.remove(backup_path)
        except Exception:
            if os.path.isfile(backup_path):
                os.rename(backup_path, video_path)
            raise

        return measured_before, measured_after, gain_db

    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def _extract_audio(ffmpeg: str, video_path: str, output_wav: str):
    """Extract audio track from video to WAV."""
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-vn",  # No video
        "-acodec", "pcm_s16le",
        "-ar", "48000",
        "-ac", "2",
        output_wav,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Audio extraction failed: {result.stderr[-500:]}")
    logger.debug(f"Extracted audio to: {output_wav}")


def _measure_lufs(ffmpeg: str, file_path: str) -> Optional[float]:
    """Measure integrated loudness using ebur128 filter.

    IMPORTANT: We must use the LAST 'I:' value from the ebur128 output,
    which comes from the Summary section and represents the true integrated
    loudness.  Per-frame lines also contain 'I:' but those are running
    averages that can be wildly inaccurate (especially at the start of a
    track with a quiet intro).
    """
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i", file_path,
        "-af", "ebur128=peak=true",
        "-f", "null",
        "-",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        last_lufs = None
        for line in result.stderr.split("\n"):
            if "I:" in line and "LUFS" in line:
                parts = line.strip().split()
                for i, part in enumerate(parts):
                    if part == "I:":
                        try:
                            last_lufs = float(parts[i + 1])
                        except (IndexError, ValueError):
                            continue
        if last_lufs is not None:
            return last_lufs
    except Exception as e:
        logger.error(f"LUFS measurement error: {e}")

    return None


def _apply_gain(ffmpeg: str, input_path: str, output_path: str, gain_db: float):
    """Apply gain adjustment to audio file."""
    cmd = [
        ffmpeg, "-y",
        "-i", input_path,
        "-af", f"volume={gain_db}dB",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Gain adjustment failed: {result.stderr[-500:]}")
    logger.debug(f"Applied {gain_db}dB gain to: {output_path}")


def _remux_audio(ffmpeg: str, video_path: str, audio_path: str, output_path: str):
    """Remux adjusted audio back into video container, copying video stream."""
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0",
        "-map", "1:a:0",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"Remux failed: {result.stderr[-500:]}")
    logger.debug(f"Remuxed audio into: {output_path}")
