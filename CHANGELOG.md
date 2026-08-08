# Changelog

## [1.11.1] - 2026-08-08

### Fixed
- **Windows console flashes while Playarr is open** — Queue health polling now launches ffmpeg, ffprobe and yt-dlp with `CREATE_NO_WINDOW`, and startup orphan-process cleanup is hidden too.

## [1.11.0] - 2026-08-01

### Added
- Hash-locked backend and frontend verification, Windows CI, sanitised media fixtures, generated OpenAPI types and module-growth gates.
- Durable priority mutation commands, bounded write queues, operation polling, optimistic video revisions and recoverable managed-file rename plans.
- Sidecar v2 validation, atomic backups, transactional outbox reconciliation and a two-pass portable-identity rebuild service.
- Migration preflight, online backup, integrity verification, logical reconciliation, inspectable reports and automatic restore on failed upgrades.

### Changed
- URL and disk imports share the same workspace, policy, mutation-plan builder, Stage-C actor and deferred dispatcher, with resumable checkpoints and structured stage events.
- Library/navigation list views use aligned sortable columns and URL-backed query state; permanent UI actions consistently use Save, Undo, Cancel, Remove and Delete language.
- Resolve video routes use explicit `/api/resolve/videos/{video_id}` paths and frontend types are checked against the generated API contract.

### Removed
- Kodi add-on routes, settings, export commands, exporter source and bundled plug-in assets. Existing NFO sidecars and media files are unaffected; see `docs/KODI_REMOVAL.md`.

### Fixed
- Legacy databases are backed up and migrated before startup recovery queries, preventing the v1.10 startup crash.
- Archive manifests resolve videos by portable Playarr ID instead of transient SQL row IDs.
- Committed sidecar changes survive a process stop between the database commit and filesystem write.

## [1.10.1] - 2026-08-01

### Fixed
- **Legacy database startup migration** — bundled installs now add and backfill the processing-job request and operation correlation columns before startup recovery queries run. The repair is idempotent and resumes safely after a partially completed upgrade.

## [1.10.0] - 2026-08-01

### Added
- **Durable operations and portable recovery** — sidecar schema v2 adds stable identities, revisions, content hashes, crash-safe atomic writes, reconciliation, and an outbox; mutations now expose correlation-aware operation state and deployment diagnostics.
- **Review and remediation workflows** — persistent review cases, dependency edges, remediation plans, crop evidence, source-fidelity checks, playlist revisions, and occurrence-aware batch editing.
- **Structured integrations and diagnostics** — TMVDB contribution eligibility and durable submission outbox, one typed import policy across entry points, structured/redacted scraper traces, and downloadable diagnostic bundles.
- **Expanded library and playback controls** — artist consolidation, genre management, archive restore planning, preference registry, shared data views, persistent playback surfaces, and party-start gating.

### Changed
- **Pipeline convergence** — URL, library, and rescan imports now share policy semantics and canonical leaf services while compatibility adapters preserve the existing stage entry points.
- **API and settings contracts** — corrected OpenAPI routes, constrained and grouped settings, masked secrets, explicit AI lifecycle state, canonical queue paging, and durable editor/download jobs.
- **Frontend structure** — route-level lazy loading and error boundaries, common filtering/data-view components, and consistent navigation across library management pages.

### Removed
- **Retired public Kodi surfaces** — the Kodi add-on/export endpoints and settings navigation are no longer exposed; generic internal NFO interoperability remains available.

### Fixed
- **Artwork safety and reuse** — valid existing artwork is preserved, fallback artwork copies use the validated copy gateway, and image-persistence enforcement distinguishes metadata/API calls from binary image downloads.
- **New Videos acceptance and replacement rules** — completeness, diversity, replacement, and recommendation acceptance now use explicit server-side contracts.

## [1.9.40] - 2026-07-04

### Fixed
- **Party Mode playlist now applies everywhere, including Kodi** — choosing a Party Mode playlist (Settings → Party Mode) is now honoured by the server's party-mode endpoint, so the same playlist plays across the web player, TV, Cast, and the Kodi add-on. Previously the choice only took effect in the browser/TV/Cast (applied client-side); Kodi reached Party Mode only through the server and so ignored the playlist, falling back to filter-based generation. The Kodi "Start the Party" prompt now shows the active playlist name instead of the (now-overridden) exclusions summary.

## [1.9.39] - 2026-07-04

### Fixed
- **Video editor crop panel no longer shifts when you enter the first value** — a follow-up to the earlier crop-jump fix. The crop *result* readout (e.g. `1920×800+0+140`) only appeared once a value went non-zero, which grew the panel and nudged the stepper controls out from under the cursor. It's now always shown — displaying the source dimensions (`1920×1080 · no crop`) when there's no crop — so the panel height stays constant and the arrows don't move.

## [1.9.38] - 2026-07-04

### Added
- **Send to Video Editor from the Now Playing queue** — right-clicking a track in the queue now has a *Send to Video Editor* option that adds it to the editor queue, matching the action already available from a video's detail page.

### Changed
- **AI now runs on import only when you select it — no accidental token spend** — with an AI provider configured in Settings, importing a video *without* choosing AI could still spend tokens: the post-import enrichment step was queued on every advanced/URL import regardless of the per-import AI toggle, and the plot-rewrite step ran whenever an environment provider was set. Both now respect the toggle (in both the URL and library-import pipelines), with a defence-in-depth re-check before enrichment ever calls a provider. Scene analysis was already token-free (local ffmpeg). Selecting AI works exactly as before.
- **Metadata Manager uses a vertical sidebar** — the horizontally-scrolling tab strip is replaced with a left-hand vertical menu (with a mobile dropdown), matching Settings, so the sections no longer scroll off-screen.
- **Kodi Add-on moved under Cast Mode** — in Settings, the Kodi Add-on section now sits directly beneath Cast Mode alongside the other external-playback options.
- **Clearer video-editor trim controls** — the trim fields are relabelled **Trim off start (s)** / **Trim off end (s)** (with tooltips spelling out that they cut from the beginning / end), and entering a value now moves the playhead to that point — type/step *Trim off start* to 1.1s and the preview jumps to 1.1s; *Trim off end* jumps to the new end point — so you can see exactly what will be removed.
- **More reliable automatic letterbox detection** — black-bar detection no longer trusts a single sample window (which a lone dark or bright scene could throw off). It now samples several windows across the whole video and takes a consensus: per-window mode + across-window median, dark-frame rejection, symmetry snapping (a lopsided top/bottom reading is treated as unreliable rather than cropping into real content), and relative thresholds with even alignment. The result is fewer missed letterboxes and, more importantly, far fewer false crops.

