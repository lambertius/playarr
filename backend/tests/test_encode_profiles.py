from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.video_editor as video_editor
from app.services.video_editor import encode_video, resolve_encode_plan, validate_encoded_output


def _probe(*, pix_fmt="yuv420p", transfer="bt709", audio_codec="aac", video_codec=None, video_bitrate=20_000_000):
    return {
        "format": {"duration": "120.0", "format_name": "matroska"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": video_codec or ("hevc" if "10" in pix_fmt else "h264"),
                "bit_rate": str(video_bitrate),
                "width": 3840,
                "height": 2160,
                "pix_fmt": pix_fmt,
                "bits_per_raw_sample": "10" if "10" in pix_fmt else "8",
                "avg_frame_rate": "24000/1001",
                "r_frame_rate": "24000/1001",
                "time_base": "1/24000",
                "sample_aspect_ratio": "1:1",
                "color_primaries": "bt2020" if transfer == "smpte2084" else "bt709",
                "color_transfer": transfer,
                "color_space": "bt2020nc" if transfer == "smpte2084" else "bt709",
            },
            {
                "codec_type": "audio",
                "codec_name": audio_codec,
                "sample_rate": "48000",
                "channels": 6,
                "channel_layout": "5.1",
            },
        ],
    }


def test_source_fidelity_preserves_hdr_depth_with_source_bitrate_envelope():
    plan = resolve_encode_plan(
        "concert.mkv",
        profile="source_fidelity",
        probe=_probe(pix_fmt="yuv420p10le", transfer="smpte2084"),
    )

    assert plan["errors"] == []
    assert plan["source"]["hdr"] is True
    assert plan["output"]["video_encoder"] == "libx265"
    assert plan["output"]["pixel_format"] == "yuv420p10le"
    assert plan["source"]["video_bitrate"] == 20_000_000
    assert plan["output"]["target_video_bitrate"] == 20_000_000
    assert plan["output"]["maxrate"] == 40_000_000
    assert plan["output"]["bufsize"] == 80_000_000
    assert plan["output"]["bitrate_policy"] == "source_referenced_constrained_quality"
    assert plan["output"]["crf"] == 14
    assert plan["output"]["preset"] == "slow"
    assert plan["output"]["width"] == 3840
    assert plan["output"]["height"] == 2160
    assert plan["output"]["frame_timing"] == "source_timestamps"
    assert plan["output"]["encoder_time_base"] == "demux"
    assert plan["output"]["metadata"] == "copy"
    assert plan["output"]["chapters"] == "copy"
    assert plan["output"]["audio_codec"] == "copy"


def test_balanced_blocks_hdr_instead_of_silently_flattening_it():
    plan = resolve_encode_plan(
        "concert.mkv",
        profile="balanced",
        probe=_probe(pix_fmt="yuv420p10le", transfer="smpte2084"),
    )

    assert plan["errors"]
    assert "discard HDR/bit-depth" in plan["errors"][0]


def test_bitrate_reference_accounts_for_codec_efficiency_and_resolution_floor():
    efficient_source = resolve_encode_plan(
        "concert.mkv",
        profile="source_fidelity",
        probe=_probe(video_codec="hevc", video_bitrate=8_000_000),
    )
    assert efficient_source["output"]["video_encoder"] == "libx264"
    assert efficient_source["output"]["adjusted_source_bitrate"] == 12_000_000
    assert efficient_source["output"]["target_video_bitrate"] == efficient_source["output"]["resolution_bitrate_floor"]
    assert efficient_source["output"]["target_video_bitrate"] > efficient_source["output"]["adjusted_source_bitrate"]

    low_rate_4k = resolve_encode_plan(
        "concert.mkv",
        profile="source_fidelity",
        probe=_probe(video_bitrate=1_000_000),
    )
    assert low_rate_4k["output"]["target_video_bitrate"] > 19_000_000
    assert low_rate_4k["output"]["resolution_bitrate_floor"] == low_rate_4k["output"]["target_video_bitrate"]


