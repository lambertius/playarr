# Playarr Concurrency & SQLite-Lock Diagnostic

Generated: 2026-03-14  
Scope: `library_import_video_task` (primary), `import_video_task` (secondary)  
Evidence source: code analysis + real log data from import session

---

## 1. Lock Ownership Map

### Lock Definitions

| # | Variable | Type | File | Line |
|---|----------|------|------|------|
| 1 | `_pipeline_lock` | `threading.Lock()` | backend/app/tasks.py | L94 |
| 2 | `_cancel_lock` | `threading.Lock()` | backend/app/worker.py | L68 |
| 3 | `TelemetryStore._lock` | `threading.Lock()` | backend/app/services/telemetry.py | L82 |
| 4 | `TelemetryStore._sub_lock` | `threading.Lock()` | backend/app/services/telemetry.py | L85 |
| 5 | `_download_semaphore` | `threading.Semaphore(1)` | backend/app/services/downloader.py | L137 |

### `_pipeline_lock` Acquisition Sites (THE BOTTLENECK)

#### Site A: `library_import_video_task` — First acquire (L4492)

- **Acquire:** L4492 → **Release:** L4584
- **Scope:** ~90 lines — duplicate re-check, create VideoItem + QualitySignature + Genre + Source, `db.flush()`, `db.commit()`
- **Contains:**
  - DB writes: YES (INSERT VideoItem, QualitySignature, Genre, Source, commit at L4579)
  - Network I/O: NO
  - AI: NO
  - ffmpeg: NO
  - Sleeps: NO
  - Retries: NO
  - File I/O: NO
  - Cosmetic helpers: YES (`_append_job_log`, `_set_pipeline_step`)

#### Site B: `library_import_video_task` — Second acquire (L4918)

- **Acquire:** L4918 → **Release:** L5565 (normal), L5674 (finally)
- **Scope:** ~650 lines — full advanced enrichment + entity writes + artwork + Kodi export
- **Contains:**
  - DB writes: YES (entities, canonical track, revisions, sources, media assets, genres, snapshot, export; commits at L4960, L4996, L5035, L5055, L5067, L5536, L5555)
  - Network I/O: **YES** — `download_entity_assets` (L4995), `process_artist_album_artwork` (L5020), `musicbrainzngs.get_release_by_id` (L5148, L5189), `_fetch_front_cover` (L5210), `_fetch_front_cover_by_release_group` (L5212), `download_image` (L5174, L5232), `search_imdb_music_video` (L5321), `search_wikipedia_artist` (L5411), `search_wikipedia_album` (L5436), `extract_album_wiki_url_from_single` (L5444)
  - AI: NO
  - ffmpeg: NO
  - Sleeps: **YES** — `time.sleep(1.1)` at L5148, L5189 (MusicBrainz rate limit)
  - Retries: NO (but cosmetic helpers have retries)
  - File I/O: YES (NFO write at L5505, Kodi export at L5544-5547)
  - Cosmetic helpers: YES — ~15 `_append_job_log` + ~10 `_set_pipeline_step` calls, each opening CosmeticSessionLocal

#### Site C: `import_video_task` — Single acquire (L1036)

- **Acquire:** L1036 → **Release:** L2025 (normal), L2166 (finally)
- **Scope:** ~990 lines — entity writes, artwork, metadata enrichment, poster upgrade, IMDB/Wikipedia, Kodi export, full VideoItem save
- **Contains:**
  - DB writes: YES (all entity + VideoItem + Source + QualitySignature + Genre + MediaAsset + MetadataSnapshot + NormalizationHistory; multiple commits)
  - Network I/O: **YES** — same pattern as Site B plus IMDB, Wikipedia artist/album searches
  - AI: NO
  - ffmpeg: NO
  - Sleeps: **YES** — `time.sleep(1.1)` x2 for MB rate limit + `time.sleep(1.0 * (_commit_attempt + 1))` at L2004 for commit retry
  - File I/O: YES (NFO, Kodi export)
  - Cosmetic helpers: YES

#### Site D: `redownload_video_task` — Single acquire (L2305)

- **Acquire:** L2305 → **Release:** L2501
- **Scope:** ~200 lines — ffprobe, loudness, file re-organization, audio normalization, NFO write, VideoItem update
- **Contains:**
  - DB writes: YES (VideoItem update, QualitySignature update, NormalizationHistory insert, commit)
  - Network I/O: NO
  - AI: NO
  - ffmpeg: **YES** — `extract_quality_signature` (ffprobe), `measure_loudness` (ffmpeg), `normalize_video` (ffmpeg)
  - Sleeps: NO
  - File I/O: YES (archive old folder, move new, artwork copy, NFO write)
  - Cosmetic helpers: YES

### Other Locks (Not Bottlenecks)