### Fixed
- **TV mode "Start the Party" now shows it's working** — pressing Start could sit on a black screen for several seconds (queue fetch + the on-the-fly transcode buffering) with no sign anything had happened. The button now switches to a spinner immediately, and a prominent full-screen **"Starting the party…"** overlay stays up from the press until the first video frame actually plays — covering the whole buffering window, not just the queue fetch. If autoplay is blocked, the overlay steps aside so the existing "Press OK to start" prompt is visible.
- **Review queue: the "Reclassify" menu is no longer cut off** — the version-type dropdown was clipped by the duplicate-group card's boundary, hiding some options and making it unusable. It now renders in a popover layer so every option is reachable.
- **Review queue: duplicate comparison items are now actionable** — in a duplicate group, the pre-existing library item (labelled *Existing*) previously showed only "Not in review queue" with no controls. You can now reclassify or delete it too, so you can keep the newer copy and remove the old one directly.
- **Artist conflicts now explain themselves** — the Metadata Manager's "N artist conflicts" warning previously gave no indication of what it meant. The overview now describes the problem (one artist stored under different name spellings) and links straight to Artist Consolidation to fix it, and notes that fixing is optional.
- **Video editor remembers a video's scanned crop** — a previously letterbox-scanned video now loads into the editor with its detected crop already applied, instead of appearing uncropped and needing a rescan every visit. (The applied crop lived only in memory and reset on navigation, and a guard meant to avoid re-running detection also blocked re-applying the stored result; the backend's stored crop is now re-applied on load, and videos already scanned aren't re-detected.)
- **Crop field no longer jumps when you press the up arrow** — with all crop values at zero, clicking a stepper's up arrow made the *Clear* button appear, which shifted the panel and moved the arrow out from under the cursor. The button's space is now always reserved, so repeated clicks land.
- **New Videos actions no longer lock up during imports** — refreshing suggestions, adding to / removing from the cart, and dismissing videos could appear to freeze for up to 30 seconds (or fail with *"database is locked"*) while an import was writing new videos to the library. Interactive requests committed on their own database session, bypassing the pipeline's serialised write queue, so they collided with it at the SQLite level and (worse) could stall the whole write queue behind them. All interactive endpoints now run on a guarded database session that routes every write through the same global lock the import pipeline uses, so requests and imports can no longer contend.
- **The same lock-up affected many other actions** — video editing (encode, batch-encode, restore-from-archive, exclude-from-scan, letterbox scan), the review/match queue (approve/dismiss/apply-match/rename), metadata and library edits (rename, delete, canonical links, artwork), playlists, playback-history recording, and preference changes all committed on the request session and are now serialised the same way. Any of these performed during an active import no longer blocks.
- **New Videos "Refresh" no longer blocks the UI for minutes** — feed generation runs many slow YouTube searches; it now runs in the background and the page streams suggestions in as each category completes (the Refresh button keeps spinning until it finishes) instead of holding one long request open. Generating an empty feed on first load is likewise done in the background instead of hanging.

### Changed
- **Write serialisation is now enforced at the session layer, not per-endpoint** — the shared write lock is now re-entrant, and the request database engine carries an automatic guard that acquires it on the first write of a transaction and releases it on commit/rollback. Endpoints that commit are serialised against the background write queue *by construction*, so this class of "action appears to lock" bug cannot be reintroduced by a new endpoint forgetting to opt in. (Internal; no behaviour change beyond the fixes above.)

## [1.9.37] - 2026-07-03

### Added
- **Drag-and-drop playlist ordering** — playlist tracks can now be reordered by dragging (grab the handle), in addition to the up/down buttons. Changes are staged locally and applied with an explicit **Save order** / **Discard** bar, so you can rearrange freely and commit once instead of a save per move.
- **Shuffle & Play All** — a new button beside *Play All* on each playlist queues the whole playlist in a random order.
- **Video editor — set trim points from the playhead & keyboard shortcuts** — trim in/out can be set to the current playback position with a click, and the editor now has shortcuts: **Space** play/pause, **←/→** seek, **Shift+←/→** frame-step, **I/O** set trim in/out.
- **Video editor — Restore Original & Cancel encode** — the archived pre-edit original can now be restored directly from the editor (previously only reachable from the Archive page), and a running encode can be cancelled from the progress banner.
- **Duplicate scan runs automatically after a library scan** — importing new files now triggers a duplicate check, and a **fingerprint (AcoustID) identifier is captured** during content identification so fingerprint-based duplicate matching actually engages.

### Changed
- **Smarter duplicate detection** — title normalisation now strips version qualifiers (remix, remaster, live, edit, …) wherever they appear in brackets — including cases like *"Kernkraft 400 (DJ Gius Remix)"* vs *"Kernkraft 400"* — as well as trailing "– Radio Edit"-style suffixes; artist matching now ignores accents (*Beyoncé* ≡ *Beyonce*) and trailing *feat./ft.* credits. A file skipped as a duplicate during import is now flagged for review instead of vanishing silently.
- **Video editor clarity** — the "Aspect Ratio" control is relabelled **Stretch to ratio (DAR)** (it stretches, it does not crop), the overlay toggle and queue-removal buttons are relabelled to reflect what they actually do, an **encode confirmation** step summarises what will be applied before the file is replaced, and a library scan now **merges into** the editor queue instead of clearing your manually-added items.
- **Playlists reflect edits in "Recently Updated"** — reordering, sorting, or adding/removing tracks now updates the playlist's timestamp.

### Fixed
- **Archived originals can no longer be played, scanned, or re-imported** — once a video is encoded and its original bumped to `_archive`, playback endpoints reject the archived path, the library importer refuses archive directories, and the scanner skips them. The archive is now genuinely a restore-only holding area.
- **Re-encoding no longer overwrites the true archived original** — a second edit of the same video archives the intermediate under a timestamped name and keeps the manifest pinned to the real original, so *Restore Original* always recovers the genuine source. Restore also no longer risks deleting the true original during cleanup.
- **Stale video after a crop/re-encode** — re-encoded videos no longer keep playing the pre-edit version from the browser cache (streaming responses now send cache validators).

## [1.9.36] - 2026-07-02

### Added
- **In-app yt-dlp updater** — Settings → About → System Information can now update (or first-time install) the yt-dlp download engine on its own, independently of a full Playarr reinstall. It shows the installed vs latest version, downloads the binary from the official yt-dlp GitHub releases into a user-writable managed location, verifies it runs, and swaps it in atomically. Because yt-dlp tracks YouTube's constantly-changing player, keeping it current is the usual fix for downloads capping at low resolution.
- **Playlist reorganisation** — a playlist's tracks can now be **sorted by artist, title, or year (A–Z / Z–A)** — the new order is saved — and **manually reordered** with per-track up/down controls.
- **Rename playlists** — a playlist's name can now be edited inline from its detail view (hover the title, click the pencil, Enter to save / Esc to cancel).

### Changed
- **Playlists are now de-duplicated** — a track can only appear in a playlist once. The Add-to-Playlist picker shows which playlists already contain the track and acts as a **toggle**: clicking a playlist it's already in **removes** it instead of adding a duplicate, so you can quickly add/remove across playlists. (Batch adds silently skip tracks already present.)

### Fixed
- **YouTube downloads now reach full resolution (4K)** — yt-dlp is invoked with the EJS remote challenge-solver enabled (`--remote-components ejs:github`), so it can decipher YouTube's signature/n-parameter and access the high-resolution DASH formats. Without it, only the pre-muxed 360p stream was usable and downloads silently fell back to 360p.

## [1.9.34] - 2026-07-02

### Added
- **Rate & playlist tracks from the queue** — right-clicking a track in the Now Playing queue opens a context menu to **add it to a new or existing playlist** and to **rate the song or video** (5 stars each), without leaving the page.
- **Party Mode playlist** — Settings → Party Mode can now select an existing playlist as the Party Mode source; when set, Party Mode plays that playlist (shuffled). Leaving it on *Auto-generate* keeps the existing behaviour of building the queue from the current filter and exclusion/era settings.

### Changed
- **Start-with-Windows simplified for reliability** — the installer checkbox and the in-app *Start with Windows* toggle now manage a single HKCU `Run` entry pointing at the real `Playarr.exe`, replacing the previous two competing mechanisms (a Startup-folder shortcut plus a registry command that targeted a script not shipped in the installed build). Startup now launches to the system tray, and the in-app toggle syncs the registry reliably after saving.

## [1.9.33] - 2026-06-19

### Added
- **Cast Mode (`/cast`)** — a new low-overhead Party Mode page tuned for Chrome's "Cast tab" feature. Because Chrome re-encodes the entire tab while casting, this page holds the artwork wall **static** (no scrolling, no tile swaps) and disables blur, so only the video moves — keeping the cast sharp and the PC's encoder light. Has its own optional compatibility transcode toggle.

