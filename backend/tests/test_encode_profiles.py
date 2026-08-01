from app.services.video_editor import resolve_encode_plan


def _probe(*, pix_fmt="yuv420p", transfer="bt709", audio_codec="aac"):
    return {
        "format": {"duration": "120.0", "format_name": "matroska"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc" if "10" in pix_fmt else "h264",
                "width": 3840,
                "height": 2160,
                "pix_fmt": pix_fmt,
                "bits_per_raw_sample": "10" if "10" in pix_fmt else "8",
                "avg_frame_rate": "24000/1001",
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


def test_source_fidelity_preserves_hdr_depth_without_bitrate_ceiling():
    plan = resolve_encode_plan(
        "concert.mkv",
        profile="source_fidelity",
        probe=_probe(pix_fmt="yuv420p10le", transfer="smpte2084"),
    )

    assert plan["errors"] == []
    assert plan["source"]["hdr"] is True
    assert plan["output"]["video_encoder"] == "libx265"
    assert plan["output"]["pixel_format"] == "yuv420p10le"
    assert plan["output"]["maxrate"] is None
    assert plan["output"]["frame_timing"] == "passthrough"
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
