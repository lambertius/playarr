"""Regression coverage for resumable FFmpeg-backed playback streams."""
import asyncio
import io
import os
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.routers import playback
from app.services import playback_stream_cache


class _FakeStdout:
    def __init__(self, payload: bytes):
        self._buffer = io.BytesIO(payload)
        self.read_sizes: list[int] = []

    def read1(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self._buffer.read(size)

    def read(self, size: int) -> bytes:  # pragma: no cover - read1 is preferred
        raise AssertionError(f"blocking read({size}) should not be used")

    def close(self) -> None:
        self._buffer.close()


class _FakeProcess:
    def __init__(self, payload: bytes = b"", stdout=None):
        self.stdout = stdout if stdout is not None else _FakeStdout(payload)
        self.returncode = None
        self._stderr_tail = []
        self.killed = False

    def wait(self, timeout: int | None = None) -> int:
        self.returncode = -9 if self.killed else 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_transformed_stream_retries_use_the_same_cached_byte_range(tmp_path, monkeypatch):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source identity")
    cache_root = tmp_path / "cache"
    payload = bytes(range(256)) * 4096
    processes: list[_FakeProcess] = []

    def spawn(_cmd):
        process = _FakeProcess(payload)
        processes.append(process)
        return process

    monkeypatch.setattr(playback, "get_runtime_dirs", lambda: SimpleNamespace(cache_dir=cache_root))
    monkeypatch.setattr(playback, "_spawn_ffmpeg", spawn)

    command = ["ffmpeg", "-i", str(source), "-f", "mp4", "pipe:1"]
    app = FastAPI()

    @app.get("/media")
    async def media(request: Request):
        return await playback._streaming_response(request, command, str(source), "compat")

    with TestClient(app) as client:
        first = client.get("/media")
        assert first.status_code == 200
        assert first.content == payload
        assert "accept-ranges" not in first.headers
        assert len(processes) == 1
        assert processes[0].stdout.read_sizes
        assert max(processes[0].stdout.read_sizes) == playback_stream_cache.STREAM_READ_SIZE

        retry = client.get("/media", headers={"Range": "bytes=700000-700099"})
        assert retry.status_code == 206
        assert retry.content == payload[700000:700100]
        assert retry.headers["content-range"] == f"bytes 700000-700099/{len(payload)}"
        assert len(processes) == 1, "a Range retry must not launch a timestamp-zero transcode"


def test_transformed_stream_cache_is_invalidated_when_source_changes(tmp_path, monkeypatch):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"first")
    cache_root = tmp_path / "cache"
    payloads = [b"first representation", b"second representation"]
    processes: list[_FakeProcess] = []

    def spawn(_cmd):
        process = _FakeProcess(payloads[len(processes)])
        processes.append(process)
        return process

    monkeypatch.setattr(playback, "get_runtime_dirs", lambda: SimpleNamespace(cache_dir=cache_root))
    monkeypatch.setattr(playback, "_spawn_ffmpeg", spawn)

    command = ["ffmpeg", "-i", str(source), "-f", "mp4", "pipe:1"]
    app = FastAPI()

    @app.get("/media")
    async def media(request: Request):
        return await playback._streaming_response(request, command, str(source), "compat")

    with TestClient(app) as client:
        assert client.get("/media").content == payloads[0]
        source.write_bytes(b"changed source size")
        assert client.get("/media").content == payloads[1]

    assert len(processes) == 2


