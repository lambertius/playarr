"""BASE-003 fixture-library contract tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from app.services.sidecar_store import SidecarValidationError, validate_playarr_sidecar


FIXTURES = Path(__file__).parent / "fixtures" / "media"
MANIFEST = json.loads((FIXTURES / "fixture_manifest.json").read_text(encoding="utf-8"))


def _ffprobe() -> str:
    bundled = Path(__file__).parents[2] / "tools" / "ffprobe.exe"
    executable = str(bundled) if bundled.is_file() else shutil.which("ffprobe")
    assert executable, "ffprobe is required by the BASE-003 verification gate"
    return executable


def _probe(name: str) -> dict:
    result = subprocess.run(
        [_ffprobe(), "-v", "error", "-show_streams", "-of", "json", str(FIXTURES / name)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_fixture_manifest_files_exist_and_are_small():
    for name in MANIFEST["fixtures"]:
        path = FIXTURES / name
        assert path.is_file(), name
        assert 0 < path.stat().st_size < 250_000, name


def test_probe_contracts_cover_sdr_hdr_aspect_and_vfr():
    sdr = _probe("sdr_16x9.mp4")["streams"]
    sdr_video = next(stream for stream in sdr if stream["codec_type"] == "video")
    assert (sdr_video["width"], sdr_video["height"], sdr_video["pix_fmt"]) == (320, 180, "yuv420p")
    assert any(stream["codec_type"] == "audio" for stream in sdr)

    hdr = next(stream for stream in _probe("hdr10_bt2020.mkv")["streams"] if stream["codec_type"] == "video")
    assert hdr["pix_fmt"] == "yuv420p10le"
    assert hdr["color_primaries"] == "bt2020"
    assert hdr["color_transfer"] == "smpte2084"

    four_three = next(stream for stream in _probe("four_three.mp4")["streams"] if stream["codec_type"] == "video")
    assert (four_three["width"], four_three["height"]) == (320, 240)

    vfr = next(stream for stream in _probe("variable_frame_rate.mp4")["streams"] if stream["codec_type"] == "video")
    assert vfr["r_frame_rate"] != vfr["avg_frame_rate"]


def test_duplicate_and_sidecar_edge_cases_are_reproducible():
    digest = lambda name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
    assert digest("duplicate_exact_copy.mp4") == digest("sdr_16x9.mp4")
    validate_playarr_sidecar(FIXTURES / "live_version.playarr.xml")
    validate_playarr_sidecar(FIXTURES / "cover_version.playarr.xml")
    with pytest.raises(SidecarValidationError):
        validate_playarr_sidecar(FIXTURES / "malformed.playarr.xml")