| Lock | Sites | Max Hold Time | Contains Heavy Work? |
|------|-------|---------------|---------------------|
| `_cancel_lock` | 3 (worker.py L77, L83, L89) | Microseconds | No — in-memory set ops only |
| `TelemetryStore._lock` | 10 (telemetry.py L90–L214) | Microseconds | No — in-memory dict/dataclass ops |
| `TelemetryStore._sub_lock` | 3 (telemetry.py L229–L243) | Microseconds | No — in-memory queue writes |
| `_download_semaphore` | 1 (downloader.py L322) | Minutes | Yes — full yt-dlp download subprocess; intentional serialize |

---

## 2. Per-Step Timing Breakdown — Real Import (Job 3, second run)

Source: `playarr.log` entry for Job 3, library import of "2 Unlimited - Get Ready For This"

| Step | Start | End | Duration | Lock Held? | DB Write? | Network? | CPU-Heavy? |
|------|-------|-----|----------|------------|-----------|----------|------------|
| Initial lock (create VideoItem) | 20:33:33.044 | 20:33:33.051 | **0.007s** | YES | YES (commit) | No | No |
| Pre-advanced: file copy, quality analysis, normalize, preview, YouTube match, yt-dlp meta | 20:33:33.051 | 20:35:03.163 | **~90s** | No | Minor (flags) | YES (yt-dlp, YouTube search) | YES (ffprobe, ffmpeg) |
| resolve_metadata_unified (AI + MB + Wikipedia + IMDB) | 20:35:03.163 | 20:35:09.328 | **~6s** | No | No | YES (MB, Wikipedia, IMDB, AI) | No |
| AI final review | 20:35:09.328 | 20:35:09.328 | **<1s** | No | No | No | No |
| Entity resolution (network phase) + sanitize | 20:35:09.328 | 20:35:56.434 | **~47s** | No | No | YES (MB, Wikipedia) | No |
| **WAIT for pipeline lock** | 20:35:56.434 | 20:39:47.887 | **231s (3m51s)** | WAITING | — | — | — |
| **Lock held: entity DB writes + artwork + enrichment + poster + sources + Kodi export** | 20:39:47.887 | 20:46:56.975 | **429s (7m9s)** | **YES** | YES (many commits) | YES (CAA, MB, Wikipedia, IMDB, image downloads) | No |
| Post-lock: orphan cleanup, matching, scene analysis, AI enrichment | 20:46:56.975 | (completed) | ~several min | No | YES | YES (AI API) | YES (ffmpeg scene detect) |

### Key Observations:

- **Lock wait: 3m51s** — Job 3 sat idle waiting for Job 2 to finish its 7+ minute locked section
- **Lock held: 7m9s** — All other threads blocked for this entire duration
- **Network I/O under lock: ~12-15 HTTP calls** to MusicBrainz, CoverArtArchive, Wikipedia, IMDB, plus image downloads
- **Forced sleeps under lock: 2.2s** — two `time.sleep(1.1)` for MB rate limiting
- **Effective throughput: 1 job per ~7 minutes** despite 4 worker threads
- **4 threads → 75% idle** — pipeline lock serializes all advanced enrichment phases

---

## 3. Database Session Model

### Session Factories

| Factory | Engine | busy_timeout | autoflush | expire_on_commit | Purpose |
|---------|--------|-------------|-----------|------------------|---------|
| `SessionLocal` | `engine` (main) | 30000ms | False | **False** | All essential pipeline data |
| `CosmeticSessionLocal` | `_cosmetic_engine` | 5000ms | False | True (default) | Job status, log_text, pipeline_steps |

### Session Lifecycle Per Task

```
library_import_video_task:
  db = SessionLocal()           # L4281 — created once at task start
  ... (all pipeline work)
  db.commit()                   # multiple commits throughout
  db.close()                    # L5676 — in finally block

_update_job / _append_job_log / _set_pipeline_step:
  cs = CosmeticSessionLocal()   # NEW session per call, per retry attempt
  cs.commit() or cs.rollback()
  cs.close()                    # always in finally
```

### Commit/Flush/Rollback Locations — `library_import_video_task`

| Operation | Lines |
|-----------|-------|
| `db.flush()` | L4540 |
| `db.commit()` | L4579, L4960, L4996, L5035, L5055, L5067, L5536, L5555, L5578, L5587, L5612, L5639, L5647, L5655 |
| `db.rollback()` | L4979, L5580, L5593, L5662, L5667 |
| `db.close()` | L5676 (finally) |

### Thread Safety

- **No sessions are shared across threads.** Each task/worker creates its own `SessionLocal()` at the top and closes in `finally`.
- **No ScopedSession or thread-local sessions** are used.
- `CosmeticSessionLocal` is created fresh on every `_update_job`/`_append_job_log`/`_set_pipeline_step` call.
- `download_asset` (in `metadata/assets.py`) creates its own separate `SessionLocal()` session.

### Cross-Thread Contention Pattern