def test_bitrate_reference_falls_back_to_container_rate_or_quality_only():
    format_rate_probe = _probe(video_bitrate=None)
    format_rate_probe["format"]["bit_rate"] = "10000000"
    from_format = resolve_encode_plan("concert.mkv", probe=format_rate_probe)
    assert from_format["source"]["video_bitrate"] == 10_000_000
    assert from_format["output"]["maxrate"]

    unavailable_probe = _probe(video_bitrate=None)
    unavailable = resolve_encode_plan("concert.mkv", probe=unavailable_probe)
    assert unavailable["source"]["video_bitrate"] is None
    assert unavailable["output"]["maxrate"] is None
    assert unavailable["output"]["bitrate_policy"] == "quality_only_source_bitrate_unavailable"


def test_lossless_trim_defaults_to_lossless_audio_and_preserves_layout():
    plan = resolve_encode_plan(
        "concert.mkv",
        profile="source_fidelity",
        trim_start=2.5,
        audio_passthrough=True,
        probe=_probe(audio_codec="flac"),
    )

    assert plan["output"]["audio_codec"] == "alac"
    assert plan["output"]["audio_bitrate"] is None
    assert plan["output"]["audio_sample_rate"] == "preserve"
    assert plan["output"]["audio_channels"] == "preserve"
    assert any("losslessly" in warning for warning in plan["warnings"])


def test_source_fidelity_never_adds_a_lossy_audio_generation():
    plan = resolve_encode_plan(
        "concert.mp4",
        profile="source_fidelity",
        trim_start=2.5,
        probe=_probe(audio_codec="aac"),
    )

    assert plan["output"]["audio_codec"] == "alac"
    assert plan["output"]["audio_bitrate"] is None


def test_explicit_audio_codec_override_is_still_honoured():
    plan = resolve_encode_plan(
        "concert.mp4",
        profile="source_fidelity",
        audio_passthrough=False,
        audio_codec="aac",
        audio_bitrate="320k",
        probe=_probe(audio_codec="aac"),
    )

    assert plan["output"]["audio_codec"] == "aac"
    assert plan["output"]["audio_bitrate"] == "320k"


def test_encode_command_preserves_timestamps_rate_layout_and_sample_rate(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    source.write_bytes(b"source")
    captured: list[str] = []

    monkeypatch.setattr(video_editor, "probe_file", lambda _path: _probe(audio_codec="aac"))
    monkeypatch.setattr(video_editor, "get_settings", lambda: SimpleNamespace(resolved_ffmpeg="ffmpeg"))

    class _Stdout:
        def readline(self):
            return ""

    class _Process:
        def __init__(self, cmd, **_kwargs):
            captured.extend(cmd)
            Path(cmd[-1]).write_bytes(b"encoded")
            self.stdout = _Stdout()

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(video_editor.subprocess, "Popen", _Process)

    stats = encode_video(str(source), str(output), trim_start=1.0)

    assert stats["encode_plan"]["output"]["audio_codec"] == "alac"
    assert captured[captured.index("-fps_mode:v") + 1] == "passthrough"
    assert captured[captured.index("-enc_time_base:v") + 1] == "demux"
    assert "-r" not in captured
    assert captured[captured.index("-maxrate:v") + 1] == "40000000"
    assert captured[captured.index("-bufsize:v") + 1] == "80000000"
    assert captured[captured.index("-c:a") + 1] == "alac"
    assert captured[captured.index("-ar:a:0") + 1] == "48000"
    assert captured[captured.index("-channel_layout:a:0") + 1] == "5.1"


def test_validation_rejects_scaling_or_frame_rate_conversion(monkeypatch):
    source_probe = _probe()
    output_probe = _probe()
    output_video = output_probe["streams"][0]
    plan = resolve_encode_plan("source.mp4", probe=source_probe)

    output_video["width"] = 1920
    output_video["height"] = 1080
    probes = iter([source_probe, output_probe])
    monkeypatch.setattr(video_editor, "probe_file", lambda _path: next(probes))
    monkeypatch.setattr(video_editor, "get_settings", lambda: SimpleNamespace(resolved_ffmpeg="ffmpeg"))
    with pytest.raises(RuntimeError, match="crop without scaling"):
        validate_encoded_output("source.mp4", "output.mp4", plan)

    output_probe = _probe()
    output_probe["streams"][0]["avg_frame_rate"] = "25/1"
    probes = iter([source_probe, output_probe])
    monkeypatch.setattr(video_editor, "probe_file", lambda _path: next(probes))
    with pytest.raises(RuntimeError, match="frame rate changed"):
        validate_encoded_output("source.mp4", "output.mp4", plan)