### Changed
- **Playback profiles** — Now Playing, TV (`/tv`), and Cast (`/cast`) are now driven by an explicit per-context profile, so each is optimised for its use case and the on-the-fly transcode is selected per context. TV transcoding therefore only runs while `/tv` is actually open, and Cast transcoding only while `/cast` is open — no background encode overhead on the PC otherwise.
- **Animation tuning for compatibility** — outside regular browser playback the heavy effects are dropped automatically: `backdrop-filter` blur (queue panel + metadata overlay) is disabled for TV and Cast, and artwork tile swaps are forced to plain opacity fades (no 3D flip/spin) since TV GPUs handle 3D transforms poorly. The cheap `translateY` artwork scroll is kept for TV.
- **Settings reorganised** — TV Mode and Cast Mode now have their own subsections under **Settings → Party Mode** (with short how/why instructions and the relevant transcode toggles), instead of living under Now Playing. Now Playing keeps only the regular **Browser** compatibility-transcode toggle.

## [1.9.32] - 2026-06-19

### Added
- **Server errors are now logged** — unhandled request errors (HTTP 500s) are written with full traceback to `playarr.log` and `crash.log`. Previously these were emitted only by uvicorn's own logger, which in the packaged windowed build goes to a discarded stderr, so playback/stream failures left no trace. (This was the missing piece that made the streaming issues hard to diagnose.)

### Notes
- Verified the compatibility transcode end-to-end in the packaged build (H.264/AAC, ≤1080p): both A/V and video-only paths stream with the bundled ffmpeg.

## [1.9.31] - 2026-06-19

### Added
- **Compatibility transcoding (per-context toggles)** — Settings → Playback now has two switches: *Transcode for Compatibility — TV mode* and *— Browser*. When on, the server re-encodes video on the fly to a broadly-compatible, network-friendly **H.264 High / 8-bit / ≤1080p / ~6 Mbps + AAC** stream (`?transcode=1` on the stream endpoints). This fixes frame drops/stutter caused by source codecs the device can't decode smoothly (HEVC/VP9/**AV1**, 10-bit, or bitrates too high for the link) — e.g. an AV1 2880×2160 source is delivered as H.264 1920×1440. TV and browser are independent so you can target each. Costs server CPU when enabled.
- **Playback diagnostics** — to pinpoint network drop-outs/hangs:
  - Server: every stream now logs an end-of-stream summary (MB sent, duration, **throughput in Mbps**, time-to-first-byte, ffmpeg return code).
  - Client: a per-video monitor reports **dropped/total frames, stall count, time spent waiting, and seconds buffered ahead** to the server log via `POST /api/playback/client-metrics` (every 15s + on track end) — visible in `playarr.log` without opening dev tools, which matters for TV devices.

## [1.9.30] - 2026-06-19

### Added
- **Self-healing server supervisor** — the server now runs as a supervised child process. If it ever dies unexpectedly (crash, native fault, or an external force-kill of the server) the supervisor automatically relaunches it, with a crash-loop guard (5 failures in 60s → falls back to running in-process so the app can never be left unable to start). This also fixes **Settings → Restart**, which previously did nothing in the packaged build (it exited the process with no supervisor to relaunch it).

### Changed
- **Graceful shutdown for clean stops/updates** — new `POST /api/settings/shutdown`. The installer now asks a running Playarr to shut down cleanly (releasing the executable and sockets) and only force-kills as a fallback, so updates no longer hard-kill the app and look like a crash in the logs.
- **Orphan prevention** — a supervised server process exits automatically if its supervisor disappears, so a killed supervisor can never leave a server running and holding the port.
- Crash diagnostics, the heartbeat, and the worker thread-pool limit from recent releases remain in effect.

## [1.9.29] - 2026-06-19

### Fixed
- **Queue auto-hide never triggered in TV mode** — browsers fire synthetic `mousemove` events when animated content (the scrolling artwork grid) moves under a stationary cursor, and those kept resetting the auto-hide timer, so on a TV (where the pointer never actually moves) the queue stayed visible. Mouse movement now only counts when the pointer's coordinates actually change.

### Changed
- **Video re-centres when the queue auto-hides** — when the queue fades out it now slides off to the right and the video gently glides (700ms) to the centre of the screen; when the queue returns, the video glides back to its position. Applies wherever queue auto-hide is enabled (TV and windowed).

## [1.9.28] - 2026-06-19

### Fixed
- **TV mode stalled/hesitated when advancing to the next track** — on each track change TV mode called `killStreams()`, which kills *all* active streams and raced with the stream it was about to start, killing the next track's FFmpeg and forcing a re-request (visible in the logs as the same file streamed twice seconds apart). Changing the persistent video's `src` already aborts the previous stream (the server cleans it up on disconnect), so the redundant `killStreams()` call is removed — the queue now advances smoothly.
- **TV artwork wall too sparse (only a few tiles on a 4K TV)** — TV browsers render the page at a low logical viewport, so the native-viewport rendering introduced in 1.9.27 left the grid only a few tiles wide. TV mode again lays out on a fixed 16:9 canvas at a configurable resolution (Settings → Playback → "TV Mode Resolution": 1080p / 2K / 4K, default 1080p) and CSS-scales it to fit, giving browser-like tile density regardless of what the TV browser reports.
- **A track (notably a heavy 4K transcode) could end partway through and skip to the next** — Starlette pumps a streaming response's sync generator by re-acquiring a worker-thread per chunk, and a burst of concurrent artwork-image requests (the Now Playing grid) could saturate the default 40-thread pool and starve the video stream's chunk reads, underrunning playback until the element reported end-of-stream and auto-advanced (the logs showed the worker-thread count spiking to ~49 right as the 4K track terminated). The worker thread-pool limit is raised to 256 so streaming and artwork serving no longer contend.

## [1.9.27] - 2026-06-19

### Added
- **Crash diagnostics** — the server still died during playback streaming with no traceback (and no Windows error event), because the frozen windowed build sends stdout/stderr to devnull, so an unhandled exception that ends the process leaves no record. A new diagnostics layer now captures the cause to `logs/crash.log`: native faults via `faulthandler` (all-thread dump), unhandled exceptions on the main and worker threads, asyncio loop exceptions, a wrapper around `uvicorn.run`, and an `atexit` marker. A 20s heartbeat (`threads=… active_streams=…`) is logged so thread/stream accumulation is visible and the last heartbeat pinpoints when the loop stopped. If the server dies again, `crash.log` will name the cause.

### Changed
- **TV mode now renders at the device's native viewport** — congruent with the normal browser: artwork-grid density, video size, queue, controls and behaviour are identical to a regular browser tab. The fixed-canvas scaling (and its "TV Mode Resolution" setting) is removed; artwork tile size is controlled by the existing "Tile Size" setting, the same as the browser. TV-specific behaviour is limited to single-stream audio playback and the one-tap autoplay fallback.

## [1.9.26] - 2026-06-19

### Fixed
- **TV mode artwork grid didn't fill the screen** — the grid container is what the size-measuring ResizeObserver attaches to, but in the empty (pre-load) state the component returned a *different* element without that ref, so the observer never attached and the grid stayed sized to the initial window. On desktop the window equals the canvas so it looked fine; in TV mode the canvas is a different scaled size, leaving the grid too small. The container now always renders, so the grid measures and fills the canvas at any resolution.

## [1.9.25] - 2026-06-19

