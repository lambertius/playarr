# Sanitized media fixtures (BASE-003)

These synthetic clips contain no third-party audio, video, artwork, names, API
responses, or local paths. They are intentionally tiny so clean CI runs can
exercise media contracts without downloading external samples.

| File | Contract represented |
| --- | --- |
| `sdr_16x9.mp4` | 8-bit SDR, 16:9, 25 fps, AAC stereo source |
| `hdr10_bt2020.mkv` | 10-bit BT.2020 / SMPTE ST 2084 source |
| `four_three.mp4` | Native 4:3 content |
| `letterboxed.mp4` | 16:9 content padded to 4:3; expected crop `320x180+0+30` |
| `pillarboxed.mp4` | 4:3 content padded to 16:9; expected crop `240x180+40+0` |
| `variable_frame_rate.mp4` | 15 fps first segment and 30 fps second segment |
| `live_version.mp4` | Logical `live` version sidecar fixture |
| `cover_version.mp4` | Logical `cover` version sidecar fixture |
| `duplicate_exact_copy.mp4` | Byte-identical duplicate of `sdr_16x9.mp4` |
| `malformed.playarr.xml` | Invalid/truncated sidecar recovery case |

`fixture_manifest.json` is authoritative for expected probe and crop values.
Binary fixtures were generated from FFmpeg `testsrc2` and `sine` filters.
