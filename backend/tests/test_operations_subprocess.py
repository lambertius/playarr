import subprocess
from types import SimpleNamespace

from app.routers import operations


def test_tool_version_forwards_hidden_window_flags(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout="ffmpeg version 8.0\n",
            stderr="",
        )

    hidden_window_flags = {"creationflags": 0x08000000}
    monkeypatch.setattr(operations, "HIDE_WINDOW", hidden_window_flags)
    monkeypatch.setattr(operations.subprocess, "run", fake_run)

    result = operations._tool_version(lambda: "ffmpeg", "-version")

    assert captured["command"] == ["ffmpeg", "-version"]
    assert captured["kwargs"]["creationflags"] == hidden_window_flags["creationflags"]
    assert result == {"available": True, "version": "ffmpeg version 8.0"}


def test_startup_ffmpeg_cleanup_forwards_hidden_window_flags(monkeypatch):
    from app import main

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=128, stdout="", stderr="")

    hidden_window_flags = {"creationflags": 0x08000000}
    monkeypatch.setattr(main, "HIDE_WINDOW", hidden_window_flags)
    monkeypatch.setattr(subprocess, "run", fake_run)

    main._kill_zombie_ffmpeg()

    assert captured["command"] == ["taskkill", "/F", "/IM", "ffmpeg.exe"]
    assert captured["kwargs"]["creationflags"] == hidden_window_flags["creationflags"]