```
Thread 1 (pipeline lock held):
  main db session → db.flush() → SQLite RESERVED lock held
  calls _append_job_log → CosmeticSessionLocal tries to write
    → BLOCKED by own main session's RESERVED lock → retries up to 10x

Thread 2 (pipeline lock NOT held):
  calls _append_job_log → CosmeticSessionLocal tries to write
    → BLOCKED by Thread 1's RESERVED lock → retries up to 10x
```

---

## 4. SQLite Engine/Config

### Main Engine (backend/app/database.py L17-24)

```python
engine = create_engine(
    settings.database_url,              # "sqlite:///./playarr.db"
    connect_args={"check_same_thread": False},
    echo=False,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=40,
    pool_timeout=120,
)
```

**PRAGMAs** (L35-42, via `@event.listens_for(engine, "connect")`):

| PRAGMA | Value | Notes |
|--------|-------|-------|
| `journal_mode` | WAL | Concurrent readers + single writer |
| `busy_timeout` | 30000 (30s) | Wait up to 30s for write lock |
| `synchronous` | NORMAL | Safe with WAL, reduces fsync |
| `foreign_keys` | ON | Enforce FK constraints + CASCADE |

**Pool class:** `QueuePool` (default; confirmed by pool_size/max_overflow params)

### Cosmetic Engine (L57-62)

```python
_cosmetic_engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=False,
    pool_pre_ping=True,
    # No pool_size/max_overflow → defaults: pool_size=5, max_overflow=10
)
```

**PRAGMAs** (L64-72):

| PRAGMA | Value | Notes |
|--------|-------|-------|
| `journal_mode` | WAL | Same as main |
| `busy_timeout` | 5000 (5s) | Lower than main — relies on application-level retry |
| `synchronous` | NORMAL | Same |
| `foreign_keys` | ON | Same |

### Key Observation

Two separate `QueuePool` instances hitting the same SQLite file. The main engine has pool_size=20 + max_overflow=40 = up to 60 connections. The cosmetic engine has defaults (5+10 = 15). SQLite allows only **one writer at a time** regardless of connection count — pool sizing is irrelevant for write serialization.

---

## 5. Write Frequency Map — One Advanced Library Import

### Essential Writes (Main SessionLocal `db`)

| # | Model | Operation | Frequency | Under Lock? |
|---|-------|-----------|-----------|-------------|
| 1 | VideoItem | INSERT | 1 | Lock #1 |
| 2 | VideoItem | UPDATE (metadata fields) | 2–3 | Lock #2 |
| 3 | QualitySignature | INSERT | 1 | Lock #1 |
| 4 | Genre + assoc | INSERT/UPDATE | 3–10 | Lock #1 + #2 |
| 5 | Source (NFO URL) | INSERT | 0–1 | Lock #1 |
| 6 | Source (YouTube match) | INSERT | 0–1 | Between locks |
| 7 | Source (Wikipedia single) | INSERT | 0–1 | Lock #2 |
| 8 | Source (IMDB) | INSERT | 0–1 | Lock #2 |
| 9 | Source (MB single) | INSERT | 0–1 | Lock #2 |
| 10 | Source (MB artist) | INSERT | 0–1 | Lock #2 |
| 11 | Source (MB album) | INSERT/UPDATE | 0–1 | Lock #2 |
| 12 | Source (Wikipedia artist) | INSERT | 0–1 | Lock #2 |
| 13 | Source (Wikipedia album) | INSERT | 0–1 | Lock #2 |
| 14 | ArtistEntity | INSERT/UPDATE | 1 | Lock #2 |
| 15 | AlbumEntity | INSERT/UPDATE | 0–1 | Lock #2 |
| 16 | TrackEntity | INSERT/UPDATE | 1 | Lock #2 |
| 17 | CanonicalTrack (TrackEntity) | INSERT/UPDATE | 1 | Lock #2 |
| 18 | MetadataRevision | INSERT | 1–2 | Lock #2 |
| 19 | MetadataSnapshot | INSERT | 1 | Lock #2 |
| 20 | MediaAsset | DELETE + INSERT | 2–6 | Lock #2 |
| 21 | CachedAsset | INSERT/UPDATE | 1–4 | Lock #2 (own session) |
| 22 | ExportManifest | INSERT/UPDATE | 3–6 | Lock #2 |
| 23 | MatchResult + MatchCandidate | INSERT | 3–16 | Post-lock |
| 24 | NormalizationResult | INSERT/UPDATE | 1 | Post-lock |
| 25 | AISceneAnalysis + AIThumbnail | INSERT | 0–12 | Post-lock |
| 26 | AIMetadataResult | INSERT/UPDATE | 0–2 | Post-lock |

**Total essential writes: ~25–70 per import**

### Cosmetic Writes (CosmeticSessionLocal)

