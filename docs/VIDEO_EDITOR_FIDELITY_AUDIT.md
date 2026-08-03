# Video Editor fidelity audit

The Video Editor now treats source fidelity as a verified output contract, not
just a label for a set of FFmpeg options.

## Default contract

| Property | Source-fidelity behaviour |
| --- | --- |
| Frame dimensions | Never scales. Output dimensions equal the source dimensions minus an intentional crop. |
| Frame timing | Passes source timestamps through and uses the demuxer time base. It never supplies an output `-r`. |
| Video quality | CRF 14 with the `slow` preset. Source bitrate, codec efficiency, output resolution, and frame rate establish a quality-safe bitrate reference with 2x peak headroom. The explicit user controls remain available. |
| HDR and bit depth | Retains HDR transfer metadata and chooses a high-depth HEVC path for 10/12-bit sources. |
| Pixel format and colour | Retains compatible chroma/depth and explicitly carries colour range, space, primaries, and transfer characteristics. |
| Audio without trim | Copies the original audio stream. |
| Audio requiring encode | Uses ALAC by default, preserving the decoded source without adding another lossy generation. Sample rate and channel layout are explicit. |
| Metadata | Copies global metadata, chapters, subtitles, and rotation handling. |

AAC, Opus, FLAC, and ALAC remain explicit overrides. Selecting a lossy override
is an informed user choice rather than an implicit consequence of trimming.

## Replacement safety gate

The staged output must pass all applicable checks before it can replace the
library file:

- expected cropped-or-original dimensions, with no scaling;
- average frame rate within 0.2% when the full timeline is retained;
- source audio channels, sample rate, and channel layout;
- source HDR transfer metadata and high bit depth;
- a complete decode with FFmpeg;
- SSIM of at least 0.98 for source-fidelity encodes whose geometry and timing
  were not intentionally changed.

The validation report records source, target-reference, and output video
bitrates. Bitrate alone is not a pass/fail quality metric because a slower or
more efficient codec can preserve the same image with fewer bits.

Trimmed variable-frame-rate media is not judged by whole-file average rate,
because selecting a segment can legitimately change that average. Its timing is
preserved by the timestamp/time-base encode contract and verified for decodability.

## Performance trade-off

The `slow` preset and CRF 14 deliberately spend more CPU and storage than the
old CRF 18/`medium` defaults. They do not alter resolution or frame rate. Users
who value throughput or smaller output can choose Balanced or Custom, while the
safe default prioritises avoiding visible degradation.

The source rate is deliberately not applied as fixed average bitrate (`-b:v`)
or minimum bitrate. Those modes can spend bits on simple scenes and starve
complex scenes. Source Fidelity instead remains CRF-led and uses a generous
source-derived `maxrate`/`bufsize` envelope. Efficient sources such as HEVC,
AV1, and VP9 receive conversion headroom when the output encoder is H.264, and
a resolution/frame-rate floor protects unusually compressed inputs.

The relevant FFmpeg behaviours are documented in the official
[FFmpeg command-line documentation](https://ffmpeg.org/ffmpeg.html) (`-fps_mode`,
`-enc_time_base`) and [codec documentation](https://www.ffmpeg.org/ffmpeg-all.html)
(libx264/libx265 CRF and presets).
