import threading
import time

from app.services import ytdlp_updater


def test_status_never_waits_for_remote_release_check(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    monkeypatch.setattr(ytdlp_updater, "_latest_version", None)
    monkeypatch.setattr(ytdlp_updater, "_last_check_monotonic", 0.0)
    monkeypatch.setattr(ytdlp_updater, "_check_in_progress", False)
    monkeypatch.setattr(ytdlp_updater, "get_installed_version", lambda: "2026.01.01")
    monkeypatch.setattr(ytdlp_updater, "is_managed", lambda: True)
    monkeypatch.setattr(ytdlp_updater, "resolved_path", lambda: "yt-dlp")
    monkeypatch.setattr(ytdlp_updater, "managed_ytdlp_path", lambda: "yt-dlp")

    def slow_remote_check():
        started.set()
        release.wait(timeout=2)
        ytdlp_updater._latest_version = "2026.02.02"
        ytdlp_updater._last_check_monotonic = time.monotonic()
        return ytdlp_updater._latest_version

    monkeypatch.setattr(ytdlp_updater, "get_latest_version", slow_remote_check)
    before = time.monotonic()
    status = ytdlp_updater.get_status()
    elapsed = time.monotonic() - before

    assert elapsed < 0.2
    assert status["installed_version"] == "2026.01.01"
    assert status["latest_version"] is None
    assert started.wait(timeout=1)

    release.set()
    deadline = time.monotonic() + 1
    while ytdlp_updater._check_in_progress and time.monotonic() < deadline:
        time.sleep(0.01)
    refreshed = ytdlp_updater.get_status()
    assert refreshed["latest_version"] == "2026.02.02"
    assert refreshed["update_available"] is True