def test_range_retry_joins_an_in_progress_producer_after_disconnect(tmp_path, monkeypatch):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source identity")
    cache_root = tmp_path / "cache"
    release = threading.Event()
    first_emitted = threading.Event()
    first_chunk = b"first fragment"
    second_chunk = b"second fragment"
    processes: list[_FakeProcess] = []

    class BlockingStdout:
        def __init__(self):
            self.calls = 0

        def read1(self, _size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                first_emitted.set()
                return first_chunk
            if self.calls == 2:
                assert release.wait(timeout=5)
                return second_chunk
            return b""

        def read(self, _size: int) -> bytes:  # pragma: no cover - read1 is preferred
            raise AssertionError("blocking read should not be used")

        def close(self) -> None:
            release.set()

    def spawn(_cmd):
        process = _FakeProcess(stdout=BlockingStdout())
        processes.append(process)
        return process

    monkeypatch.setattr(playback, "get_runtime_dirs", lambda: SimpleNamespace(cache_dir=cache_root))
    monkeypatch.setattr(playback, "_spawn_ffmpeg", spawn)

    command = ["ffmpeg", "-i", str(source), "-f", "mp4", "pipe:1"]
    cache_path = playback_stream_cache.playback_cache_path(
        command, str(source), "compat", cache_root
    )
    entry = playback_stream_cache.get_or_start_playback_producer(
        cache_path,
        command,
        str(source),
        "compat",
        False,
        spawn,
        lambda _path, _process: None,
        lambda _path, _process: None,
        threading.BoundedSemaphore(1),
    )
    assert isinstance(entry, playback_stream_cache.PlaybackProducer)

    # Consume one progressive fragment, then emulate the TV closing that HTTP
    # response while immediately asking for a later byte range.
    tail = playback_stream_cache.tail_playback_producer(entry)
    assert next(tail) == first_chunk
    assert first_emitted.is_set()
    tail.close()

    async def reconnect():
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/media",
                "headers": [(b"range", b"bytes=4-")],
            }
        )
        task = asyncio.create_task(
            playback._streaming_response(request, command, str(source), "compat")
        )
        await asyncio.sleep(0.05)
        assert not task.done(), "the Range retry should wait for generation one"
        assert len(processes) == 1
        release.set()
        response = await task
        body = b"".join([chunk async for chunk in response.body_iterator])
        return response, body

    response, body = asyncio.run(reconnect())
    assert response.status_code == 206
    assert response.headers["content-range"] == (
        f"bytes 4-{len(first_chunk + second_chunk) - 1}/"
        f"{len(first_chunk + second_chunk)}"
    )
    assert body == (first_chunk + second_chunk)[4:]
    assert cache_path.read_bytes() == first_chunk + second_chunk
    assert len(processes) == 1, "a reconnect must join, never respawn from timestamp zero"


@pytest.mark.parametrize(
    ("header", "kind", "start", "end", "suffix"),
    [
        (None, "none", None, None, None),
        ("bytes=0-", "single", 0, None, None),
        ("bytes=0-99", "single", 0, 99, None),
        ("bytes=17-", "single", 17, None, None),
        ("bytes=-17", "single", None, None, 17),
        ("items=1-2", "unsupported", None, None, None),
        ("bytes=0-garbage", "malformed", None, None, None),
        ("bytes=0-1-2", "malformed", None, None, None),
        ("bytes=9-3", "malformed", None, None, None),
        ("bytes=0-1,4-5", "multiple", None, None, None),
        ("bytes=-0", "unsatisfiable", None, None, None),
    ],
)
def test_byte_range_parser_distinguishes_all_request_forms(
    header, kind, start, end, suffix
):
    parsed = playback_stream_cache.parse_byte_range(header)
    assert (parsed.kind, parsed.start, parsed.end, parsed.suffix_length) == (
        kind,
        start,
        end,
        suffix,
    )