| Helper | Model | Field | Frequency | Under Lock? |
|--------|-------|-------|-----------|-------------|
| `_update_job` | ProcessingJob | status, current_step, progress_percent, started_at, etc. | ~15–20 | Some under lock |
| `_append_job_log` | ProcessingJob | log_text (append) | ~30–50 | Some under lock |
| `_set_pipeline_step` | ProcessingJob | pipeline_steps (JSON append) | ~15–20 | Some under lock |

**Total cosmetic writes: ~60–90 per import, each opening/closing its own CosmeticSessionLocal**

---

## 6. Retry/Sleep Map

### Under Pipeline Lock

| # | Location | Duration | Purpose | Max Block |
|---|----------|----------|---------|-----------|
| 1 | tasks.py L5148 | `time.sleep(1.1)` | MB rate limit after `get_release_by_id` | 1.1s |
| 2 | tasks.py L5189 | `time.sleep(1.1)` | MB rate limit (poster guard) | 1.1s |
| 3 | tasks.py L2004 (`import_video_task` only) | `time.sleep(1.0 * (_commit_attempt + 1))` | Commit retry, 3 attempts | 6s total |

### Called From Under Lock (Cosmetic Helpers)

| # | Function | Location | Backoff | Max Attempts | Max Sleep Total | Called Under Lock? |
|---|----------|----------|---------|-------------|-----------------|-------------------|
| 4 | `_update_job` | tasks.py L126 | `1.0 * (attempt + 1)` | 10 | 45s | YES (some calls) |
| 5 | `_append_job_log` | tasks.py L167 | `1.0 * (attempt + 1)` | 10 | 45s | YES (some calls) |
| 6 | `_set_pipeline_step` | tasks.py L205 | `1.0 * (attempt + 1)` | 10 | 45s | YES (some calls) |

**Critical interaction:** When a cosmetic helper is called while the pipeline lock is held, and the main `db` session has a pending flush/commit (SQLite RESERVED lock), the CosmeticSessionLocal will contend with it — potentially triggering the full 45s retry cascade while the pipeline lock is held.

### Not Under Pipeline Lock

| # | Location | Duration | Purpose |
|---|----------|----------|---------|
| 7 | tasks.py L558 | 10/30/90/300/900s (+25% jitter) | Download retry backoff |
| 8 | metadata_resolver.py L261, L304, L370, L395, L459, L607, L717, L743, L909 | 1.1s each | MusicBrainz API rate limiting |
| 9 | artist_album_scraper.py L354, L397, L436 | 1.1s each | MB rate limit in artwork scraper |
| 10 | unified_metadata.py L245 | 1.1s | MB rate limit |
| 11 | metadata/providers/musicbrainz.py L35 | 1.1s | Global MB throttle |
| 12 | metadata/providers/coverartarchive.py L101 | 1.1s | MB search rate limit |
| 13 | metadata/assets.py L222 | `1.0 * (attempt + 1)`, 5 attempts | DB commit retry for entity assets |
| 14 | ai/final_review.py L387, L394 | 2s pre-call, 3s retry | AI provider rate limit |
| 15 | routers/library.py L412, L438, L471, L1187 | Various | OneDrive-aware file deletion retries |
| 16 | tasks.py L2711 | 3s poll, up to 3600s | Batch job completion polling |
| 17 | tasks.py L3039 | 1.1s | MB rate limit in scrape_metadata_task |

---

## 7. Execution Model

### Task Dispatch (backend/app/worker.py)

```
if Redis available:
    Celery .delay() → Celery prefork workers
else:
    threading.Thread(daemon=True) → in-process

Currently running: NO Redis → in-process threading mode
```

### Celery Configuration

| Setting | Value |
|---------|-------|
| `task_serializer` | json |
| `worker_prefetch_multiplier` | 1 (one task at a time per worker process) |
| `task_acks_late` | True |
| `task_track_started` | True |
| `task_default_retry_delay` | 30s |
| `task_max_retries` | 3 |
| `worker_concurrency` | Not set (defaults to CPU count) |
| Pool type | Not set (defaults to prefork) |
| Queues | Not set (all tasks use default queue) |

### Library Import Dispatch

`library_import_task` (L4170) creates child jobs, then dispatches:

| Mode | Dispatch Method | Concurrency |
|------|----------------|-------------|
| Celery (Redis) | `dispatch_task(library_import_video_task, job_id=child_id)` per video | Depends on worker count |
| Thread (no Redis) | `ThreadPoolExecutor(max_workers=min(N, 4))` at L4237 | **4 threads max** |

### Effective Architecture

```
library_import_task (parent, daemon thread)
  └─ ThreadPoolExecutor(max_workers=4)
       ├─ Thread 1: library_import_video_task (video A)
       ├─ Thread 2: library_import_video_task (video B)
       ├─ Thread 3: library_import_video_task (video C)
       └─ Thread 4: library_import_video_task (video D)
  └─ complete_batch_job_task (polls until all done)
```