### Fixed
- **Server instability/crash during playback streaming** — the video streaming endpoints piped ffmpeg's stderr but never drained it; on files where ffmpeg emits continuous warnings (e.g. non-monotonic DTS), the OS pipe buffer filled, ffmpeg blocked on the stderr write, and the stream thread hung forever. Under TV mode's rapid track cycling this exhausted the server's thread pool and brought it down. ffmpeg stderr is now discarded (`DEVNULL`) so it can't deadlock. Additionally, the `/kill-streams` endpoint (called on every track change) now runs its blocking process kills off the event loop, so it can't freeze the server.

## [1.9.24] - 2026-06-19

### Fixed
- **TV mode stalled at the end of each track** — the TV video element was keyed by track ID, so every track change fully remounted it and started a fresh stream with no cleanup of the previous one (and lost its autoplay permission). TV mode now uses a single persistent video element driven imperatively — it kills the previous track's stream, loads the next, and plays — mirroring the desktop audio transition, so the queue advances continuously without stalling.

## [1.9.23] - 2026-06-19

### Fixed
- **TV mode video squashed into a narrow band** — the Now Playing video area was sized with viewport units (`vh`), which inside TV mode's CSS-scaled render canvas pointed at the smaller real browser viewport instead of the canvas, collapsing the video into a thin band. The video area now sizes relative to the TV canvas height, so it fills the 16:9 display correctly at any resolution.

## [1.9.22] - 2026-06-19

### Changed
- **Queue auto-hide is now a general playback setting** — it applies on the Now Playing screen in both windowed and fullscreen layouts (previously fullscreen/theatre only), so the desktop install honours it too.

## [1.9.21] - 2026-06-19

### Added
- **TV mode resolution setting** — Settings → Playback → "TV Mode Resolution" (720p / 1080p / 2K / 4K). The `/tv` page now lays its visual out on a fixed 16:9 canvas at the chosen resolution and CSS-scales it to fit the screen, so artwork-grid density and the video-to-background ratio no longer depend on whatever low logical viewport a TV browser reports. Raise it if the artwork wall looks sparse / the video looks oversized.
- **Queue auto-hide in theatre mode** — Settings → Playback → "Queue Auto-Hide": *No auto-hide* (always shown), *Per song* (fades out after a delay, reappears at the start of each track), or *Full auto-hide* (fades out and only returns on mouse movement). A configurable "Hide After" delay drives the timer. The queue gently fades and reappears on mouse movement, mirroring the play-bar behaviour.

### Changed
- The Now Playing artwork grid now measures its own container rather than the window, so it fills the TV render canvas correctly (and reacts to resolution changes).

## [1.9.20] - 2026-06-18

### Changed
- **TV mode (`/tv`) re-architected to single-stream playback** — instead of the desktop player's dual-stream design (a hidden `<audio>` driving the clock while a separate muted video follows it, which was bandwidth-doubling and unreliable on TV browsers), TV mode now plays one combined audio+video stream in the on-screen video element, which is its own clock and advances the queue itself; the global audio element stays silent. This fixes the no-audio / stuck-loop / slow-load problems at the root while rendering the *exact* Party Mode visual (artwork wall + video + queue) natively — no cast/mirror re-encode. Autoplay starts on its own in a kiosk browser with autoplay enabled; otherwise a one-tap "Press OK to start" prompt appears. The desktop player is unchanged.

## [1.9.19] - 2026-06-18

### Fixed
- **TV mode (`/tv`): no audio, stuck-in-a-loop, slow loading** — the player is audio-master (a global `<audio>` drives the clock; the on-screen video is muted and slaved to it), and browsers block autoplay-with-sound without a user gesture, so on TV devices the audio never started, the clock stalled, and the muted video was repeatedly yanked back to the start (the loop) while thrashing the remux (slow load). TV mode now builds the Party Mode queue but holds playback behind a one-time "Press OK to start" prompt; that gesture unlocks audio for the session, after which the clock advances and the video syncs normally. Renders natively on the device — no cast/mirror re-encode.

## [1.9.18] - 2026-06-18

### Added
- **TV / kiosk mode (`/tv`)** — a single full-screen deep link that auto-starts Party Mode with no app chrome, for casting the scrolling-artwork visual to a TV. Designed to be opened by a browser on a TV device (e.g. NVIDIA Shield, Android/Google TV, or a Chromium kiosk) so the page renders **natively at full resolution** — no screen-mirroring re-encode, so quality is limited only by the source video bitrate.