def test_completed_cache_uses_version_independent_single_range_responses(tmp_path):
    payload = bytes(range(64))
    cache_path = tmp_path / "completed.mp4"
    cache_path.write_bytes(payload)
    app = FastAPI()

    @app.api_route("/cached", methods=["GET", "HEAD"])
    async def cached(request: Request):
        return playback_stream_cache.cached_playback_response(cache_path, request)

    with TestClient(app) as client:
        full = client.get("/cached")
        assert full.status_code == 200
        assert full.content == payload
        assert full.headers["accept-ranges"] == "bytes"
        assert full.headers["content-length"] == str(len(payload))

        bounded = client.get("/cached", headers={"Range": "bytes=5-9"})
        assert bounded.status_code == 206
        assert bounded.content == payload[5:10]
        assert bounded.headers["content-range"] == "bytes 5-9/64"
        assert bounded.headers["content-length"] == "5"

        suffix = client.get("/cached", headers={"Range": "bytes=-4"})
        assert suffix.status_code == 206
        assert suffix.content == payload[-4:]
        assert suffix.headers["content-range"] == "bytes 60-63/64"

        invalid = client.get("/cached", headers={"Range": "bytes=0-nope"})
        assert invalid.status_code == 400
        assert invalid.content == b""

        multiple = client.get(
            "/cached", headers={"Range": "bytes=0-1,4-5"}
        )
        assert multiple.status_code == 416
        assert multiple.headers["content-range"] == "bytes */64"
        assert multiple.content == b""

        beyond_end = client.get("/cached", headers={"Range": "bytes=64-"})
        assert beyond_end.status_code == 416
        assert beyond_end.headers["content-range"] == "bytes */64"

        unsupported = client.get("/cached", headers={"Range": "items=5-9"})
        assert unsupported.status_code == 200
        assert unsupported.content == payload

        stale_validator = client.get(
            "/cached",
            headers={"Range": "bytes=5-9", "If-Range": '"stale"'},
        )
        assert stale_validator.status_code == 412
        assert stale_validator.content == b"Cached representation changed; reload media"

        head = client.head("/cached", headers={"Range": "bytes=5-9"})
        assert head.status_code == 206
        assert head.content == b""
        assert head.headers["content-length"] == "5"


def test_cold_ranges_never_spawn_timestamp_zero_for_nonzero_or_invalid_requests(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source identity")
    cache_root = tmp_path / "cache"
    payload = b"0123456789abcdef"
    processes: list[_FakeProcess] = []

    def spawn(_cmd):
        process = _FakeProcess(payload)
        processes.append(process)
        return process

    monkeypatch.setattr(
        playback, "get_runtime_dirs", lambda: SimpleNamespace(cache_dir=cache_root)
    )
    monkeypatch.setattr(playback, "_spawn_ffmpeg", spawn)
    command = ["ffmpeg", "-i", str(source), "-f", "mp4", "pipe:1"]
    app = FastAPI()

    @app.get("/media")
    async def media(request: Request):
        return await playback._streaming_response(request, command, str(source), "compat")

    with TestClient(app) as client:
        malformed = client.get("/media", headers={"Range": "bytes=0-nope"})
        assert malformed.status_code == 400
        multiple = client.get(
            "/media", headers={"Range": "bytes=0-1,4-5"}
        )
        assert multiple.status_code == 416
        nonzero = client.get("/media", headers={"Range": "bytes=5-"})
        assert nonzero.status_code == 503
        suffix = client.get("/media", headers={"Range": "bytes=-4"})
        assert suffix.status_code == 503
        assert processes == []

        # A bounded zero range may create generation one, but it waits for the
        # final immutable bytes and returns exactly the requested 206 interval.
        bounded_zero = client.get("/media", headers={"Range": "bytes=0-3"})
        assert bounded_zero.status_code == 206
        assert bounded_zero.content == payload[:4]
        assert bounded_zero.headers["content-range"] == (
            f"bytes 0-3/{len(payload)}"
        )
        assert len(processes) == 1


def test_kernel_lock_makes_another_worker_a_final_file_follower(tmp_path):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source identity")
    cache_path = tmp_path / "playback" / "representation.mp4"
    cache_path.parent.mkdir()
    owner_fd = playback_stream_cache._try_acquire_producer_lock(cache_path)
    assert owner_fd is not None
    spawned = []
    try:
        entry = playback_stream_cache.get_or_start_playback_producer(
            cache_path,
            ["ffmpeg"],
            str(source),
            "compat",
            False,
            lambda _cmd: spawned.append(True),
            lambda _path, _process: None,
            lambda _path, _process: None,
            threading.BoundedSemaphore(1),
        )
        assert isinstance(entry, playback_stream_cache.ExternalPlaybackProducer)
        assert spawned == []
    finally:
        playback_stream_cache._release_producer_lock_fd(owner_fd)

    reacquired = playback_stream_cache._try_acquire_producer_lock(cache_path)
    assert reacquired is not None, "the OS must release ownership without unlinking"
    playback_stream_cache._release_producer_lock_fd(reacquired)
    assert playback_stream_cache.playback_producer_lock_path(cache_path).is_file()


def test_external_worker_waits_for_atomic_final_before_serving_range(tmp_path):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source identity")
    cache_root = tmp_path / "cache"
    command = ["ffmpeg", "-i", str(source), "-f", "mp4", "pipe:1"]
    cache_path = playback_stream_cache.playback_cache_path(
        command, str(source), "compat", cache_root
    )
    foreign_part = cache_path.with_name(f"{cache_path.name}.part-foreign")
    payload = b"0123456789abcdef"
    foreign_part.write_bytes(payload[:4])
    owner_fd = playback_stream_cache._try_acquire_producer_lock(cache_path)
    assert owner_fd is not None

    async def follow_owner():
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/media",
                "headers": [(b"range", b"bytes=5-9")],
            }
        )
        task = asyncio.create_task(
            playback_stream_cache.serve_transformed_media(
                request,
                cmd=command,
                file_path=str(source),
                label="compat",
                heavy=False,
                cache_root=cache_root,
                spawn_process=lambda _cmd: pytest.fail("a follower must not spawn"),
                register_process=lambda _path, _process: None,
                unregister_process=lambda _path, _process: None,
                heavy_semaphore=threading.BoundedSemaphore(1),
            )
        )
        await asyncio.sleep(0.03)
        assert not task.done(), "a foreign part must never be exposed to the follower"
        foreign_part.write_bytes(payload)
        os.replace(foreign_part, cache_path)
        response = await task
        body = b"".join([chunk async for chunk in response.body_iterator])
        return response, body

    try:
        response, body = asyncio.run(follow_owner())
    finally:
        playback_stream_cache._release_producer_lock_fd(owner_fd)
    assert response.status_code == 206
    assert body == payload[5:10]
    assert response.headers["content-range"] == f"bytes 5-9/{len(payload)}"