But `_pipeline_lock` serializes the locked sections, so effective concurrency for the DB-write + enrichment phase = **1**.

### Import Task Architecture

| Task | Type | Sub-tasks? |
|------|------|-----------|
| `import_video_task` | **Monolithic** — download through final save in one task | No |
| `library_import_video_task` | **Monolithic** — file copy through final save in one task | No |
| `library_import_task` | Parent orchestrator | Spawns N `library_import_video_task` children + 1 `complete_batch_job_task` |

---

## 8. Import Task Call Outline — `library_import_video_task`

```
L4267  ─── TASK START ───────────────────────────────────────────────────
L4273  _update_job(status=analyzing)                           [COSMETIC DB]
L4281  db = SessionLocal()

       ─── PRE-LOCK PHASE (parallel-safe) ──────────────────────────────
L4318  Parse metadata from NFO/filename
L4380  extract_quality_signature()                             [CPU: ffprobe]
L4388  measure_loudness()                                      [CPU: ffmpeg]
L4396  Pre-check duplicates (read-only DB query)
L4410  Organize file (copy/move to library)                    [CPU: file I/O]
L4447  Copy artwork from source directory                      [FILE I/O]
L4470  normalize_video()                                       [CPU: ffmpeg]

       ─── LOCK #1 ACQUIRE (L4492) ─────────────────────────────────────
L4496  Duplicate re-check under lock (TOCTOU)                  [DB read]
L4510  CREATE VideoItem + QualitySignature + Genre + Source    [DB write]
L4540  db.flush()                                              [DB write]
L4579  db.commit()                                             [DB write]
       ─── LOCK #1 RELEASE (L4584) ─────────────────────────────────────

       ─── BETWEEN-LOCK PHASE ──────────────────────────────────────────
L4604  write_nfo_file()                                        [FILE I/O]
L4620  generate_preview()                                      [CPU: ffmpeg]
L4633  find_best_youtube_match()                               [NETWORK: YouTube]
L4684  get_available_formats() + extract_metadata_from_ytdlp() [NETWORK: yt-dlp]
L4711  resolve_metadata_unified()                              [NETWORK: Wikipedia, MB, AI]
L4762  generate_ai_summary()                                   [NETWORK: AI API]
L4804  detect_version_type()
L4890  resolve_artist/album/track (network phase)              [NETWORK: MB, Wikipedia]
L4915  sanitize_album()

       ─── LOCK #2 ACQUIRE (L4918) ─────────────────────────────────────
L4922  get_or_create_artist/album/track, canonical_track       [DB write]
L4960  db.commit() — entity creation                           [DB write]
L4995  download_entity_assets()                                [NETWORK + DB]
L5020  process_artist_album_artwork()                          [NETWORK: CAA, Wikipedia]
L5047  CachedAsset sync                                        [DB write]
L5093  Fill metadata from entities                             [DB write (in-memory)]
L5117  generate_ai_summary() if new plot                       [NETWORK: AI API]
L5148  musicbrainzngs.get_release_by_id() + sleep(1.1)        [NETWORK + SLEEP]
L5174  download_image() — scraper poster                       [NETWORK]
L5189  musicbrainzngs.get_release_by_id() + sleep(1.1)        [NETWORK + SLEEP]
L5210  _fetch_front_cover()                                    [NETWORK: CAA]
L5212  _fetch_front_cover_by_release_group()                   [NETWORK: CAA]
L5232  download_image() — poster upgrade                       [NETWORK]
L5256  Update VideoItem fields from resolved metadata          [DB write (in-memory)]
L5295  Source records: Wikipedia, IMDB, MB single/artist/album [NETWORK + DB]
       L5321  search_imdb_music_video()                        [NETWORK: IMDB]
       L5411  search_wikipedia_artist()                        [NETWORK: Wikipedia]
       L5436  search_wikipedia_album()                         [NETWORK: Wikipedia]
       L5444  extract_album_wiki_url_from_single()             [NETWORK: Wikipedia]
L5443  MediaAsset records                                      [DB write]
L5479  Genre update + NFO rewrite                              [DB + FILE I/O]
L5500  _save_metadata_snapshot()                               [DB write]
L5520  db.commit() — main advanced commit                      [DB write]
L5530  Kodi export (artist, album, video NFO files)            [FILE I/O + DB]
L5555  db.commit() — post-export commit                        [DB write]
       ─── LOCK #2 RELEASE (L5564) ─────────────────────────────────────

       ─── POST-LOCK PHASE ─────────────────────────────────────────────
L5569  cleanup_orphaned_entity()                               [DB write]
L5582  matching_resolve_video()                                [DB write]
L5597  _purge_stale_scene_data() + ai_analyze_scenes()         [CPU: ffmpeg + DB]
L5620  enrich_video_metadata() — AI enrichment                 [NETWORK: AI + DB]
L5653  Final db.commit() + _update_job(status=complete)        [DB write]

L5660  ─── EXCEPTION HANDLING ──────────────────────────────────────────
L5671  ─── FINALLY: release lock if held, db.close() ──────────────────
```

