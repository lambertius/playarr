"""
Telemetry Service — Real-time ephemeral metrics for active jobs.

All telemetry data is held in-memory (not persisted to DB).
Thread-safe: multiple worker threads update concurrently, SSE reads safely.
"""
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class AttemptRecord:
    """One download/process attempt."""
    attempt_num: int
    started_at: float  # time.time()
    ended_at: Optional[float] = None
    strategy: str = "best"          # format strategy used
    reason: str = ""                # why this attempt happened
    outcome: str = "in_progress"    # in_progress | success | failed | cancelled
    error: str = ""
    format_spec: str = ""


@dataclass
class DownloadMetrics:
    """Real-time download telemetry."""
    speed_bytes: float = 0.0                # current speed in bytes/s
    avg_speed_30s: float = 0.0              # rolling 30s average
    downloaded_bytes: int = 0
    total_bytes: int = 0
    eta_seconds: float = 0.0
    fragments_done: int = 0
    fragments_total: int = 0
    percent: float = 0.0
    selected_format: str = ""
    last_progress_at: float = 0.0           # time.time() of last progress update
    consecutive_stall_seconds: float = 0.0  # seconds at < 50 KB/s


@dataclass
class ProcessMetrics:
    """Real-time processing (ffmpeg) telemetry."""
    step_name: str = ""
    speed_factor: float = 0.0   # e.g. 2.3x realtime
    fps: float = 0.0
    progress_pct: float = 0.0
    elapsed_seconds: float = 0.0


@dataclass
class HealthInfo:
    """Derived health / risk assessment."""
    stall_flags: List[str] = field(default_factory=list)
    risk_score: int = 0             # 0-100
    recommended_action: str = ""    # "", "retry", "format_fallback", "cancel"


