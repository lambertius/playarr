"""
Retry Policy Engine — Intelligent retry strategy with format fallback.

5-attempt strategy:
  1. Best quality (bestvideo+bestaudio/best)
  2. Re-fetch formats, retry same quality
  3. Cap at 1080p
  4. Prefer mp4 native container
  5. Single muxed stream (no merge)

Backoff schedule: 10s, 30s, 90s, 300s, 900s (+ jitter)
"""
import logging
import random
import time
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── Format strategies ────────────────────────────────────────────

STRATEGIES = [
    {
        "name": "best",
        "format_spec": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "description": "Best quality (default)",
    },
    {
        "name": "refetch_best",
        "format_spec": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "description": "Re-fetch formats, retry best quality",
    },
    {
        "name": "cap_1080p",
        "format_spec": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "description": "Cap resolution at 1080p",
    },
    {
        "name": "prefer_mp4",
        "format_spec": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "description": "Prefer native MP4 (avoid remux)",
    },
    {
        "name": "single_muxed",
        "format_spec": "best[ext=mp4]/best",
        "description": "Single muxed stream (no merge needed)",
    },
]

BACKOFF_SECONDS = [10, 30, 90, 300, 900]
MAX_ATTEMPTS = 5


@dataclass
class RetryDecision:
    """What the retry engine decided."""
    should_retry: bool
    attempt_num: int          # 1-based, next attempt
    strategy_name: str
    format_spec: str
    backoff_seconds: float
    reason: str


def get_strategy(attempt_num: int) -> dict:
    """Get the strategy for a given attempt number (1-based)."""
    idx = min(attempt_num - 1, len(STRATEGIES) - 1)
    return STRATEGIES[idx]


def compute_backoff(attempt_num: int) -> float:
    """Compute backoff with jitter for a given attempt (1-based)."""
    idx = min(attempt_num - 1, len(BACKOFF_SECONDS) - 1)
    base = BACKOFF_SECONDS[idx]
    jitter = random.uniform(0, base * 0.25)
    return base + jitter


def decide_retry(current_attempt: int, error_message: str = "") -> RetryDecision:
    """
    Decide whether and how to retry after a failure.

    Args:
        current_attempt: The attempt that just failed (1-based)
        error_message: Error text from the failed attempt

    Returns:
        RetryDecision with retry parameters
    """
    next_attempt = current_attempt + 1

    if next_attempt > MAX_ATTEMPTS:
        return RetryDecision(
            should_retry=False,
            attempt_num=next_attempt,
            strategy_name="exhausted",
            format_spec="",
            backoff_seconds=0,
            reason=f"All {MAX_ATTEMPTS} attempts exhausted",
        )

    strategy = get_strategy(next_attempt)
    backoff = compute_backoff(next_attempt)

    # Analyze error to potentially skip to a more aggressive strategy
    error_lower = (error_message or "").lower()
    reason = f"Attempt {current_attempt} failed"

    if "merge" in error_lower or "mux" in error_lower:
        # Merge failures → skip straight to prefer_mp4 or single_muxed
        if next_attempt < 4:
            strategy = STRATEGIES[3]  # prefer_mp4
            reason = "Merge failure detected → trying native MP4"
    elif "403" in error_lower or "forbidden" in error_lower:
        # Auth/rate limit → re-fetch is good
        reason = "Access error → re-fetching formats"
    elif "format" in error_lower and "not available" in error_lower:
        # Format not available → cap resolution
        if next_attempt < 3:
            strategy = STRATEGIES[2]  # cap_1080p
            reason = "Format unavailable → capping resolution"
    elif "timeout" in error_lower or "timed out" in error_lower:
        reason = "Timeout → retrying with backoff"

    return RetryDecision(
        should_retry=True,
        attempt_num=next_attempt,
        strategy_name=strategy["name"],
        format_spec=strategy["format_spec"],
        backoff_seconds=backoff,
        reason=reason,
    )


def should_auto_retry(error_message: str) -> bool:
    """
    Determine if an error is potentially recoverable *as a download error*.
    Some errors (e.g. invalid URL) don't benefit from retrying.

    Importantly, infrastructure errors like SQLite locks are NOT download
    failures and must NOT trigger format-fallback retries.
    """
    error_lower = (error_message or "").lower()

    # Non-recoverable / non-download errors — retrying with a different
    # yt-dlp format spec won't help with any of these.
    non_recoverable = [
        "unsupported url",
        "is not a valid url",
        "video unavailable",
        "private video",
        "removed by the uploader",
        "account terminated",
        "copyright",
        "this video has been removed",
        # Infrastructure errors (not download failures)
        "database is locked",
        "operationalerror",
        "sqlite",
    ]
    for nr in non_recoverable:
        if nr in error_lower:
            return False

    return True


def format_backoff_display(seconds: float) -> str:
    """Human-readable backoff string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s" if secs else f"{minutes}m"