### `import_video_task` Outline (abbreviated)

```
L417   ─── TASK START ───────────────────────────────────────────────────
L450   db = SessionLocal()

       ─── PRE-LOCK PHASE ──────────────────────────────────────────────
L456   identify_provider() + canonicalize_url()
L476   Check existing + quality upgrade                        [NETWORK: yt-dlp]
L501   Download with retry (5 attempts, backoff)               [NETWORK + SLEEP]
L598   extract_quality_signature() + measure_loudness()        [CPU: ffprobe + ffmpeg]
L617   resolve_metadata_unified()                              [NETWORK: Wikipedia, MB, AI]
L765   detect_version_type()
L884   organize_file()
L908   normalize_video()                                       [CPU: ffmpeg]
L933   write_nfo_file()
L958   download poster/thumb                                   [NETWORK]
L986   resolve_artist/album/track (network)                    [NETWORK: MB, Wikipedia]

       ─── LOCK ACQUIRE (L1036) ────────────────────────────────────────
L1043  sanitize_album
L1055  Entity DB writes (get_or_create artist/album/track)     [DB write]
L1136  download_entity_assets()                                [NETWORK + DB]
L1160  process_artist_album_artwork()                          [NETWORK: CAA, Wikipedia]
L1268  Entity enrichment + MB RG lookup + sleep(1.1) x2       [NETWORK + SLEEP]
L1400  Poster upgrade via CAA                                  [NETWORK]
L1530  Match scoring                                           [DB]
L1563  Kodi export + db.commit()                               [FILE I/O + DB]
L1600  Full VideoItem save/update (Sources, QualitySig, etc.)  [NETWORK + DB]
       L1722  search_imdb_music_video()                        [NETWORK]
       L1834  search_wikipedia_artist()                        [NETWORK]
       L1871  search_wikipedia_album()                         [NETWORK]
L1997  db.commit() with 3-retry (sleep(1s, 2s, 3s))           [DB + SLEEP]
       ─── LOCK RELEASE (L2024) ────────────────────────────────────────

       ─── POST-LOCK ───────────────────────────────────────────────────
L2029  Orphan cleanup, matching, preview, scene analysis, AI   [CPU + NETWORK + DB]
```

---

## 9. Session 25 Architecture Redesign

Updated: Session 25

### Problem Summary

The Session 22 global serial deferred queue eliminated DB lock errors but caused
~14 minute total import time for 10 videos (all deferred tasks serialised).
Combined with Session 24's reduction from 4→2 Stage B workers, import throughput
was approximately halved compared to the original (broken) parallel approach.

Additionally, `ws.log()` wrote to `CosmeticSessionLocal` on **every log line**.
With 8+ concurrent threads, this created hundreds of competing short-lived write
transactions — the single largest source of SQLite write contention.

### New Architecture (Session 25)

```
library_import_task (parent, daemon thread)
  └─ ThreadPoolExecutor(max_workers=8)        ← was 2
       ├─ Thread 1–8: per-video staged pipeline
       │   ├── Stage A: coarse status updates (CosmeticSessionLocal)
       │   ├── Stage B: workspace build (file I/O only, no DB writes)
       │   ├── Stage C: apply_mutation_plan under _apply_lock (<2s)
       │   └── Stage D: dispatch_deferred → see below
       └─ complete_batch_job_task (polls until done)

dispatch_deferred(video_id) — per-video coordinator thread:
  └─ ThreadPoolExecutor(max_workers=4)
       ├─ preview            (pure ffmpeg I/O)
       ├─ kodi_export        (pure disk I/O)
       ├─ entity_artwork     (I/O: download images; DB: _apply_lock)
       ├─ scene_analysis     (ffmpeg + DB; busy_timeout + retry)
       ├─ matching           (MusicBrainz + DB; busy_timeout + retry)
       ├─ ai_enrichment      (AI API + DB; busy_timeout + retry)
       └─ orphan_cleanup     (DB; busy_timeout + retry)
  → ws.sync_logs_to_db()    (single bulk write)
  → ws.cleanup_on_success()
```

### Contention Model

| Writer | Lock/Strategy | Hold Time | Concurrent? |
|--------|---------------|-----------|-------------|
| Stage C (apply_mutation_plan) | `_apply_lock` (Python Lock) | <2s | No — serialised |
| Entity artwork DB | `_apply_lock` (Python Lock) | <100ms | No — waits for Stage C |
| Scene analysis | SQLite busy_timeout 30s + retry ×3 | ~5s | Yes |
| Matching | SQLite busy_timeout 30s + retry ×3 | ~5s | Yes |
| AI enrichment | SQLite busy_timeout 30s + retry ×3 | ~5-30s | Yes |
| Orphan cleanup | SQLite busy_timeout 30s + retry ×3 | <100ms | Yes |
| _coarse_update | CosmeticSessionLocal 5s + retry ×5 | ~10ms | Yes |
| ws.log() | **No DB writes** (file only) | — | — |
| ws.sync_logs_to_db() | CosmeticSessionLocal (single write) | ~50ms | 1 per job |