@dataclass
class JobTelemetry:
    """Full telemetry snapshot for one job."""
    job_id: int = 0
    download: DownloadMetrics = field(default_factory=DownloadMetrics)
    process: ProcessMetrics = field(default_factory=ProcessMetrics)
    health: HealthInfo = field(default_factory=HealthInfo)
    attempts: List[AttemptRecord] = field(default_factory=list)
    _speed_history: deque = field(default_factory=lambda: deque(maxlen=60))  # 1 sample/s for 60s

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for SSE / API (excludes private fields)."""
        d = asdict(self)
        d.pop("_speed_history", None)
        return d


class TelemetryStore:
    """Thread-safe in-memory telemetry store."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[int, JobTelemetry] = {}
        self._subscribers: List[Any] = []  # SSE subscriber queues
        self._sub_lock = threading.Lock()

    # ── Lifecycle ────────────────────────────────────────────────

    def create(self, job_id: int) -> JobTelemetry:
        with self._lock:
            t = JobTelemetry(job_id=job_id)
            self._jobs[job_id] = t
            return t

    def get(self, job_id: int) -> Optional[JobTelemetry]:
        with self._lock:
            return self._jobs.get(job_id)

    def remove(self, job_id: int):
        with self._lock:
            self._jobs.pop(job_id, None)

    def active_ids(self) -> List[int]:
        with self._lock:
            return list(self._jobs.keys())

    def snapshot_all(self) -> Dict[int, Dict[str, Any]]:
        """Return serialisable snapshot of all active telemetry."""
        with self._lock:
            return {jid: t.to_dict() for jid, t in self._jobs.items()}

    def snapshot(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            t = self._jobs.get(job_id)
            return t.to_dict() if t else None

    # ── Download updates ─────────────────────────────────────────

    def update_download(self, job_id: int, *,
                        speed_bytes: float = None,
                        downloaded_bytes: int = None,
                        total_bytes: int = None,
                        eta_seconds: float = None,
                        fragments_done: int = None,
                        fragments_total: int = None,
                        percent: float = None,
                        selected_format: str = None):
        with self._lock:
            t = self._jobs.get(job_id)
            if not t:
                return
            dl = t.download
            now = time.time()

            if speed_bytes is not None:
                dl.speed_bytes = speed_bytes
                t._speed_history.append((now, speed_bytes))
                # Rolling 30s average
                cutoff = now - 30
                recent = [s for ts, s in t._speed_history if ts >= cutoff]
                dl.avg_speed_30s = sum(recent) / len(recent) if recent else 0.0
                # Stall detection: < 50 KB/s
                if speed_bytes < 50 * 1024:
                    if dl.last_progress_at > 0:
                        dl.consecutive_stall_seconds = now - dl.last_progress_at
                else:
                    dl.consecutive_stall_seconds = 0.0
                    dl.last_progress_at = now

            if downloaded_bytes is not None:
                dl.downloaded_bytes = downloaded_bytes
            if total_bytes is not None:
                dl.total_bytes = total_bytes
            if eta_seconds is not None:
                dl.eta_seconds = eta_seconds
            if fragments_done is not None:
                dl.fragments_done = fragments_done
            if fragments_total is not None:
                dl.fragments_total = fragments_total
            if percent is not None:
                dl.percent = percent
                if dl.last_progress_at == 0.0:
                    dl.last_progress_at = now
            if selected_format is not None:
                dl.selected_format = selected_format

            # Recompute health
            self._recompute_health(t)

    # ── Process updates ──────────────────────────────────────────

    def update_process(self, job_id: int, *,
                       step_name: str = None,
                       speed_factor: float = None,
                       fps: float = None,
                       progress_pct: float = None,
                       elapsed_seconds: float = None):
        with self._lock:
            t = self._jobs.get(job_id)
            if not t:
                return
            p = t.process
            if step_name is not None:
                p.step_name = step_name
            if speed_factor is not None:
                p.speed_factor = speed_factor
            if fps is not None:
                p.fps = fps
            if progress_pct is not None:
                p.progress_pct = progress_pct
            if elapsed_seconds is not None:
                p.elapsed_seconds = elapsed_seconds

    # ── Attempt tracking ─────────────────────────────────────────

    def start_attempt(self, job_id: int, attempt_num: int,
                      strategy: str = "best", reason: str = "",
                      format_spec: str = "") -> Optional[AttemptRecord]:
        with self._lock:
            t = self._jobs.get(job_id)
            if not t:
                return None
            rec = AttemptRecord(
                attempt_num=attempt_num,
                started_at=time.time(),
                strategy=strategy,
                reason=reason,
                format_spec=format_spec,
            )
            t.attempts.append(rec)
            return rec

    def end_attempt(self, job_id: int, outcome: str, error: str = ""):
        with self._lock:
            t = self._jobs.get(job_id)
            if not t or not t.attempts:
                return
            rec = t.attempts[-1]
            rec.ended_at = time.time()
            rec.outcome = outcome
            rec.error = error

    # ── SSE subscriber management ────────────────────────────────

    def subscribe(self):
        """Return an asyncio.Queue-like object for SSE."""
        import asyncio
        q = asyncio.Queue(maxsize=50)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def notify_subscribers(self):
        """Push current snapshot to all SSE subscribers."""
        snap = self.snapshot_all()
        with self._sub_lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(snap)
                except Exception:
                    dead.append(q)
            for q in dead:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass

    # ── Internal helpers ─────────────────────────────────────────

    def _recompute_health(self, t: JobTelemetry):
        """Derive health/risk from current metrics."""
        h = t.health
        h.stall_flags = []
        score = 0

        dl = t.download
        # Stall: speed < 50 KB/s for > 60 seconds
        if dl.consecutive_stall_seconds > 60:
            h.stall_flags.append("low_speed_stall")
            score += 40

        # No progress for 90 seconds
        if dl.last_progress_at > 0:
            silent = time.time() - dl.last_progress_at
            if silent > 90:
                h.stall_flags.append("no_progress")
                score += 50

        # High retry count
        if len(t.attempts) >= 3:
            score += 20
        elif len(t.attempts) >= 2:
            score += 10

        # Low average speed
        if dl.avg_speed_30s > 0 and dl.avg_speed_30s < 100 * 1024:
            h.stall_flags.append("slow_avg")
            score += 15

        h.risk_score = min(score, 100)

        if h.risk_score >= 70:
            h.recommended_action = "format_fallback"
        elif h.risk_score >= 40:
            h.recommended_action = "retry"
        else:
            h.recommended_action = ""


# ── Singleton ────────────────────────────────────────────────────
telemetry_store = TelemetryStore()