def test_disconnected_producer_is_cancelled_only_before_bytes_escape(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source identity")
    semaphore = threading.BoundedSemaphore(1)

    def make_entry(name: str):
        return playback_stream_cache.PlaybackProducer(
            tmp_path / f"{name}.mp4",
            ["ffmpeg"],
            str(source),
            "compat",
            False,
            lambda _cmd: None,
            lambda _path, _process: None,
            lambda _path, _process: None,
            semaphore,
        )

    monkeypatch.setattr(
        playback_stream_cache, "PLAYBACK_CACHE_RECONNECT_GRACE_SECONDS", 0
    )
    monkeypatch.setattr(
        playback_stream_cache, "PLAYBACK_PRODUCER_HEARTBEAT_SECONDS", 0.005
    )

    unobserved = make_entry("unobserved")
    first_monitor = threading.Thread(
        target=playback_stream_cache.monitor_playback_producer,
        args=(unobserved,),
        daemon=True,
    )
    first_monitor.start()
    deadline = time.monotonic() + 1
    while not unobserved.cancelled and time.monotonic() < deadline:
        time.sleep(0.005)
    assert unobserved.cancelled
    with unobserved.condition:
        unobserved.finished = True
        unobserved.condition.notify_all()
    first_monitor.join(timeout=1)

    exposed = make_entry("exposed")
    exposed.bytes_exposed = True
    second_monitor = threading.Thread(
        target=playback_stream_cache.monitor_playback_producer,
        args=(exposed,),
        daemon=True,
    )
    second_monitor.start()
    time.sleep(0.03)
    assert not exposed.cancelled
    with exposed.condition:
        exposed.finished = True
        exposed.condition.notify_all()
    second_monitor.join(timeout=1)