### Key Changes

1. **ws.log() DB writes removed** — Eliminated the ~60-90 per-import cosmetic
   writes that were the primary contention source. Logs write to files only.
   `sync_logs_to_db()` bulk-writes once after all deferred tasks complete.

2. **Global serial queue removed** — Replaced with per-video
   `ThreadPoolExecutor(max_workers=4)`. All deferred tasks run in parallel
   within each video AND across videos.

3. **Entity artwork DB phase under `_apply_lock`** — I/O phase (downloads) runs
   in parallel. DB writes (MediaAsset creation) acquire `_apply_lock` for
   <100ms, serialised with Stage C but not blocking I/O.

4. **Poster upgrade provenance check** — `library_source` posters no longer
   block CoverArtArchive upgrades. Only scraper/artwork_pipeline posters are
   kept.

5. **Stage B workers: 2→8** — With ws.log() contention removed and Stage C
   serialised via Python lock (not SQLite timeout), safe to run many parallel
   workspace builds.

### Expected Throughput

| Metric | Before (Session 24) | After (Session 25) |
|--------|---------------------|---------------------|
| Stage B workers | 2 | 8 |
| Deferred parallelism | Serial (global queue) | 4-way parallel per video |
| ws.log() DB writes/import | ~60-90 | 0 (file only) |
| DB sync writes/import | ~60-90 | 1 (bulk) |
| Entity artwork latency | 6-8 min (queue wait) | ~30s (parallel) |
| Total 10-video import | ~14 min | ~2-3 min (estimated) |

## 9. Deferability Inventory

Classification of each pipeline step in `library_import_video_task`:

