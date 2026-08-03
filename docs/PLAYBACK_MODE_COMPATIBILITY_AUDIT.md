# Playback mode compatibility and overhead audit

**Completed:** 2 August 2026
**Scope:** PC browser playback, TV browser/kiosk mode, Cast-tab mode, Party Mode admission, and the retired Kodi add-on.

## Supported modes

| Mode | Media clock and streams | Visual workload | Compatibility path |
|---|---|---|---|
| PC | One audio-master stream plus one muted video-only stream | Animated artwork wall, optional blur and transitions | Original/remux by default; optional H.264 compatibility transcode |
| TV | One combined audio/video stream; the visible video is the only queue-advance owner | Fixed 16:9 canvas, scrolling wall, fade-only tile changes, no blur | Original/remux by default; optional H.264/AAC transcode |
| Cast | One combined audio/video stream; the visible video is the only queue-advance owner | Static artwork wall, no tile swaps, no blur | Original/remux by default; optional H.264/AAC transcode |

The PC dual-stream design costs an extra connection and demux operation but keeps audio continuous while the muted visual surface is repaired or resynchronised. TV and Cast deliberately use a single stream to avoid clock drift and duplicate decoding. A full compatibility transcode is the highest-cost path and remains opt-in per mode. Server-side full encodes are bounded by the shared transcode semaphore.

## Findings and remediation

### PC native fullscreen exit

Native fullscreen and Playarr's presentation mode previously diverged when Escape or browser chrome exited fullscreen. The store remained in video-only mode and Chromium could retain a detached decoded-video compositor layer while the independent audio element continued.

Fullscreen exit now clears presentation mode and remounts only the PC's muted video surface. The audio master, queue occurrence and playhead remain intact; the replacement video seeks to the audio clock when playback resumes. Theatre mode and ordinary route navigation retain the same mounted player.

### Shared activity reveal

Pointer, keyboard and focus activity now drives one visibility timer for the queue, metadata card and controls in every profile. A stationary pointer is ignored so the animated artwork wall cannot continually wake the interface.

### TV duplicate/restarted transitions

The hidden PC audio element previously paused when TV/Cast took ownership but retained its source and active event handlers. A delayed `ended` event could therefore advance the shared queue after the visible TV video had already advanced it. This explained tracks apparently ending early, being skipped, or a repeated occurrence appearing to restart.

TV/Cast admission now fully detaches the desktop audio source and every audio callback rejects events while TV ownership is active. The visible media element also retains its per-source session token, so stale or duplicate `ended` events cannot claim another transition.

### TV/Cast admission and controls

The start surface now combines playlist selection, artist/album/genre restrictions, minimum song/video ratings, all supported version exclusions, the adult-content switch, era and layout. Saved exclusion lists are always combined with these session choices. Controls have 56px-or-larger targets, visible focus rings and linear D-pad navigation.

During playback TV and Cast show only Previous, Random and Next as large remote targets directly below the metadata card. Pointer/controller activity restores the metadata card, queue and transport together. The desktop seek/play/repeat bar is not rendered on these surfaces.

### Kodi add-on

The Playarr Kodi add-on, download/export routes and bundled assets were already retired in 1.11. No Kodi runtime is part of the supported playback matrix. NFO and artwork compatibility remains a media-library export feature and is independent of the removed add-on.

## Regression coverage

- Fullscreen transition classification verifies that only PC remounts its video surface.
- Media-session tests reject stale and duplicate `ended` events.
- Audio ownership tests prove delayed desktop events cannot move the TV queue.
- TV/Cast transport tests cover target sizing and directional focus.
- Party start tests cover playlist/audience controls and remote focus movement.
