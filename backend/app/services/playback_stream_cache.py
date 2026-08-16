"""Byte-stable, resumable cache for FFmpeg-backed playback responses.

The first HTTP request tails an append-only part file while one background
producer writes FFmpeg's stdout. A completed artifact is atomically published
and all later requests use normal HTTP byte ranges against those exact bytes.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from email.utils import formatdate
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

if os.name == "nt":
    import msvcrt
else:
    import fcntl


logger = logging.getLogger(__name__)

PLAYBACK_CACHE_MAX_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB
PLAYBACK_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
PLAYBACK_CACHE_RECENT_GRACE_SECONDS = 60 * 60
PLAYBACK_CACHE_PRUNE_INTERVAL_SECONDS = 5 * 60
PLAYBACK_CACHE_PART_MAX_AGE_SECONDS = 60 * 60
PLAYBACK_CACHE_RECONNECT_GRACE_SECONDS = 30
PLAYBACK_CACHE_RANGE_WAIT_SECONDS = 5 * 60
PLAYBACK_PRODUCER_HEARTBEAT_SECONDS = 2
PLAYBACK_PRODUCER_IDLE_TIMEOUT_SECONDS = 2 * 60
PLAYBACK_PRODUCER_MAX_RUNTIME_SECONDS = 4 * 60 * 60
PLAYBACK_CACHE_FORMAT_VERSION = 1
STREAM_READ_SIZE = 256 * 1024
MAX_RANGE_HEADER_LENGTH = 256
MAX_RANGE_NUMBER_DIGITS = 40

SpawnProcess = Callable[[list], Any]
ProcessHook = Callable[[str, Any], None]

_cache_lock = threading.Lock()
_cache_last_prune = 0.0
_producers_lock = threading.Lock()
_producers: dict[Path, "PlaybackProducer"] = {}


@dataclass(frozen=True)
class ParsedByteRange:
    """Syntactic classification of one HTTP Range header."""

    kind: Literal[
        "none", "single", "unsupported", "malformed", "multiple", "unsatisfiable"
    ]
    start: int | None = None
    end: int | None = None
    suffix_length: int | None = None

    @property
    def progressive_initial(self) -> bool:
        """Only an absent Range or exact ``bytes=0-`` may stream from a miss."""
        return self.kind in ("none", "unsupported") or (
            self.kind == "single"
            and self.start == 0
            and self.end is None
            and self.suffix_length is None
        )


@dataclass(frozen=True)
class ExternalPlaybackProducer:
    """A representation currently owned by another local web worker."""

    cache_path: Path
    lock_path: Path


def source_fingerprint(file_path: str) -> tuple:
    stat = os.stat(file_path)
    return (
        os.path.normcase(os.path.realpath(file_path)),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def parse_byte_range(range_header: str | None) -> ParsedByteRange:
    """Parse a single byte range without needing the representation size.

    Multiple ranges are deliberately rejected rather than silently turning a
    retry into a timestamp-zero body.  Playarr's media clients only need one
    contiguous range and this keeps behavior identical across Starlette
    versions.
    """
    if range_header is None:
        return ParsedByteRange("none")
    if len(range_header) > MAX_RANGE_HEADER_LENGTH:
        return ParsedByteRange("malformed")
    try:
        unit, value = range_header.split("=", 1)
    except ValueError:
        return ParsedByteRange("malformed")
    if unit.strip().lower() != "bytes":
        return ParsedByteRange("unsupported")
    value = value.strip()
    if not value:
        return ParsedByteRange("malformed")
    if "," in value:
        return ParsedByteRange("multiple")
    match = re.fullmatch(r"([0-9]*)-([0-9]*)", value)
    if not match:
        return ParsedByteRange("malformed")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return ParsedByteRange("malformed")
    if max(len(start_text), len(end_text)) > MAX_RANGE_NUMBER_DIGITS:
        return ParsedByteRange("malformed")
    if not start_text:
        suffix_length = int(end_text)
        if suffix_length == 0:
            return ParsedByteRange("unsatisfiable")
        return ParsedByteRange("single", suffix_length=suffix_length)
    start = int(start_text)
    end = int(end_text) if end_text else None
    if end is not None and start > end:
        return ParsedByteRange("malformed")
    return ParsedByteRange("single", start=start, end=end)


def _resolve_byte_range(spec: ParsedByteRange, file_size: int) -> tuple[int, int] | None:
    """Resolve a parsed range to inclusive offsets, or None if unsatisfiable."""
    if spec.kind != "single" or file_size <= 0:
        return None
    if spec.suffix_length is not None:
        start = max(0, file_size - spec.suffix_length)
        return start, file_size - 1
    if spec.start is None or spec.start >= file_size:
        return None
    end = file_size - 1 if spec.end is None else min(spec.end, file_size - 1)
    if end < spec.start:
        return None
    return spec.start, end


def playback_cache_path(cmd: list, file_path: str, label: str, cache_root: Path | str) -> Path:
    """Return the cache path for one exact transformed representation."""
    identity = "\0".join([
        str(PLAYBACK_CACHE_FORMAT_VERSION),
        *(str(field) for field in source_fingerprint(file_path)),
        label,
        *(str(part) for part in cmd),
    ])
    digest = hashlib.sha256(identity.encode("utf-8", "surrogatepass")).hexdigest()
    cache_dir = Path(cache_root) / "playback"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{digest}.mp4"


def playback_producer_lock_path(cache_path: Path) -> Path:
    return cache_path.with_name(f"{cache_path.name}.lock")


def _try_acquire_producer_lock(cache_path: Path) -> int | None:
    """Try to take a kernel-held per-representation lock without blocking."""
    lock_path = playback_producer_lock_path(cache_path)
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        logger.warning("Could not open playback producer lock %s", lock_path, exc_info=True)
        return None
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release_producer_lock_fd(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _external_producer_is_running(cache_path: Path) -> bool:
    fd = _try_acquire_producer_lock(cache_path)
    if fd is None:
        return True
    _release_producer_lock_fd(fd)
    return False


def _remove_cache_file(path: Path) -> int:
    """Best-effort cache removal; return the number of bytes removed."""
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except OSError:
        # A FileResponse may currently hold the file open on Windows. It will
        # be considered again during the next maintenance pass.
        return 0


def prune_playback_cache(cache_dir: Path, preserve: Path | None = None) -> None:
    """Bound transformed-media cache age and disk use without hot-path walks."""
    global _cache_last_prune
    # A long-running transcode can legitimately keep a part file open beyond
    # the stale-part cutoff.  Snapshot active paths before pruning so Unix does
    # not unlink a producer's append-only file out from under its readers
    # (Windows would normally reject that unlink instead).
    with _producers_lock:
        active_parts = {
            entry.part_path for entry in _producers.values() if not entry.finished
        }
        active_outputs = {
            entry.cache_path for entry in _producers.values() if not entry.finished
        }
    now_mono = time.monotonic()
    with _cache_lock:
        if now_mono - _cache_last_prune < PLAYBACK_CACHE_PRUNE_INTERVAL_SECONDS:
            return
        _cache_last_prune = now_mono

        now = time.time()
        try:
            entries = list(cache_dir.iterdir())
        except OSError:
            return

        for path in entries:
            if ".part-" not in path.name or path in active_parts:
                continue
            try:
                if now - path.stat().st_mtime <= PLAYBACK_CACHE_PART_MAX_AGE_SECONDS:
                    continue
            except OSError:
                continue

            # A part owned by a different web worker is not represented in
            # this process's registry.  Only delete it after taking the same
            # per-key lock used by producers; a busy lock proves it is live.
            cache_name = path.name.split(".part-", 1)[0]
            part_cache_path = path.with_name(cache_name)
            lock_fd = _try_acquire_producer_lock(part_cache_path)
            if lock_fd is None:
                continue
            try:
                try:
                    still_stale = (
                        now - path.stat().st_mtime
                        > PLAYBACK_CACHE_PART_MAX_AGE_SECONDS
                    )
                except OSError:
                    still_stale = False
                if still_stale:
                    _remove_cache_file(path)
            finally:
                _release_producer_lock_fd(lock_fd)

        completed: list[tuple[Path, os.stat_result]] = []
        for path in entries:
            if path.suffix != ".mp4" or path == preserve or path in active_outputs:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if now - stat.st_mtime > PLAYBACK_CACHE_MAX_AGE_SECONDS:
                _remove_cache_file(path)
            else:
                completed.append((path, stat))

        total = sum(stat.st_size for _, stat in completed)
        if preserve and preserve.is_file():
            try:
                total += preserve.stat().st_size
            except OSError:
                pass
        if total <= PLAYBACK_CACHE_MAX_BYTES:
            return

        for path, stat in sorted(completed, key=lambda entry: entry[1].st_mtime):
            if total <= PLAYBACK_CACHE_MAX_BYTES:
                break
            # Retain fresh artifacts across the initial response's immediate
            # refill requests and avoid racing a currently-playing TV client.
            if now - stat.st_mtime < PLAYBACK_CACHE_RECENT_GRACE_SECONDS:
                continue
            total -= _remove_cache_file(path)


def _cache_validator_headers(stat: os.stat_result) -> dict[str, str]:
    return {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
        "ETag": f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"',
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
    }


def _iter_cached_file(path: Path, start: int, length: int):
    with open(path, "rb") as source:
        source.seek(start)
        remaining = length
        while remaining > 0:
            chunk = source.read(min(STREAM_READ_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _range_error_response(status_code: int, file_size: int | None = None) -> Response:
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    if status_code == 416 and file_size is not None:
        headers["Content-Range"] = f"bytes */{file_size}"
    return Response(status_code=status_code, headers=headers)


def _full_cached_response(
    path: Path,
    request: Request,
    stat: os.stat_result,
    headers: dict[str, str],
) -> Response:
    full_headers = {**headers, "Content-Length": str(stat.st_size)}
    if request.method.upper() == "HEAD":
        return Response(status_code=200, media_type="video/mp4", headers=full_headers)
    return StreamingResponse(
        _iter_cached_file(path, 0, stat.st_size),
        status_code=200,
        media_type="video/mp4",
        headers=full_headers,
    )


def cached_playback_response(
    path: Path,
    request: Request,
    range_spec: ParsedByteRange | None = None,
) -> Response:
    """Serve a completed artifact with version-independent single ranges."""
    try:
        stat = path.stat()
    except OSError:
        return Response(
            content="Transformed media cache disappeared; retry",
            status_code=503,
            media_type="text/plain",
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )
    headers = _cache_validator_headers(stat)
    spec = range_spec or parse_byte_range(request.headers.get("range"))
    if spec.kind == "malformed":
        return _range_error_response(400)
    if spec.kind == "multiple":
        return _range_error_response(416, stat.st_size)
    if spec.kind == "unsatisfiable":
        return _range_error_response(416, stat.st_size)
    if spec.kind in ("none", "unsupported"):
        return _full_cached_response(path, request, stat, headers)

    resolved = _resolve_byte_range(spec, stat.st_size)
    if resolved is None:
        return _range_error_response(416, stat.st_size)
    start, end = resolved

    # RFC 9110 normally turns an If-Range validator mismatch into a full 200.
    # For transformed playback that would be indistinguishable from the bug we
    # are preventing: a nonzero retry suddenly receiving timestamp zero. Make
    # the stale validator explicit so the media element can reload deliberately.
    if_range = request.headers.get("if-range")
    if if_range and if_range not in (headers["ETag"], headers["Last-Modified"]):
        return Response(
            content="Cached representation changed; reload media",
            status_code=412,
            media_type="text/plain",
            headers={**headers, "Cache-Control": "no-store"},
        )

    length = end - start + 1
    range_headers = {
        **headers,
        "Content-Range": f"bytes {start}-{end}/{stat.st_size}",
        "Content-Length": str(length),
    }
    if request.method.upper() == "HEAD":
        return Response(status_code=206, media_type="video/mp4", headers=range_headers)
    return StreamingResponse(
        _iter_cached_file(path, start, length),
        status_code=206,
        media_type="video/mp4",
        headers=range_headers,
    )


class PlaybackProducer:
    """One append-only FFmpeg representation shared by all HTTP consumers."""

    def __init__(
        self,
        cache_path: Path,
        cmd: list,
        file_path: str,
        label: str,
        heavy: bool,
        spawn_process: SpawnProcess,
        register_process: ProcessHook,
        unregister_process: ProcessHook,
        heavy_semaphore: threading.BoundedSemaphore,
    ):
        self.cache_path = cache_path
        self.lock_path = playback_producer_lock_path(cache_path)
        self.lock_fd: int | None = None
        part_token = secrets.token_hex(16)
        self.part_path = cache_path.with_name(
            f"{cache_path.name}.part-{os.getpid()}-{part_token}"
        )
        self.cmd = cmd
        self.file_path = file_path
        self.source_fingerprint = source_fingerprint(file_path)
        self.label = label
        self.heavy = heavy
        self.spawn_process = spawn_process
        self.register_process = register_process
        self.unregister_process = unregister_process
        self.heavy_semaphore = heavy_semaphore
        self.condition = threading.Condition()
        self.available = 0
        self.finished = False
        self.ready = False
        self.failed = False
        self.cancelled = False
        self.cancel_reason: str | None = None
        self.process: Any | None = None
        self.consumers = 0
        self.bytes_exposed = False
        self.started_at = time.monotonic()
        self.last_progress_at = self.started_at
        # Starting the producer precedes ASGI iterating the response body by a
        # small amount.  Treat it as provisionally orphaned until a progressive
        # reader or Range waiter attaches; otherwise a request disconnected
        # before body iteration could leave a full transcode running forever.
        self.no_consumers_since: float | None = time.monotonic()

    def attach(self) -> None:
        with self.condition:
            self.consumers += 1
            self.no_consumers_since = None

    def detach(self) -> None:
        with self.condition:
            self.consumers = max(0, self.consumers - 1)
            if self.consumers == 0 and not self.finished:
                self.no_consumers_since = time.monotonic()
            self.condition.notify_all()

    def abandoned(self) -> bool:
        with self.condition:
            return (
                self.no_consumers_since is not None
                and time.monotonic() - self.no_consumers_since
                >= PLAYBACK_CACHE_RECONNECT_GRACE_SECONDS
            )

    def is_cancelled(self) -> bool:
        with self.condition:
            return self.cancelled

    def set_process(self, process: Any) -> None:
        with self.condition:
            self.process = process
            self.last_progress_at = time.monotonic()
            self.condition.notify_all()

    def note_progress(self) -> None:
        with self.condition:
            self.last_progress_at = time.monotonic()

    def cancel(self, reason: str) -> None:
        process = None
        with self.condition:
            if self.finished or self.cancelled:
                return
            self.cancelled = True
            self.cancel_reason = reason
            process = self.process
            self.condition.notify_all()
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass


def _write_all(file_obj, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = file_obj.write(view)
        if not written:
            raise OSError("playback cache write made no progress")
        view = view[written:]


def _finish_process(process: Any, natural_eof: bool) -> None:
    if not natural_eof:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.stdout.close()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=5)
        except Exception:
            pass


def monitor_playback_producer(entry: PlaybackProducer) -> None:
    """Cancel an abandoned, stalled, or overlong FFmpeg producer."""
    while True:
        with entry.condition:
            if entry.finished:
                return
            now = time.monotonic()
            process = entry.process
            no_consumers_since = entry.no_consumers_since
            bytes_exposed = entry.bytes_exposed
            last_progress_at = entry.last_progress_at
            started_at = entry.started_at

        if now - started_at >= PLAYBACK_PRODUCER_MAX_RUNTIME_SECONDS:
            entry.cancel("producer runtime limit exceeded")
        elif (
            process is not None
            and now - last_progress_at >= PLAYBACK_PRODUCER_IDLE_TIMEOUT_SECONDS
        ):
            entry.cancel("FFmpeg produced no playback bytes before the idle timeout")
        elif (
            not bytes_exposed
            and no_consumers_since is not None
            and now - no_consumers_since >= PLAYBACK_CACHE_RECONNECT_GRACE_SECONDS
        ):
            entry.cancel("all playback consumers disconnected")

        with entry.condition:
            if entry.finished:
                return
            entry.condition.wait(timeout=PLAYBACK_PRODUCER_HEARTBEAT_SECONDS)


def run_playback_producer(entry: PlaybackProducer) -> None:
    """Produce one byte-stable MP4 independently of any one HTTP connection."""
    acquired = False
    process = None
    cache_file = None
    sent = 0
    ttfb = None
    natural_eof = False
    registered = False
    start = time.monotonic()
    name = os.path.basename(entry.file_path)
    try:
        if entry.heavy:
            while not entry.is_cancelled():
                if entry.heavy_semaphore.acquire(timeout=1):
                    acquired = True
                    break
        if entry.is_cancelled() or entry.abandoned():
            return

        # Unbuffered writes make each advertised byte immediately readable by
        # the progressive response tailing this same append-only file.
        cache_file = open(entry.part_path, "xb", buffering=0)
        process = entry.spawn_process(entry.cmd)
        entry.set_process(process)
        entry.register_process(entry.file_path, process)
        registered = True
        read_chunk = getattr(process.stdout, "read1", None) or process.stdout.read
        while True:
            if entry.is_cancelled():
                break
            chunk = read_chunk(STREAM_READ_SIZE)
            if not chunk:
                natural_eof = True
                break
            if ttfb is None:
                ttfb = time.monotonic() - start
            _write_all(cache_file, chunk)
            sent += len(chunk)
            entry.note_progress()
            with entry.condition:
                entry.available = sent
                entry.condition.notify_all()
    except Exception:
        logger.exception("Playback producer failed [%s] %s", entry.label, name)
    finally:
        if process is not None:
            if registered:
                try:
                    entry.unregister_process(entry.file_path, process)
                except Exception:
                    logger.exception("Could not unregister playback producer %s", name)
            _finish_process(process, natural_eof)
        if cache_file is not None:
            try:
                cache_file.close()
            except OSError:
                pass

        elapsed = time.monotonic() - start
        returncode = process.returncode if process is not None else None
        mbps = (sent * 8 / 1_000_000 / elapsed) if elapsed > 0.01 else 0.0
        logger.info(
            "Stream end [%s] %s: %.1f MB in %.1fs (%.1f Mbps), ttfb=%.2fs, ffmpeg_rc=%s",
            entry.label,
            name,
            sent / 1_000_000,
            elapsed,
            mbps,
            (ttfb if ttfb is not None else -1.0),
            returncode,
        )
        if natural_eof and returncode not in (0, None):
            tail = getattr(process, "_stderr_tail", None)
            if tail:
                logger.error(
                    "FFmpeg [%s] %s exited rc=%s; stderr tail:\n%s",
                    entry.label,
                    name,
                    returncode,
                    "\n".join(tail),
                )

        try:
            source_unchanged = source_fingerprint(entry.file_path) == entry.source_fingerprint
        except OSError:
            source_unchanged = False
        success = (
            natural_eof
            and returncode == 0
            and source_unchanged
            and not entry.is_cancelled()
        )

        # Readers open, copy and close chunks while holding this condition, so
        # Windows can atomically rename without an open-file race.
        with entry.condition:
            if success:
                try:
                    # The lock owner rechecked this path before spawning.  Do
                    # not overwrite an unexpected completed representation:
                    # that would let a late/uncoordinated writer change bytes
                    # underneath a client that already received them.
                    if entry.cache_path.exists():
                        logger.error(
                            "Refusing to overwrite playback cache %s",
                            entry.cache_path,
                        )
                    else:
                        os.replace(entry.part_path, entry.cache_path)
                        entry.ready = True
                        logger.info(
                            "Playback cache stored [%s] %s: %.1f MB",
                            entry.label,
                            name,
                            sent / 1_000_000,
                        )
                except OSError:
                    logger.warning(
                        "Could not publish playback cache %s",
                        entry.cache_path,
                        exc_info=True,
                    )
            if not entry.ready:
                entry.failed = True
                _remove_cache_file(entry.part_path)
            entry.process = None
            entry.finished = True
            entry.condition.notify_all()

        if acquired:
            entry.heavy_semaphore.release()
        with _producers_lock:
            if _producers.get(entry.cache_path) is entry:
                del _producers[entry.cache_path]
        _release_producer_lock_fd(entry.lock_fd)
        entry.lock_fd = None


def get_or_start_playback_producer(
    cache_path: Path,
    cmd: list,
    file_path: str,
    label: str,
    heavy: bool,
    spawn_process: SpawnProcess,
    register_process: ProcessHook,
    unregister_process: ProcessHook,
    heavy_semaphore: threading.BoundedSemaphore,
    *,
    start_if_missing: bool = True,
) -> PlaybackProducer | ExternalPlaybackProducer | None:
    created = False
    with _producers_lock:
        if cache_path.is_file():
            return None
        entry = _producers.get(cache_path)
        if entry is not None and not entry.finished:
            return entry

        lock_fd = _try_acquire_producer_lock(cache_path)
        if lock_fd is None:
            return ExternalPlaybackProducer(
                cache_path=cache_path,
                lock_path=playback_producer_lock_path(cache_path),
            )
        # The previous owner may have published after our first file check but
        # before releasing the kernel lock.
        if cache_path.is_file():
            _release_producer_lock_fd(lock_fd)
            return None
        if not start_if_missing:
            _release_producer_lock_fd(lock_fd)
            return None

        # Holding the per-key lock proves no other worker can still be using a
        # part for this representation, so leftovers from a crashed owner are
        # now safe to remove.
        for part_path in cache_path.parent.glob(f"{cache_path.name}.part-*"):
            _remove_cache_file(part_path)
        try:
            entry = PlaybackProducer(
                cache_path,
                cmd,
                file_path,
                label,
                heavy,
                spawn_process,
                register_process,
                unregister_process,
                heavy_semaphore,
            )
            entry.lock_fd = lock_fd
            _producers[cache_path] = entry
            created = True
        except Exception:
            _release_producer_lock_fd(lock_fd)
            raise
    if created:
        try:
            producer_thread = threading.Thread(
                target=run_playback_producer,
                args=(entry,),
                name=f"playback-{cache_path.stem[:12]}",
                daemon=True,
            )
            producer_thread.start()
        except Exception:
            with _producers_lock:
                if _producers.get(cache_path) is entry:
                    del _producers[cache_path]
            with entry.condition:
                entry.failed = True
                entry.finished = True
                entry.condition.notify_all()
            _release_producer_lock_fd(entry.lock_fd)
            entry.lock_fd = None
            raise
        try:
            monitor_thread = threading.Thread(
                target=monitor_playback_producer,
                args=(entry,),
                name=f"playback-watch-{cache_path.stem[:12]}",
                daemon=True,
            )
            monitor_thread.start()
        except Exception:
            # The producer is already running and owns the kernel lock.  Its
            # normal EOF/failure cleanup remains safe even without a watchdog.
            logger.exception("Could not start playback producer watchdog")
    return entry


def tail_playback_producer(entry: PlaybackProducer):
    """Yield the growing representation from byte zero for the first request."""
    offset = 0
    entry.attach()
    try:
        while True:
            data = b""
            finished = False
            with entry.condition:
                while entry.available <= offset and not entry.finished:
                    entry.condition.wait(timeout=0.5)
                target = entry.available
                finished = entry.finished
                if target > offset:
                    path = entry.cache_path if entry.ready else entry.part_path
                    try:
                        with open(path, "rb") as source:
                            source.seek(offset)
                            data = source.read(min(STREAM_READ_SIZE, target - offset))
                    except OSError:
                        data = b""
            if data:
                offset += len(data)
                with entry.condition:
                    entry.bytes_exposed = True
                yield data
                continue
            if finished:
                break
    finally:
        entry.detach()


async def _wait_for_playback_cache(
    entry: PlaybackProducer | ExternalPlaybackProducer,
) -> bool:
    deadline = time.monotonic() + PLAYBACK_CACHE_RANGE_WAIT_SECONDS
    if isinstance(entry, ExternalPlaybackProducer):
        while time.monotonic() < deadline:
            if entry.cache_path.is_file():
                return True
            if not _external_producer_is_running(entry.cache_path):
                return entry.cache_path.is_file()
            await asyncio.sleep(0.1)
        return False

    entry.attach()
    try:
        while time.monotonic() < deadline:
            with entry.condition:
                if entry.ready:
                    return True
                if entry.failed:
                    return False
            await asyncio.sleep(0.1)
        return False
    finally:
        entry.detach()


async def serve_transformed_media(
    request: Request,
    *,
    cmd: list,
    file_path: str,
    label: str,
    heavy: bool,
    cache_root: Path | str,
    spawn_process: SpawnProcess,
    register_process: ProcessHook,
    unregister_process: ProcessHook,
    heavy_semaphore: threading.BoundedSemaphore,
) -> Response:
    """Serve a progressive first response and immutable ranged retries."""
    range_spec = parse_byte_range(request.headers.get("range"))
    cache_path = playback_cache_path(cmd, file_path, label, cache_root)
    prune_playback_cache(cache_path.parent, preserve=cache_path)
    if cache_path.is_file():
        logger.info("Playback cache hit [%s] %s", label, os.path.basename(file_path))
        return cached_playback_response(cache_path, request, range_spec)

    # Reject bad syntax before it can spend CPU on FFmpeg.  A valid multi-range
    # is intentionally unsupported; one contiguous byte interval is all media
    # playback requires and avoids version-dependent multipart behavior.
    if range_spec.kind == "malformed":
        return _range_error_response(400)
    if range_spec.kind in ("multiple", "unsatisfiable"):
        return _range_error_response(416)

    # A cold nonzero/suffix retry cannot safely create generation two: its byte
    # offset came from a representation that may not have been produced by this
    # worker.  It may join an existing local/external owner, but otherwise must
    # ask the client to retry or deliberately reload from byte zero.
    starts_at_zero = (
        range_spec.kind == "single"
        and range_spec.start == 0
        and range_spec.suffix_length is None
    )
    may_start_producer = range_spec.progressive_initial or starts_at_zero

    entry = get_or_start_playback_producer(
        cache_path,
        cmd,
        file_path,
        label,
        heavy,
        spawn_process,
        register_process,
        unregister_process,
        heavy_semaphore,
        start_if_missing=may_start_producer,
    )
    if entry is None:
        if cache_path.is_file():
            return cached_playback_response(cache_path, request, range_spec)
        return Response(
            content="No stable transformed representation exists for this byte range",
            status_code=503,
            media_type="text/plain",
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )

    # Only the local owner can expose a growing append-only representation, and
    # only for an absent/unsupported Range or exact bytes=0-.  Followers never
    # read another worker's partial file.
    if not range_spec.progressive_initial or isinstance(entry, ExternalPlaybackProducer):
        if await _wait_for_playback_cache(entry) and cache_path.is_file():
            return cached_playback_response(cache_path, request, range_spec)
        return Response(
            content="Transformed media is not ready; retry the same byte range",
            status_code=503,
            media_type="text/plain",
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )

    return StreamingResponse(
        tail_playback_producer(entry),
        media_type="video/mp4",
        headers={
            "Content-Type": "video/mp4",
            "Cache-Control": "no-cache",
        },
    )