| Step | Lines | Category | Rationale |
|------|-------|----------|-----------|
| Parse NFO/filename | L4318–4372 | **Blocking import** | Must identify the video before anything else |
| Extract quality signature | L4380–4386 | **Blocking import** | Needed for duplicate check + QualitySignature record |
| Measure loudness | L4388–4394 | **Safe to defer** | Only needed if normalizing; could run post-import |
| Pre-check duplicates | L4396–4407 | **Blocking import** | Must reject duplicates before creating VideoItem |
| Organize file (copy/move) | L4410–4444 | **Blocking import** | Must have file in library before VideoItem references it |
| Copy artwork from source | L4447–4466 | **Safe to defer** | Artwork can be added later |
| Audio normalization | L4470–4485 | **Safe to defer** | Can run post-import; video is playable without it |
| Create VideoItem + commit (Lock #1) | L4492–4584 | **Blocking import** | Core record must exist |
| Write NFO | L4604–4617 | **Safe to defer** | NFO is for Kodi/external consumers |
| Generate preview | L4620–4627 | **Safe to defer** | Preview thumbnail is cosmetic |
| YouTube source matching | L4633–4681 | **Safe to defer** | Source URL enrichment, not required for import |
| yt-dlp metadata fetch | L4684–4695 | **Safe to defer** | Enrichment data, not required for basic import |
| resolve_metadata_unified (AI+MB+Wiki) | L4711–4759 | **Safe to defer** | Metadata enrichment — import works without it |
| AI summary generation | L4762–4772 | **Safe to defer** | Cosmetic enrichment |
| Version detection | L4804–4873 | **Safe to defer** | Classification, not needed for basic import |
| Entity resolution (network) | L4890–4913 | **Safe to defer** | Entity linking can happen post-import |
| Entity DB writes (Lock #2) | L4918–4975 | **Safe to defer** (if entities are deferred) | Entity creation can be a separate phase |
| download_entity_assets | L4995–5005 | **Safe to defer** | Asset caching is enrichment |
| process_artist_album_artwork | L5020–5088 | **Safe to defer** | Artwork is enrichment |
| Metadata enrichment (MB RG, AI summary) | L5093–5160 | **Safe to defer** | Field enrichment from resolved entities |
| Poster upgrade (CAA/Wikipedia) | L5163–5253 | **Safe to defer** | Poster quality improvement |
| Update VideoItem fields | L5256–5292 | **Safe to defer** (if entities deferred) | Updates from entity resolution |
| Source records (IMDB, Wikipedia, MB) | L5295–5468 | **Safe to defer** | Source URL records are enrichment |
| MediaAsset records | L5443–5494 | **Safe to defer** | Asset tracking |
| Genre update + NFO rewrite | L5479–5505 | **Safe to defer** | Enriched NFO rewrite |
| MetadataSnapshot | L5500–5518 | **Safe to defer** | Undo support |
| Kodi export | L5530–5547 | **Safe to defer** | Export for external consumers |
| Orphan entity cleanup | L5569–5579 | **Safe to defer** | Housekeeping |
| Matching/confidence scoring | L5582–5594 | **Safe to defer** | Quality scoring |
| Scene analysis | L5597–5617 | **Safe to defer / Safe to parallelize** | Independent CPU work, no shared state |
| AI metadata enrichment | L5620–5650 | **Safe to defer / Safe to parallelize** | Independent AI call, no shared state |

### Parallelizability

| Category | Steps | Notes |
|----------|-------|-------|
| **Safe to parallelize** | Scene analysis, AI enrichment, preview generation, loudness measurement | Independent, no shared DB state |
| **Safe to defer (sequential)** | All enrichment (MB, Wikipedia, IMDB, CAA, artwork), all source records, NFO, Kodi export, matching | Can run as a separate post-import task |
| **Unsafe to parallelize** | Entity creation (get_or_create_*), VideoItem update, Source creation | TOCTOU risk — same artist/album across concurrent imports creates duplicates |
| **Blocking import** | Parse, quality sig, duplicate check, file organize, VideoItem INSERT | Must complete before import is "done" |

### Minimum Blocking Work per Import

Only these steps **must** complete for the import to succeed:
1. Parse NFO/filename
2. Extract quality signature (for duplicate check)
3. Duplicate check
4. Organize file
5. Create VideoItem + QualitySignature + Genre (from NFO) + Source (from NFO) + commit

**Everything else is enrichment that can be deferred.**

---

## 10. Example "database is locked" Traces

### Trace 1: 18:53:00 — `_append_job_log` fails while pipeline lock held by another thread

```
2026-03-14 18:52:29,506 - app.tasks - INFO - [Job 18] Waiting for pipeline lock (entity writes) …
2026-03-14 18:53:00,077 - app.tasks - ERROR - _append_job_log failed after 10 attempts:
    (sqlite3.OperationalError) database is locked
    [SQL: UPDATE processing_jobs SET log_text=?, updated_at=? WHERE processing_jobs.id = ?]
    [parameters: ("...[Job 20 log_text]...", '2026-03-14 10:52:59.448555', 20)]
```

**What was writing:** Job 20's main `SessionLocal` was inside its pipeline-locked section (acquired 18:52:08, released 18:54:27), performing entity creation + artwork downloads + CoverArtArchive fetches. The main session held a SQLite RESERVED lock during `db.commit()`.

**What was blocked:** Job 20's own `_append_job_log` call (from within the locked section) tried to write to ProcessingJob via `CosmeticSessionLocal`. The CosmeticSession's busy_timeout (5s) was insufficient — the main session's commit took longer. After 10 retry attempts (1+2+3+...+10 = 45s sleep total + 10 × 5s busy_timeout = 95s total wait), the helper gave up.

**Pattern:** Self-contention — the same thread's pipeline session blocks its own cosmetic helper session.

### Trace 2: 18:54:20 — Same pattern, 80 seconds later

```
2026-03-14 18:54:20,786 - app.tasks - ERROR - _append_job_log failed after 10 attempts:
    (sqlite3.OperationalError) database is locked
    [SQL: UPDATE processing_jobs SET log_text=?, updated_at=? WHERE processing_jobs.id = ?]
    [parameters: ("...[Job 20 log_text continues]...", '2026-03-14 10:54:20.150051', 20)]
```

**Context:** Same Job 20, still inside its pipeline-locked section (18:52:08 → 18:54:27). At this point, the main session is doing Wikipedia/IMDB source record creation + artwork downloads, generating SQLite write contention.

### Aggregate Statistics

| Metric | Value |
|--------|-------|
| Total "database is locked" errors | **153** |
| By `_append_job_log` | 136 (89%) |
| By `_set_pipeline_step` | 9 (6%) |
| By `_update_job` | 4/4 for specific jobs (3%) |
| Time span | 18:52 → 21:19 (~2.5 hours) |
| Rate | ~1 error/minute |

---

## Summary: Root Cause Chain

```
ThreadPoolExecutor(max_workers=4)
  → 4 threads run library_import_video_task concurrently
  → Pre-lock work runs in parallel (file copy, ffprobe, normalize)
  → Lock #1 (VideoItem creation): fast (~7ms), not a problem
  → Between-lock (metadata resolution): runs in parallel, ~2 minutes each
  → Lock #2 (entity writes + enrichment): 4-7 MINUTES each
       → 15+ HTTP calls under lock (CAA, MB, Wikipedia, IMDB, image downloads)
       → 2.2s forced sleep under lock (MB rate limiting)
       → 60-90 cosmetic DB writes under lock (_append_job_log etc.)
       → Cosmetic writes contend with main session → 45s retry cascades
  → Effective: 1 job every 5-7 minutes, 3 threads idle
  → 153 "database is locked" errors from cosmetic helper contention
```
