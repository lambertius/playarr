"""
Unit tests for the multi-window letterbox detector's consensus logic.

These monkeypatch the per-window cropdetect (so no ffmpeg is required) and feed
synthetic per-window crop readings to verify the aggregation: median consensus,
symmetry snapping, dark-scene/outlier rejection, and thresholds.
"""
import app.services.video_editor as ve


def _patch(monkeypatch, dims, window_crops, duration=200.0):
    """Patch probe_file + _cropdetect_window so detect_letterbox runs offline.

    window_crops: list of (w, h, x, y) returned per window in order (cycled).
    """
    ow, oh = dims
    monkeypatch.setattr(ve, "probe_file", lambda p: {
        "streams": [{"codec_type": "video", "width": ow, "height": oh}],
        "format": {"duration": str(duration)},
    })
    state = {"i": 0}

    def fake_window(ffmpeg, path, start, dur, w, h):
        c = window_crops[state["i"] % len(window_crops)]
        state["i"] += 1
        return c

    monkeypatch.setattr(ve, "_cropdetect_window", fake_window)


def test_symmetric_letterbox_detected(monkeypatch):
    # 1920x1080 with 140px black bars top+bottom → 1920x800 content
    _patch(monkeypatch, (1920, 1080), [(1920, 800, 0, 140)])
    r = ve.detect_letterbox("x.mkv")
    assert r["detected"] is True
    assert r["bar_top"] == 140 and r["bar_bottom"] == 140
    assert r["bar_left"] == 0 and r["bar_right"] == 0
    assert r["crop_y"] == 140 and r["crop_h"] == 800 and r["crop_w"] == 1920


def test_no_letterbox(monkeypatch):
    _patch(monkeypatch, (1920, 1080), [(1920, 1080, 0, 0)])
    r = ve.detect_letterbox("x.mkv")
    assert r["detected"] is False
    assert r["crop_w"] == 1920 and r["crop_h"] == 1080


def test_pillarbox_detected(monkeypatch):
    # 4:3 content (1440 wide) inside 1920 → 240px bars left+right
    _patch(monkeypatch, (1920, 1080), [(1440, 1080, 240, 0)])
    r = ve.detect_letterbox("x.mkv")
    assert r["detected"] is True
    assert r["bar_left"] == 240 and r["bar_right"] == 240
    assert r["bar_top"] == 0 and r["bar_bottom"] == 0
    assert r["crop_x"] == 240 and r["crop_w"] == 1440


def test_noisy_but_real_letterbox(monkeypatch):
    # 5 windows see the real 140px letterbox, 1 window is a bright full-frame
    # scene. Median must still land on the true letterbox.
    lb = (1920, 800, 0, 140)
    full = (1920, 1080, 0, 0)
    _patch(monkeypatch, (1920, 1080), [lb, lb, lb, full, lb, lb])
    r = ve.detect_letterbox("x.mkv")
    assert r["detected"] is True
    assert r["bar_top"] == 140 and r["bar_bottom"] == 140


def test_dark_scene_false_positive_rejected(monkeypatch):
    # Mostly no-letterbox, one window is a dark scene that reads huge bars.
    # The median must reject the single outlier → no crop.
    full = (1920, 1080, 0, 0)
    dark = (1920, 300, 0, 390)  # bogus huge bars from a near-black scene
    _patch(monkeypatch, (1920, 1080), [full, full, full, dark, full, full])
    r = ve.detect_letterbox("x.mkv")
    assert r["detected"] is False


def test_asymmetric_reading_rejected(monkeypatch):
    # Bars disagree top(140) vs bottom(0) — not a real symmetric letterbox.
    # Should NOT produce a lopsided crop.
    _patch(monkeypatch, (1920, 1080), [(1920, 940, 0, 140)])
    r = ve.detect_letterbox("x.mkv")
    assert r["detected"] is False
    assert r["crop_h"] == 1080


def test_tiny_bars_below_threshold(monkeypatch):
    # 6px bars (< max(8px, 1.8%)) are compression noise, not letterboxing.
    _patch(monkeypatch, (1920, 1080), [(1920, 1068, 0, 6)])
    r = ve.detect_letterbox("x.mkv")
    assert r["detected"] is False


def test_parse_filters_dark_and_out_of_bounds():
    stderr = "\n".join([
        "[Parsed_cropdetect_0 @ x] crop=1920:800:0:140",   # valid letterbox
        "[Parsed_cropdetect_0 @ x] crop=100:100:0:0",       # near-black frame → filtered
        "[Parsed_cropdetect_0 @ x] crop=2000:800:0:140",    # w out of bounds → filtered
        "garbage line without a crop",
    ])
    crops = ve._parse_cropdetect_lines(stderr, 1920, 1080)
    assert crops == [(1920, 800, 0, 140)]


def test_even_alignment(monkeypatch):
    # Odd bar (141) must be even-aligned down to 140.
    _patch(monkeypatch, (1920, 1080), [(1920, 798, 0, 141)])
    r = ve.detect_letterbox("x.mkv")
    assert r["bar_top"] % 2 == 0 and r["bar_bottom"] % 2 == 0
    assert r["crop_h"] % 2 == 0 and r["crop_w"] % 2 == 0