### Fixed
- **Kodi add-on: Party Mode crash** — "shuffle all" no longer builds a playlist of the entire library (which could exhaust memory and crash Kodi); the queue is capped (configurable, default 200) and the originating directory handle is released before playback starts to avoid a re-entrancy hang.
- **Kodi add-on: folder artwork** — Artist / Album / Genre / Year folders now show real artwork (representative `artist_thumb` / poster) instead of the generic add-on icon.
- **Kodi add-on: home-screen access** — added an "Add Playarr to Favourites" action (Kodi add-ons can't inject a skin main-menu item directly); documented per-skin setup.

## [1.9.17] - 2026-06-18

### Added
- **Kodi Add-on (bundled & version-matched)** — the Kodi plugin is now distributed from the server itself via **Settings → System → Kodi Add-on**. The download is built on demand from the bundled add-on source and its `addon.xml` version is stamped to the running server's version, so the plugin and server can never drift out of sync; includes in-app install instructions. The add-on's "Test connection" now warns if the installed add-on and server differ on major.minor and prompts a re-download.
- **Party Mode: "Party Like It's…"** — new Party Mode setting to cap playback at a chosen year (nothing newer plays) and weight the queue toward that era: videos closest to the target year are favoured for the front of the queue with a gradual fall-off (~10-year half-life) the further back they go. Videos with no known year stay in the pool at a low weight. Stored server-side so the Kodi add-on inherits it.

### Fixed
- **Windows Startup: App Appeared Running but Unreachable** — at logon the process started but the web UI stayed unreachable for minutes (cold disk + antivirus), requiring a manual kill and restart. Heavy startup maintenance — full-library untracked-file detection, zombie-record cleanup, artwork repair, orphan purges, duration backfill, and duplicate/rename scans — now runs in a background thread *after* the server begins accepting connections, instead of blocking uvicorn's lifespan startup.

### Changed
- **Genre Consolidation & Genre Manager** — doubled the scroll-area height of the Active Consolidations and genre lists for easier browsing.

## [1.9.14] - 2026-04-12

### Added
- **Audio Download** — new Music icon button on the video detail page extracts audio as a CBR MP3 file; FFmpeg detects source bitrate and channel count, snaps to nearest standard bitrate (64–320 kbps), and streams the result with a busy spinner; ID3 tags include artist, title, album, year, genre, poster artwork (APIC), and Windows Media Player–compliant POPM star rating via mutagen
- **Live Search on Facet Pages** — typing in the global search bar now live-filters the current facet page (Artists, Albums, Years, Genres, Ratings, Quality) with 250 ms debounce; the query syncs to the URL as `?search=` so results persist on refresh; a clear (×) button resets the filter; on non-facet routes, Enter still navigates to the Library page
- **New Videos: Preference-Based Recommendations** — "Songs You Might Like" now uses a multi-signal scoring engine: 5-star artists (weight 1.0), 4-star (0.6), 3-star (0.3 if fewer than 8 artists from higher tiers), plus a play-count engagement bonus from PlaybackHistory (0.05 per play, capped at 0.5); a new Phase 3 discovers videos in the user's top 3 genres via yt-dlp search
- **New Videos: Personalized Sections First** — "Songs You Might Like" and "Recommended By Artist" are now the first two sections on the New Videos page, ahead of Famous/Popular/New/Rising

### Fixed
- **URL Import Fails During Finalising** — adding a video by URL while other imports were in the Finalising stage could silently fail (spinner would spin then revert, URL stayed in the input field); root cause was the import endpoint's `db.commit()` contending with the write queue's `_apply_lock` at the SQLite level until `busy_timeout` (30 s) expired; job-creation commits in `import_by_url`, `_import_playlist`, `redownload_video`, and `rescan_metadata` now acquire `_apply_lock` before committing, serializing correctly with pipeline writes
- **New Videos: Junk Filtering** — videos longer than 15 minutes are now hard-blocked (trust score = 0.0) instead of receiving a trivial −0.10 penalty; videos 8–15 minutes receive a −0.25 penalty; new hard-block title patterns for "N hours of", "full album", "nonstop", and "megamix" compilations; "compilation" keyword penalty increased from −0.10 to −0.20
- **New Videos: Sparse Suggestion Lists** — each category now displays up to 20 suggestions (was 12); the "Recommended By Artist" section lowered its minimum-owned threshold from 2 to 1 video so it works with smaller libraries, and searches up to 8 artists (was 5); the taste engine searches up to 6 artists (was 3) with a generation limit of 20 (was 10)
- **Scan Metadata: Unicode Hyphen False Identity Change** — AI Source Resolution returning artist names with Unicode hyphens (en-dash U+2013, etc.) and AI Final Review normalising to ASCII hyphens was falsely detected as an artist identity change, triggering invalidation of all MusicBrainz IDs, IMDB URL, and Wikipedia sources; identity change set comparison now normalises Unicode hyphens to ASCII before comparing, matching the normalisation already used in search functions
- **Schema Upgrade: Missing crop_position Column** — `crop_position` on `media_assets` and `cached_assets` had an Alembic migration (017) but was not included in `_apply_schema_upgrades()`, causing silent failures on existing databases upgraded in-place by the bundled installer
- **Now Playing: Muted Background Stream** — MKV files used for the muted background artwork grid were being served with full audio tracks, wasting bandwidth; new `/stream-video-only` endpoint remuxes MKV to fragmented MP4 with audio stripped for muted playback contexts

## [1.9.13] - 2026-04-11

### Added
- **Artwork Manager** — new tab in Metadata Manager with pie chart breakdowns of poster art sources (source art vs thumbnail fallback), artist/album coverage stats, searchable entity browser with pagination, upload/delete/refresh artwork for artists, albums, and video posters, artwork crop position adjustment via focal point selector lightbox, and entity sources editor for MusicBrainz IDs and Wikipedia URLs
- **Artwork Crop Position** — clickable focal point selector on artwork lightbox sets CSS `object-position` for artist, album, and video poster art; persisted to `crop_position` column on `media_assets` and `cached_assets`
- **Review Queue: Artwork Categories** — two new review categories (`artwork_incomplete`, `missing_artwork`) with "Scan Artwork" button, filter pills, and "Scan Sources" bulk action to repair missing entity artwork
- **Safe Delete (Recycle Bin)** — deleted files are now sent to the OS recycle bin instead of permanent deletion; network/UNC paths where recycle bin is unavailable raise a confirmation prompt before falling back to permanent delete
- **Queue: History Sorting** — completed jobs in the Queue history tab can now be sorted by Date Added, Date Completed, Artist, or Title with ascending/descending toggle; sort preference persisted to localStorage
- **Party Mode: Pre-Rendered Fireworks** — fireworks celebration animation is pre-rendered to a WebM blob via `captureStream` + `MediaRecorder` on settings change, then played back as a video element for zero-CPU animation playback
- **Library Scan: Update Existing Mode** — new scan mode that re-reads sidecar XMLs for already-tracked items and syncs changed fields (metadata, ratings, quality/letterbox, sources, artwork, entity refs, processing state) back into the DB; designed for multi-install setups sharing the same library
- **Library Scan: Mode Selector** — Scan Library in Settings now has radio options (Import New / Update Existing / Both) matching the Export Library UI pattern
- **Startup: Zombie Record Cleanup** — on launch, DB records whose video files no longer exist on disk are automatically detected and removed, along with their child rows, cached assets, thumbnails, and orphaned entity folders
- **User Edit Provenance** — anonymous instance user ID (auto-generated UUID) silently tracks who made each edit; `field_provenance_users` JSON on VideoItem and entity models maps each field to the user who last set it; `last_edited_by` on VideoItem, `user_id` on MetadataSnapshot, and `user_id` in review history entries enable future per-user trust scoring for the musicvideo DB
- **Queue: Skipped Job Art Cards** — skipped duplicate jobs now show an art card with the matched library video's poster, parsed title, and a link to the existing entry instead of a plain text line

### Fixed
- **Video Editor: Scan/Scan All Merged** — consolidated two separate scan buttons into a single button with a popup dialog offering Scan (selected) and Scan All options
- **Video Editor: Scan Progress Bar** — fixed missing per-file progress; now shows "Scanning 47/854: Artist — Title" with percentage
- **Video Editor: Sidecar XML Persistence** — letterbox scan results are now written to `.playarr.xml` sidecar after scanning
- **Video Editor: Post-Encode Cleanup** — letterbox fields are cleared and sidecar XML updated after encoding completes
- **Video Editor: Manual Tag + Filtering** — manually-added tracks show a blue "Manual" badge; new dropdown filter for All/Letterboxed/Manual
- **Video Editor: Skip Already-Processed Filter** — new scan options to skip videos that have already been cropped or trimmed
- **Sidebar: Review Count Not Updating** — the Review badge count only fetched once on mount; added 15-second auto-polling to match the Queue badge behaviour
- **Duplicate Check: Zombie Records Blocking Imports** — duplicate detection now ignores DB records whose files are missing from disk, preventing ghost entries from blocking re-imports
- **Duplicate Skip: Job Not Linked to Match** — skipped duplicate jobs now link `video_id` to the existing matched video so the UI can show poster art and a direct link
- **Review Queue: Stale AI/Scene Flags Not Clearing** — scene analysis and AI enrichment deferred tasks in all three pipelines now call `clear_stale_enrichment_review()` after completing, so review items auto-clear without requiring a manual refresh
- **Review Queue: Removed Unused Import Error Category** — the `import_error` review category filter pill was removed since no code path generates this category
- **Delete Error Feedback** — delete operations across Library, Artists, Albums, Years, Queue, and ActionsPanel now show error toasts instead of failing silently
- **Playback: Video Source Cleanup on Unmount** — VideoPlayer and NowPlayingPage now release the video `src` attribute on unmount/track-change, causing the browser to drop the HTTP connection and terminate the backend FFmpeg streaming process
- **XML Sidecar: Canonical Tracking Parity** — `canonical_provenance` and `canonical_confidence` are now written at the identity level (not nested in `<version>`) and round-trip correctly through export/import; `editor_edit_type` also persisted
- **Settings: Export Mode Tooltips** — library export radio options now have tooltip descriptions matching the scan mode selector

## [1.9.12] - 2026-04-10

### Fixed
- **Playback: Orphaned FFmpeg Streams** — stream generator `finally` blocks used `process.wait()` which waited for FFmpeg to finish naturally; rapid track changes accumulated zombie processes; changed to `process.kill()` for immediate cleanup on client disconnect
- **Playback: Background Animation Jitter** — artwork grid swapped 36-72 images with synchronous decoding on the main thread; added `decoding="async"` to all grid images and paused swap work when the tab is hidden
- **Playback: Artwork DB Query Overhead** — every poster/artwork/thumb request ran a fresh DB query even on cache hits; added in-memory artwork cache with 120s TTL to eliminate repeated queries from the background animation grid
- **Playback: Stale Overlay Metadata** — track change did not cancel in-flight metadata fetch requests; added abort guard to prevent stale API responses from updating state
- **Video Editor: Encode Jobs Stalling on Cancel** — `_run_encode_job` never checked `is_cancelled()`, so FFmpeg encoding continued after cancel; added cancellation check in progress callback and `process.kill()` on callback error
- **Video Editor: Retry Not Working** — `retry_job()` had no handler for `video_editor_encode` job type; added handler that reads params from `job.input_params` and spawns a new encode thread
- **Video Editor: Persistent Encoding Bar** — frontend only cleared encode state on "complete" or "failed", not "cancelled"; added cancelled status handling and stopped polling on terminal statuses
- **Video Editor: Missing Progress Logs** — `log_text` was only written on successful encode; now written for failed and cancelled jobs too
- **Video Editor: DB Write Throttle** — FFmpeg progress callback wrote to DB on every output line (~10/sec); throttled to 1-second intervals

### Added
- **Playback: Kill Streams Endpoint** — new `POST /api/playback/kill-streams` endpoint that terminates all active FFmpeg streaming processes; called by the frontend on track change to proactively kill orphaned processes
- **Startup: Zombie FFmpeg Cleanup** — on Windows, `taskkill /F /IM ffmpeg.exe` runs at startup and during installer setup/uninstall to clear any orphaned processes from a previous crash

## [1.9.11] - 2026-04-09

### Added
- **Genre Consolidation: Autofill Search** — typing in a genre consolidation tile input now shows autocomplete suggestions matching existing genres, with video counts and already-consolidated indicators; powered by new `/genre-search` endpoint with debounced (200ms) queries
- **Genre Consolidation: Add/Remove Genres from Tiles** — each active consolidation tile has a `+` button to add genres via autofill search and per-alias "Remove" buttons to unconsolidate individual genres
- **Genre Consolidation: Create New Tiles** — "New Tile" button with inline name input to create empty consolidation tiles from the Genre Consolidation tab
- **Genre Consolidation: Tile Blacklist/Whitelist** — eye toggle on each tile to blacklist or whitelist the entire tile (master + all aliases) in one operation; blacklisted tiles remain visible with reduced opacity, red background, and "Blacklisted" badge
- **Genre Manager: Consolidated Genre Display** — alias genres are hidden from the Genre Manager; master genres with aliases show a Layers icon with alias count badge

### Fixed
- **Genre Autofill: Manual Edit Input** — the manual edit input for suggested consolidations was a plain text field with no autocomplete; replaced with the autofill component in controlled mode
- **Genre Autofill: Dropdown Readability** — autofill dropdown had a semi-transparent background causing text overlap with content behind it; changed to solid opaque background

## [1.9.8] - 2026-04-08

### Fixed
- **Queue Display: Tracks Vanishing During Finalizing** — the frontend `isFinalizing()` check used a 2-minute `updated_at` window to detect active deferred processing; with the serialised write queue from v1.9.7, cosmetic DB updates queue up and `updated_at` goes stale, causing tracks to drop off the active tab; removed the time window — jobs now show as "Finalizing" until the step reaches "Import complete"
- **Deferred Timeout Too Aggressive** — `_DEFERRED_TIMEOUT` was 300s (5 min) across all three pipelines; with the serialised write queue and limited semaphore slots, large batches easily exceed this; raised to 1800s (30 min) in `pipeline_url`, `pipeline`, and `pipeline_lib`
- **Watchdog Force-Unsticking Queued Jobs** — the finalizing watchdog (`_FINALIZING_WATCHDOG_MAX_AGE = 600s`) would force-mark jobs as complete while they were still legitimately waiting for the write queue; raised to 2400s (40 min) and the watchdog now checks `active_coordinator_count()` and write queue `pending()` — skips its cycle entirely while the system is actively processing
- **Deferred Semaphore Too Restrictive** — `GLOBAL_DEFERRED_SLOTS` was 3, causing excessive queueing now that DB serialisation is handled by the write queue; raised to 6 since the semaphore only needs to limit I/O load (ffmpeg, network), not DB contention

### Added
- **Deferred Coordinator Tracking** — thread-safe `_active_coordinators` set in `pipeline_url/deferred.py` with `active_coordinator_count()` API; the watchdog uses this to distinguish genuinely stuck jobs from jobs waiting in a busy queue

## [1.9.7] - 2026-04-08

### Fixed
- **Unified DB Write Lock** — three pipelines (`pipeline_url`, `pipeline`, `pipeline_lib`) each had their own `threading.Lock()` for DB writes; when running concurrently (e.g. batch import + rescan + scrape), they could deadlock against each other on the same SQLite file; created a single shared `_apply_lock` in `app/db_lock.py` that all pipelines and the write queue now use
- **Pipeline URL Deferred: Undefined `_apply_lock`** — `pipeline_url/deferred.py` referenced an undefined `_apply_lock` at the Wikipedia poster fallback code path, which would have caused a `NameError`; replaced with `db_write()` wrappers
- **Raw DB Commits Not Serialised** — `pipeline/deferred.py` and `pipeline_lib/deferred.py` had multiple raw `db.commit()` calls outside any lock; all raw commits across both files now wrapped with `with _apply_lock:` to guarantee serialization

## [1.9.6] - 2026-04-08

### Fixed
- **Deferred AI Enrichment Silently Failing** — `_deferred_ai_enrichment` in `pipeline_url/deferred.py` had a `from app.models import VideoItem` re-import inside its error-handling `except` block, which Python treated as a local variable assignment, shadowing the outer-scope import and causing `UnboundLocalError: local variable 'VideoItem' referenced before assignment` on every invocation; the error was caught silently and the task logged as "completed" despite never running AI enrichment or setting the `ai_enriched` processing flag — preventing review queue auto-clear
- **Review Auto-Clear Killed by DB Lock on XML Sidecar Write** — the deferred coordinator's `finally` block ran both the XML sidecar rewrite and the review auto-clear inside a single `try` block; when batch-scraping many items, parallel coordinator threads caused `database is locked` errors on `_final_write_xml`, which aborted the entire block including the auto-clear; the `pipeline_url` variant now routes both operations through `db_write` (the serialized write queue), and the `pipeline`/`pipeline_lib` variants use a retry loop with exponential backoff; in all three, the XML write failure is now caught independently so auto-clear always proceeds

## [1.9.5] - 2026-04-08

### Fixed
- **Review Queue: Items Not Clearing After Scrape** — `scrape_metadata_task` set `ai_enriched` processing flag but never cleared the review status; added auto-clear logic to the Finalise section for `ai_pending`, `ai_partial`, and `scanned` categories
- **Review Queue: Items Not Clearing After Rescan** — `rescan_metadata_task` wrote processing flags (`metadata_scraped`, `metadata_resolved`) but had no review auto-clear in its write phase; added matching auto-clear logic before deferred task dispatch
- **Review Queue: AI Only Mode Skipped Deferred AI Enrichment** — selecting "AI Only" in the scrape modal ran AI in the main pipeline but did not dispatch the `ai_enrichment` deferred task, preventing the deferred auto-clear from firing; now dispatches AI enrichment for both `ai_auto` and `ai_only` modes
- **Review Queue: Scan AI Enrichment Re-Flagged Approved Items** — the `scan-enrichment` endpoint targeted items with `review_status` of both `"none"` and `"reviewed"`, causing items a user explicitly approved to be re-flagged on the next scan; now only targets `"none"` status
- **Review Queue: Deferred Auto-Clear Too Strict** — the auto-clear in all three deferred pipeline coordinators (`pipeline_lib`, `pipeline`, `pipeline_url`) required both `ai_enriched` AND `scenes_analyzed` unconditionally; now parses `review_reason` to check only the specific flags that were missing (e.g. "Missing scene analysis" only requires `scenes_analyzed`)

### Added
- **Review Queue: Scene Analysis in Scrape Modal** — added "Run scene analysis" checkbox (default: on) to the batch scrape options modal; the `scene_analysis` parameter flows through the API to `rescan_metadata_task` which conditionally includes it in the deferred task list
- **Review Queue: Scanned Category Auto-Clear** — items with `review_category = "scanned"` now auto-clear when `metadata_scraped` or `metadata_resolved` processing flags are set, across all three deferred coordinators and both scrape/rescan task finalisers

## [1.9.4] - 2026-04-08

### Fixed
- **Multi-Artist Display** — tracks with multiple artists (e.g. "Zedd; Hayley Williams") now display each artist as a separate clickable link in the Metadata panel instead of a single combined link; the Edit Track IDs modal shows per-artist MusicBrainz ID fields instead of one flat field
- **XML Sidecar Persistence on Library Rescan** — clearing the library and rescanning could lose scene analysis data and entity artwork because the XML sidecar was written before deferred tasks (scene analysis, entity artwork) completed; all three pipeline variants now rewrite the XML sidecar after deferred tasks finish, and scene analysis thumbnails are copied to the video folder for portability
- **Review Queue: Items Not Clearing After Batch Scrape** — review queue items flagged for missing enrichment (scene analysis, AI metadata) were not auto-cleared when a batch scrape resolved the underlying issue; deferred task coordinators now check processing flags on completion and clear the review flag when the issue is resolved
- **Review Queue: Misleading Enrichment Message** — the review reason "Partial AI Enrichment — missing: scene analysis" incorrectly implied AI was required for scene analysis; messages now use a simpler format (e.g. "Missing scene analysis", "Missing AI metadata, scene analysis")

## [1.9.3] - 2026-04-07

### Fixed
- **Library Scan: NULL Loudness & Lost Metadata** — three bugs in library scan import: `autoflush=False` caused loudness/quality writes to silently vanish; rescan destroyed existing source links; partial XML data could overwrite richer DB values. Added `_merge_existing_xml_quality()` helper for safe XML quality merging
- **Feat Artist Normalization: Band Name False Positives** — `parse_multi_artist` incorrectly split band names like "Mumford & Sons", "Coheed and Cambria", "Earth, Wind & Fire", "Iron & Wine" etc. into separate artists; added `_PROTECTED_NAMES` set and `"and the"` pattern guard alongside existing `"& The"` protection

### Added
- **Review Queue: AI Enrichment Categories** — two new review categories ("No AI Enrichment", "Partial AI Enrichment") with scan endpoint to flag tracks missing AI metadata; includes filter pills, help dialog rows, and Scan AI Enrichment button in the review queue
- **Feat → Semicolon Normalization** — artist strings like "DJ Snake feat. Lil Jon" are now normalized to "DJ Snake; Lil Jon" across all pipeline stages (import, rescan, scrape, AI); retroactively corrected 101 existing tracks with updated DB fields, artist_ids, and XML sidecars

### Changed
- **Scraper Tester: Download Log Redesign** — removed per-field comment inputs; enlarged Download Log button; added two-step download dialog with optional feedback for bug reports

## [1.9.2] - 2026-04-06

### Fixed
- **Review Queue: Tab Layout Overflow** — category stat cards could overflow their cells on narrower viewports; grid now scales to 12 columns at xl breakpoints and labels truncate cleanly instead of overflowing
- **Duplicate Review: Orphaned Partner After Deletion** — deleting one video from a duplicate pair correctly cleared the deleted item but left the surviving partner flagged as a duplicate; the survivor now has its review flags cleared automatically when no undismissed partners remain

### Added
- **Sidebar: GitHub Sponsors Link** — unobtrusive "Support the project" link added to the sidebar footer

## [1.9.1] - 2026-04-06

### Fixed
- **AI/Scrape Metadata: Stale Platform Data After Source Correction** — when a user corrected a video's source URL (e.g. from a live version to the studio version) and ran Scrape Metadata, AI Auto, or AI Only, the pipeline used cached platform metadata (title, description, tags, channel) from the original import URL instead of the corrected one; all scrape metadata operations now force-refresh platform metadata from yt-dlp on every run, ensuring the AI and scrapers receive context from the correct video
- **Source URL Edit: Stale Metadata Not Cleared** — editing a source's URL via the Sources panel did not clear the cached `platform_title`, `platform_description`, `platform_tags`, `channel_name`, and `upload_date` fields, leaving stale data from the old video; these fields are now cleared when the URL changes so the next scrape/AI operation fetches fresh data
- **Description: Unable to Clear** — clearing the description textarea and saving had no effect because the frontend converted the empty string to `null` (which the backend interprets as "don't change"); empty strings are now sent correctly, allowing the description to be cleared

## [1.9.0] - 2026-04-06

### Added
- **Canonical Track Linking System** — comprehensive hierarchical version relationship system: videos can be linked to canonical tracks (shared identity across versions), with parent-child version chains, confidence scoring, and provenance tracking (auto/user)
- **Canonical Track Panel Overhaul** — the canonical track card on the video detail page now supports inline editing of track metadata, scanning for matching canonical tracks, creating new canonical tracks, linking/unlinking, and displays parent video relationships and provenance badges
- **Canonical Track API** — new endpoints for scanning library for canonical matches (MBID → fingerprint → fuzzy fallback), linking/unlinking canonical tracks, creating/editing canonical tracks manually, setting parent video relationships, and library-wide canonical issue scanning
- **Review Queue: Canonical Categories** — three new review categories: "No Canonical Track" for unlinked videos, "Canonical Conflict" for metadata mismatches, and "Low Canonical Confidence" for uncertain auto-links
- **Version Types: Remix & Acoustic** — `remix` and `acoustic` are now first-class version types across the entire stack: version detector classifies them independently (previously grouped under "alternate"), badges render with distinct colours (cyan/amber), all dropdowns and filters include them, and they are preserved in XML export/import
- **Version Type Consistency** — all VERSION_TYPE_OPTIONS across the frontend (MetadataEditorForm, ReviewQueuePage, SettingsPage, Badges, ImportLibraryPage) are now consistent, including `uncensored` and `18+` in all applicable locations

## [1.8.1] - 2026-04-06

### Fixed
- **Library View: Database Schema Upgrade** — library view and track-level views failed to load because two new columns (`rename_dismissed`, `exclude_from_editor_scan`) were added to the VideoItem model but not registered in the startup schema upgrade function; existing databases now have these columns added automatically on startup

## [1.8.0] - 2026-04-06

### Added
- **Review Queue: Rename Dismiss & Scan Modes** — rename review items can now be dismissed so they don't re-flag on future scans; a "New / All" toggle on the Scan Renames button lets you choose between scanning only new mismatches (default) or re-scanning all files including previously dismissed items
- **Startup Rename Scan** — optional setting to automatically scan for naming convention mismatches when the server starts, populating the Review Queue; previously dismissed items are skipped
- **Settings: Rename Scan on Startup Toggle** — new toggle in Server settings to enable/disable automatic rename scanning at launch, with tooltip explaining behaviour

### Fixed
- **Queue: False "Stuck" Status on Completed Jobs** — completed jobs (redownloads, normalizations, metadata scrapes, exports) were incorrectly showing a red "Stuck" badge due to case-sensitive terminal step matching and overly broad finalizing detection; the "Stuck" status has been removed entirely and replaced with clear "Complete" / "Finalising" states scoped only to import/rescan pipelines that have genuine deferred post-processing tasks

### Removed
- **Settings: Bulk Rename Section** — removed the duplicate bulk rename UI from the Settings page; this functionality is better placed in the Review Queue where it already exists with single and batch actions

## [1.7.0] - 2026-04-06

### Added
- **Review Queue: Redownload Action** — individual and bulk "Redownload" buttons for review items with audio normalization failures, with confirmation warning about source link accuracy
- **New Videos: Dynamic Discovery** — all category generators now use yt-dlp YouTube search as a fallback when hardcoded seed entries are exhausted, giving access to YouTube's full catalogue; implemented New and Rising categories for trending and recently-released music video discovery

### Fixed
- **Review Queue: False Duplicate Groups** — items flagged because a duplicate import was skipped (the incoming file was rejected) no longer appear as "Duplicate Group — 1 items"; they are correctly categorised as Library Import Alerts instead
- **Scraper: Self-Titled Album Search** — `search_wikipedia_album` no longer penalises disambiguation pages when the album is self-titled (e.g. Weezer's *Weezer (Teal Album)*); similarity scoring now compares against disambiguation text for self-titled albums instead of the bare artist name
- **Scraper: AI Album Fallback in Cross-Fallback** — when the Wikipedia album search returns the artist page instead of the album page (common for self-titled albums), the cross-fallback path now retries with the AI-provided album name before giving up
- **Scraper: Two-Tier Cross-Link Validation** — cross-link artist validation now checks both the parsed artist name and the infobox artist field, reducing false rejections for compilations and featured-artist tracks
- **Scraper: MB→Wikidata→Wikipedia Artist Fallback** — when direct Wikipedia search fails for an artist, the scraper now falls back to MusicBrainz → Wikidata → Wikipedia URL resolution
- **Scraper: Tracklist Remixer Validation** — tracklist-based Wikipedia search now validates that remixer credits in parenthetical suffixes match the expected artist before accepting a track URL
- **Scraper: Slash-Delimited Track Splitting** — tracklist parser now correctly handles slash-delimited track titles (e.g. "Track A / Track B") without splitting on the slash
- **Scraper: EP-as-Album Type Discard** — Wikipedia album search no longer accepts EP-type releases when searching for a full album

## [1.6.0] - 2026-04-05

### Added
- **Update Checker** — app now checks GitHub for new releases at startup and displays a dismissible banner when an update is available, with a direct link to the release page

### Fixed
- **Batch Job Timeout** — replaced fixed 1-hour wall-clock deadline with a 30-minute inactivity timeout; large batch imports (1000+ videos) no longer time out while sub-jobs are still completing successfully

## [1.5.0] - 2026-04-04

### Fixed
- **Video Player: Native Fullscreen** — fullscreen playback modes (theatre and video-only) now use the native browser Fullscreen API (`requestFullscreen` / `exitFullscreen`) instead of CSS-only viewport fill, restoring true OS-level fullscreen that hides the taskbar and browser chrome. Exiting native fullscreen via Escape correctly syncs the playback store back to normal mode

## [1.4.1] - 2026-04-04

### Fixed
- **Video Player: Duration Display** — replaced native browser video controls with custom controls that use the stored `duration_seconds` from the database, fixing the bug where track length rendered incorrectly and adjusted as the video progressed
- **Duration Backfill** — added a one-shot startup task that populates `duration_seconds` via ffprobe for any existing tracks missing the value; subsequent startups skip it automatically

## [1.4.0] - 2026-04-04

### Fixed
- **Queue: 200-Job Cap** — backend API hard-capped job list at 200 items; large imports (1200+) showed no progress, no completed jobs, and maxed at 200 active. Raised limit to 10,000 and added server-side `offset` parameter for pagination
- **Album Artwork: Single Art Mislabeled as Album Art** — `search_album_musicbrainz()` accepted Single-type releases when the album name matched the single name (e.g. self-titled "Hero"), returning the single's CoverArtArchive art as album art. Now filters out Single-type releases while preserving EPs as valid album types
- **Album Artwork: Wikipedia Album Art Ignored** — pipeline-discovered Wikipedia album art (`source="wikipedia_album"`) was missing from `ALBUM_PRIORITY`, giving it priority 999 and always losing to any other source. Added to priority list

## [1.2.0] - 2026-04-04

### Added
- **Open Folder Buttons** — "Open in file explorer" buttons added to Default Directory, Source Directories, Archive Directory settings, and the Log Viewer toolbar
- **Archive Manifest System** — when a video is archived after editing, a `.playarr-archive.json` manifest is written alongside it containing MD5 checksum, original path, video ID, artist/title, and archive timestamp
- **Manifest-Based Archive Re-Linking** — `find_archive_file()` now performs a manifest-based search as a fallback, enabling archive re-linking even when the archive directory changes or files are reorganised
- **Generic Open Directory API** — new `POST /api/settings/open-directory` endpoint for opening any directory in the OS file manager
- **Log Directory API** — new `GET /api/jobs/logs/directory` endpoint returning the absolute log directory path

### Fixed
- **URL Pipeline Naming Convention** — videos downloaded via URL pipeline now obey the configured folder structure and file naming pattern settings (previously ignored, using hardcoded `Artist - Title [Quality]` flat structure)
- **Post-AI File Re-Organization** — `_re_organize_file()` in all three pipelines now uses `build_library_subpath()` with the actual library_dir setting, correctly producing nested folder structures (e.g. `Artist/VideoFolder`) instead of computing from `os.path.dirname(old_folder)`
- **Empty Parent Cleanup** — after re-organizing a file to a new nested path, empty parent directories left behind are cleaned up
- **Archive Restore Cleanup** — restoring from archive now removes the manifest file and empty archive subfolders

### Changed
- **pipeline_url/services/file_organizer.py** — replaced with a thin re-export shim delegating to `app.services.file_organizer`, eliminating code drift between pipelines

## [1.1.0] - 2026-04-04

### Added
- **Log Viewer** — new "Logs" tab in Settings with full log viewing, search/filter, syntax highlighting, download, and selection export
- **Clean Library: Redundant File Detection** — health check now detects mismatched/orphaned sidecar files (XML, NFO, posters, thumbnails) with one-click cleanup
- **New Videos: Per-Category Counts** — each discovery category can now have its own result count setting
- **New Videos: Expanded Seeds** — significantly expanded FAMOUS_SEEDS and POPULAR_SEEDS across all genre categories; removed stub categories
- **Library Scan: Poster Disk Discovery** — scan now discovers poster artwork from disk when not present in XML sidecars
- **Rescan from Disk** — added to bulk actions modal for batch re-scanning from existing files
- **Archive Folder Exclusion** — archive folders are now excluded from library scans
- **Star Ratings & Archive Restore** — star ratings preserved through pipeline; archive restore functionality

### Fixed
- **XML Sidecar Selection** — `find_playarr_xml()` now prefers XML matching the video file stem when multiple XMLs exist in a folder
- **NFO-Only Tracks** — library scan now restores poster artwork for tracks with only NFO files (no XML)
- **Entity Re-Linking on Scan** — scan now correctly re-links artist/album/genre entities from XML sidecar data
- **Tile Swap Rate** — Now Playing background grid tile swapping now correctly batches swaps to achieve the configured tiles-per-interval rate (previously clamped to ~5/sec)
- **CMD Popup Suppression** — all subprocess calls (ffmpeg, ffprobe, yt-dlp) use CREATE_NO_WINDOW to prevent console flashes
- **XML Export/Import Parity** — complete field coverage between export and re-import ensuring no metadata loss on rescan-from-disk
- **Entity Resolution Imports** — corrected import paths for entity resolution during rescan-from-disk operations
- **Rescan Finalization** — fixed stuck "Finalizing" state during rescan operations
- **New Videos Repopulation** — fixed suggestions not repopulating after downloads
- **Directory Management** — improved runtime directory creation and validation

### Changed
- Version bumped to 1.1.0

## [1.0.0] - 2026-03-15

Initial public release.
