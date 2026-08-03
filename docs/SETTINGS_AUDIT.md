# Settings layout and wiring audit

## Scope

This audit covers every Settings navigation section, the global `AppSetting`
registry, the dedicated AI and New Videos settings APIs, server-backed UI
preferences, desktop launch behaviour, and the runtime consumers of values that
are cached in `app.config.Settings`.

## Information architecture

The Settings sidebar is organised around user intent rather than implementation
modules:

| Domain | Sections |
| --- | --- |
| Library | Library & files; Import defaults |
| Media | Video & audio; Previews |
| Intelligence | AI providers & policy; New Videos; Community metadata |
| Playback | Now Playing; Party Mode; TV Mode; Cast Mode |
| System | Server; Startup & behaviour; Server management; Logs & diagnostics |

About remains separated at the foot of navigation. Kodi configuration is not
present because the add-on has been removed. Each section now has a concise
scope explanation and each practical control/action uses the same responsive
`Field name | Field entry/action | Field description` row.

## Persistence and consumers

| Store | Ownership | Main consumers |
| --- | --- | --- |
| `AppSetting` core registry | Library paths/naming, import defaults, media processing, previews, TMVDB, startup, server | Import pipeline, file organiser, preview generator, normaliser, playback transcoder, download scheduler, desktop launcher |
| AI settings API | Provider credentials, model policy, enrichment fields, prompts | AI provider selection, enrichment, verification, scene analysis |
| New Videos settings API | Feed, recommendation, cart and category policy | New Videos feed policy and recommendation service |
| `/api/preferences` typed registry | Layouts, sorting, Party/TV/Cast/Now Playing presentation | Frontend stores and navigation surfaces across devices |

Core definitions now name concrete consumers. The catalogue reports three
different classes instead of treating every non-core database key as an orphan:
externally managed typed settings, deprecated legacy keys, and genuinely unknown
keys. Registry tests fail if a visible core setting has no declared consumer.

## Defects corrected by this audit

- `startup_rename_scan` was rendered and writable but absent from the core
  registry. It is now registered, catalogued, defaulted, and linked to the
  startup review scanner.
- `server.port` was saved but both launchers still hard-coded port 6969. The
  production and fallback launchers now read the saved database value; an
  explicit `PLAYARR_PORT` or command-line value still wins.
- Normalisation and preview settings were persisted but long-running services
  read the cached config singleton. Core updates now apply typed values
  immediately, and all config-backed values are hydrated from the database on
  restart.
- The obsolete `party_mode_exclusions` core default duplicated the typed
  `partyExclusions` preference group. It is no longer emitted as a setting and
  is recognised only as a deprecated database key for migration diagnostics.
- Naming preview requests used a state initializer for side effects and left
  uncancelled timers. They now use a cancellable debounced effect.
- Startup, naming, maintenance, restart, version, and downloader controls used
  bespoke two-column layouts. They now use the shared three-column row.

## Verification invariants

- Core setting definitions and concrete consumer mappings have exactly the same
  key set.
- The catalogue cannot report a visible core setting without consumers.
- Dedicated AI/New Videos keys, deprecated keys, and unknown keys are classified
  independently.
- A config-backed update changes the live cached value immediately.
- The shared settings row has a responsive single-column fallback and a
  three-column layout where the viewport can support it.
