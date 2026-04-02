# Scraper Consolidation Plan

## Purpose

Map all current scraping pathways, identify duplicated code, and define a plan
to extract a **common scraper component** so that all pathways produce
consistent results without independent maintenance.

---

## 1. Current Pathways Overview

There are **five distinct entry points** that trigger scraping:

| # | Pathway | Entry Point | Pipeline Used | Scraping Modules |
|---|---------|------------|---------------|-----------------|
| 1 | **Scraper Tester** | `POST /api/scraper-test/run` | `pipeline_url` services | `unified_metadata`, `metadata_resolver`, `artist_album_scraper` |
| 2 | **URL Import** | `import_video_task()` → `run_url_import_pipeline()` | `pipeline_url` stages | `unified_metadata`, `metadata_resolver`, `artist_album_scraper` |
| 3 | **Rescan Metadata** | `rescan_metadata_task()` | `pipeline_url` (direct call) | `unified_metadata`, `metadata_resolver` |
| 4 | **Scrape Metadata** | `scrape_metadata_task()` | `pipeline_url` (direct call) | `unified_metadata`, `metadata_resolver` |
| 5 | **Library Import** | `library_import_video_task()` → `run_library_import_pipeline()` | `pipeline_lib` stages | `unified_metadata`, `metadata_resolver`, `artist_album_scraper` |

### How each pathway currently uses scraping:

```
                        ┌─────────────────────────┐
                        │    Scraper Tester (1)    │
                        │  routers/scraper_test.py │
                        └──────────┬──────────────┘
                                   │ imports from pipeline_url
                                   ▼
┌───────────────┐    ┌──────────────────────────────────┐
│ URL Import (2)│───►│  pipeline_url/services/           │
│ stages.py     │    │    unified_metadata.py   (2466L) │
├───────────────┤    │    metadata_resolver.py  (1968L) │
│ Rescan    (3) │───►│    artist_album_scraper.py (918L)│
│ tasks.py      │    │    source_validation.py   (527L) │
├───────────────┤    │  pipeline_url/ai/                │
│ Scrape    (4) │───►│    source_resolution.py   (668L) │
│ tasks.py      │    │    final_review.py        (749L) │
└───────────────┘    └──────────────────────────────────┘

┌───────────────┐    ┌──────────────────────────────────┐
│ Library    (5)│───►│  pipeline_lib/services/           │
│ stages.py     │    │    unified_metadata.py   (2466L) │ ← DUPLICATE
│               │    │    metadata_resolver.py  (1968L) │ ← DUPLICATE
│               │    │    artist_album_scraper.py (918L)│ ← DUPLICATE
│               │    │    source_validation.py   (527L) │ ← DUPLICATE
│               │    │  pipeline_lib/ai/                │
│               │    │    source_resolution.py   (668L) │ ← DUPLICATE
│               │    │    final_review.py        (749L) │ ← DUPLICATE
└───────────────┘    └──────────────────────────────────┘
```

---

## 2. Complete File Duplication Inventory

### 2a. Scraper-Core Files (duplicated between pipeline_url and pipeline_lib)

These files contain the **scraping logic** and are the primary consolidation targets:

| File | pipeline_url | pipeline_lib | Lines | Purpose |
|------|:-----------:|:-----------:|:-----:|---------|
| `unified_metadata.py` | ✓ | ✓ (copy) | 2,466 | Central scraping orchestrator: AI source resolution → MB search → Wiki search → cross-fallback → AI final review |
| `metadata_resolver.py` | ✓ | ✓ (copy) | 1,968 | Wikipedia search/scrape, MusicBrainz search, IMDB search, title parsing, infobox extraction |
| `artist_album_scraper.py` | ✓ | ✓ (copy) | 918 | Dedicated artwork scraping for artists, albums, singles via Wikipedia/MusicBrainz Cover Art Archive |
| `source_validation.py` | ✓ | ✓ (copy) | 527 | Album name sanitization, source URL validation |
| `ai/source_resolution.py` | ✓ | ✓ (copy) | 668 | AI pre-scrape: guesses Wikipedia/MB URLs from video context |
| `ai/final_review.py` | ✓ | ✓ (copy) | 749 | AI post-scrape: validates and corrects scraped metadata |

**Total duplicated scraper code: ~7,296 lines × 2 copies = 14,592 lines maintained**

### 2b. Pipeline-Infrastructure Files (duplicated but NOT scraper-related)

These are pipeline orchestration/DB files — same code, different pipeline context:

| File | pipeline_url | pipeline_lib | Lines | Purpose |
|------|:-----------:|:-----------:|:-----:|---------|
| `stages.py` | ✓ | ✓ (different) | ~2,000 | Pipeline stage orchestration (different stage lists) |
| `db_apply.py` | ✓ | ✓ (copy) | ~600 | Atomic DB mutation application |
| `mutation_plan.py` | ✓ | ✓ (copy) | ~400 | Mutation plan builder |
| `deferred.py` | ✓ | ✓ (copy) | ~300 | Deferred task execution |
| `workspace.py` | ✓ | ✓ (copy) | ~200 | Workspace path management |
| `write_queue.py` | ✓ only | — | ~200 | SQLite write serialization |

### 2c. Non-Scraper Service Files (duplicated)

| File | pipeline_url | pipeline_lib | Purpose |
|------|:-----------:|:-----------:|---------|
| `ai_summary.py` | ✓ | ✓ (copy) | AI metadata summarization |
| `artwork_manager.py` | ✓ | ✓ (copy) | Artwork download/apply |
| `artwork_service.py` | ✓ | ✓ (copy) | Artwork validation |
| `canonical_track.py` | ✓ | ✓ (copy) | Canonical track linking |
| `downloader.py` | ✓ | ✓ (copy) | yt-dlp wrapper |
| `file_organizer.py` | ✓ | ✓ (copy) | File naming/organization |
| `media_analyzer.py` | ✓ | ✓ (copy) | FFprobe media analysis |
| `nfo_parser.py` | ✓ | ✓ (copy) | NFO file parsing/writing |
| `normalizer.py` | ✓ | ✓ (copy) | Audio LUFS normalization |
| `preview_generator.py` | ✓ | ✓ (copy) | Preview thumbnail generation |

### 2d. Pipeline-Exclusive Files (NOT shared)

| File | Location | Purpose |
|------|----------|---------|
| `duplicate_detection.py` | pipeline_url only | Duplicate video detection |
| `url_utils.py` | pipeline_url only | Provider identification, URL canonicalization |
| `filename_parser.py` | pipeline_lib only | Artist/title extraction from filenames |
| `youtube_matcher.py` | pipeline_lib only | Match library files to YouTube sources |

### 2e. Top-Level app/services/ (original copies — largely unused)

`backend/app/services/` contains 22 files that were the originals before the
AUTO-SEPARATION. Pipelines import from their own copies instead. These are
effectively dead code (except `url_utils.py` and `downloader.py` which are
imported by `scraper_test.py` and `tasks.py` directly).

### 2f. Legacy pipeline/ Directory (dead code)

`backend/app/pipeline/` contains 6 files that are only imported internally.
No task or router references this pipeline. It should be removed during cleanup.

---

## 3. Pathway-Specific Scraping Flows

### 3a. Scraper Tester

**File:** `backend/app/routers/scraper_test.py` (1,187 lines)

```
User Input: URL + optional overrides
    │
    ├─ 1. URL validation ─────────── app.services.url_utils
    │     identify_provider(), canonicalize_url(), is_playlist_url()
    │
    ├─ 2. yt-dlp metadata ────────── app.services.downloader
    │     get_available_formats(), extract_metadata_from_ytdlp()
    │
    ├─ 3. Title parsing ──────────── app.pipeline_url.services.metadata_resolver
    │     extract_artist_title(), clean_title()
    │
    ├─ 4. Unified metadata ───────── app.pipeline_url.services.unified_metadata
    │     resolve_metadata_unified()
    │     ├─ AI source resolution
    │     ├─ MusicBrainz search/lookup
    │     ├─ Wikipedia search/scrape
    │     ├─ Cross-fallback (MB↔Wiki)
    │     ├─ AI final review
    │     └─ Returns: metadata + _source_urls + _artwork_candidates
    │
    ├─ 5. Artwork collection ─────── app.pipeline_url.services.artist_album_scraper
    │     get_artist_artwork(), get_album_artwork_musicbrainz(),
    │     get_album_artwork_wikipedia()
    │
    └─ 6. Artwork scoring ────────── inline in scraper_test.py
          Phase 1: final_image match → Phase 2: best-per-type selection
          Returns: scored candidates with "Chosen" markers
```

**Key imports from pipeline_url:**
- `unified_metadata.resolve_metadata_unified`
- `metadata_resolver.extract_artist_title`, `clean_title`
- `artist_album_scraper.get_artist_artwork`, `get_album_artwork_musicbrainz`, `get_album_artwork_wikipedia`

**Key imports from app.services (shared):**
- `url_utils.identify_provider`, `canonicalize_url`, `is_playlist_url`
- `downloader.get_available_formats`, `extract_metadata_from_ytdlp`

### 3b. URL Import

**File:** `backend/app/pipeline_url/stages.py`

```
Video URL
    │
    ├─ Stage A: Download & analyze (pipeline-specific)
    │     downloader, media_analyzer, file_organizer
    │
    ├─ Stage B6: Resolve metadata ── resolve_metadata_unified()
    │     (identical call to scraper tester step 4)
    │
    ├─ Stage B11: Version detection
    │
    ├─ Stage B12: Entity resolution ── metadata/resolver.py
    │     resolve_artist(), resolve_album(), resolve_track()
    │     Uses: MusicBrainzProvider, WikipediaProvider, CoverArtArchiveProvider
    │
    ├─ Stage B13: Source link collection
    │     Uses _source_urls from unified metadata + independent Wikipedia search
    │     Calls: search_wikipedia_artist(), search_wikipedia_album()
    │
    ├─ Stage B14: Artwork fetch
    │     get_artist_artwork(), get_album_artwork_musicbrainz(),
    │     get_album_artwork_wikipedia()
    │
    └─ Stage C: DB write (pipeline-specific)
          db_apply, mutation_plan, write_queue
```

### 3c. Rescan Metadata

**File:** `backend/app/tasks.py` → `rescan_metadata_task()`

```
Existing VideoItem
    │
    ├─ Phase A (network I/O, no DB lock):
    │   ├─ Load context (artist, title, locked_fields, MB IDs)
    │   ├─ Backfill platform metadata (yt-dlp if needed)
    │   ├─ resolve_metadata_unified()  ← from pipeline_url
    │   ├─ Pre-compute field values (respects locked_fields)
    │   ├─ Write NFO
    │   ├─ Download poster
    │   └─ Collect source links
    │
    └─ Phase B (atomic DB write):
        └─ Apply all mutations
```

### 3d. Scrape Metadata (Manual)

**File:** `backend/app/tasks.py` → `scrape_metadata_task()`

```
Existing VideoItem + mode flags
    │
    ├─ Mode: AI Auto Analyse
    │   └─ resolve_metadata_unified()  ← from pipeline_url
    │       Results → proposed changes (user review queue)
    │
    ├─ Mode: AI Only
    │   └─ enrich_video_metadata()  ← AI enrichment only
    │
    ├─ Mode: Scrape Wikipedia
    │   ├─ User URL → scrape_wikipedia_page()
    │   └─ OR search → search_wikipedia() → scrape_wikipedia_page()
    │       Both from pipeline_url.services.metadata_resolver
    │
    ├─ Mode: Scrape MusicBrainz
    │   ├─ User URL → musicbrainzngs.get_recording_by_id()
    │   └─ OR search → search_musicbrainz()
    │       From pipeline_url.services.metadata_resolver
    │
    └─ Artwork pipeline (runs after all modes):
        get_album_artwork_musicbrainz(), get_album_artwork_wikipedia(),
        get_artist_artwork()
```

### 3e. Library Import

**File:** `backend/app/pipeline_lib/stages.py`

```
Local file path
    │
    ├─ Stage A: Parse identity from filename/NFO (pipeline-specific)
    │     filename_parser, nfo_parser, youtube_matcher
    │
    ├─ Stage B10: Resolve metadata ── resolve_metadata_unified()
    │     (identical algorithm to pipeline_url, but from pipeline_lib copy)
    │
    ├─ Stage B11: Version detection
    │
    ├─ Stage B12: Entity resolution ── pipeline_lib/metadata/resolver.py
    │
    ├─ Stage B13: Source link collection
    │     Uses _source_urls from unified metadata + independent search
    │
    ├─ Stage B14: Artwork fetch
    │     get_artist_artwork(), get_album_artwork_*()
    │
    └─ Stage C: DB write (pipeline-specific)
```

---

## 4. Function-Level Mapping: Who Calls What

### 4a. Core Scraper Functions (should become shared)

| Function | Module | Called By |
|----------|--------|----------|
| `resolve_metadata_unified()` | `unified_metadata.py` | Scraper Tester, URL Import, Rescan, Scrape Metadata, Library Import |
| `search_wikipedia()` | `metadata_resolver.py` | `unified_metadata` (internally), Scrape Metadata task (directly) |
| `scrape_wikipedia_page()` | `metadata_resolver.py` | `unified_metadata` (internally), Scrape Metadata task (directly) |
| `detect_article_mismatch()` | `metadata_resolver.py` | `unified_metadata` (internally) |
| `search_musicbrainz()` | `metadata_resolver.py` | `unified_metadata` (internally), Scrape Metadata task (directly) |
| `search_imdb_music_video()` | `metadata_resolver.py` | `unified_metadata` (internally) |
| `extract_artist_title()` | `metadata_resolver.py` | Scraper Tester, URL Import, Rescan, Scrape Metadata |
| `clean_title()` | `metadata_resolver.py` | Scraper Tester, URL Import, Scrape Metadata |
| `capitalize_genre()` | `metadata_resolver.py` | `unified_metadata` (internally) |
| `search_wikipedia_artist()` | `metadata_resolver.py` | URL Import (source links), Library Import (source links) |
| `search_wikipedia_album()` | `metadata_resolver.py` | URL Import (source links), Library Import (source links) |
| `extract_wiki_infobox_links()` | `metadata_resolver.py` | `unified_metadata` (cross-fallback) |
| `extract_album_wiki_url_from_single()` | `metadata_resolver.py` | `unified_metadata` (cross-fallback) |
| `extract_single_wiki_url_from_album()` | `metadata_resolver.py` | `unified_metadata` (cross-fallback) |
| `extract_artist_wiki_url_from_page()` | `metadata_resolver.py` | `unified_metadata` (cross-fallback) |
| `get_artist_artwork()` | `artist_album_scraper.py` | Scraper Tester, URL Import, Scrape Metadata, Library Import |
| `get_album_artwork_musicbrainz()` | `artist_album_scraper.py` | Scraper Tester, URL Import, Scrape Metadata, Library Import |
| `get_album_artwork_wikipedia()` | `artist_album_scraper.py` | Scraper Tester, URL Import, Scrape Metadata, Library Import |
| `resolve_sources_with_ai()` | `ai/source_resolution.py` | `unified_metadata` (internally) |
| `run_final_review()` | `ai/final_review.py` | `unified_metadata` (internally) |

### 4b. Pipeline-Specific Functions (stay in pipeline dirs)

| Function | Module | Used By |
|----------|--------|---------|
| `run_url_import_pipeline()` | `pipeline_url/stages.py` | URL Import task only |
| `run_library_import_pipeline()` | `pipeline_lib/stages.py` | Library Import task only |
| `resolve_artist/album/track()` | `pipeline_*/metadata/resolver.py` | Entity resolution stage |
| `detect_duplicates()` | `pipeline_url/services/duplicate_detection.py` | URL Import only |
| `parse_filename()` | `pipeline_lib/services/filename_parser.py` | Library Import only |
| `match_to_youtube()` | `pipeline_lib/services/youtube_matcher.py` | Library Import only |

### 4c. Already-Shared Functions

| Function | Module | Used By |
|----------|--------|---------|
| `identify_provider()` | `app/services/url_utils.py` | Scraper Tester, URL Import |
| `canonicalize_url()` | `app/services/url_utils.py` | Scraper Tester, URL Import |
| `is_playlist_url()` | `app/services/url_utils.py` | Scraper Tester |
| `get_available_formats()` | `app/services/downloader.py` | Scraper Tester |
| `extract_metadata_from_ytdlp()` | `app/services/downloader.py` | Scraper Tester, URL Import |

---

## 5. Identified Divergence Points

These are areas where pathways currently (or recently) diverged, causing
inconsistent scraping results:

| # | Divergence | Scraper Tester | pipeline_url | pipeline_lib |
|---|-----------|:-:|:-:|:-:|
| 1 | `db` param to `resolve_metadata_unified()` | ✓ passes db | ✓ (just fixed) | ✓ (just fixed) |
| 2 | Cross-fallback (MB↔Wiki) | ✓ | ✓ | ✓ (just ported) |
| 3 | `_source_urls` in unified metadata | ✓ | ✓ | ✓ (just ported) |
| 4 | `_artwork_candidates` tracking | ✓ | ✓ | ✓ (just ported) |
| 5 | Dual A-side "/" handling in search_wikipedia | ✓ | ✓ | ✓ (just ported) |
| 6 | "NEITHER artist NOR title" penalty | ✓ | ✓ | ✓ (just added) |
| 7 | `extract_wiki_infobox_links()` function | ✓ | ✓ | ✓ (just added) |
| 8 | `_source_urls` preference in source link collection | ✓ (N/A) | ✓ (just fixed) | ✓ (just fixed) |
| 9 | Artwork Phase 1/2 "Chosen" guards | ✓ (just fixed) | N/A (different) | N/A (different) |

All divergences marked "just fixed/ported/added" were resolved in the current
and immediately preceding sessions. The risk is that future changes to
scraper_test.py must be manually replicated to two pipeline copies.

---

## 6. Proposed Consolidation Architecture

### 6a. New Shared Module: `app/scraper/`

Extract all scraper-core code into a single shared module:

```
backend/app/
├── scraper/                          ← NEW shared scraper component
│   ├── __init__.py
│   ├── unified_metadata.py           ← FROM pipeline_url/services/ (2,466L)
│   ├── metadata_resolver.py          ← FROM pipeline_url/services/ (1,968L)
│   ├── artist_album_scraper.py       ← FROM pipeline_url/services/ (918L)
│   ├── source_validation.py          ← FROM pipeline_url/services/ (527L)
│   └── ai/
│       ├── __init__.py
│       ├── source_resolution.py      ← FROM pipeline_url/ai/ (668L)
│       └── final_review.py           ← FROM pipeline_url/ai/ (749L)
│
├── pipeline_url/                     ← Pipeline-specific orchestration ONLY
│   ├── stages.py                     ← Imports from app.scraper.*
│   ├── db_apply.py
│   ├── mutation_plan.py
│   ├── deferred.py
│   ├── workspace.py
│   ├── write_queue.py
│   ├── metadata/                     ← Entity resolution (might share later)
│   │   └── resolver.py
│   └── services/                     ← Pipeline-specific helpers only
│       ├── downloader.py             ← Already shared via app.services
│       ├── duplicate_detection.py    ← URL-pipeline exclusive
│       ├── file_organizer.py
│       ├── media_analyzer.py
│       └── ...non-scraper services
│
├── pipeline_lib/                     ← Pipeline-specific orchestration ONLY
│   ├── stages.py                     ← Imports from app.scraper.*
│   ├── db_apply.py
│   ├── ...
│   └── services/
│       ├── filename_parser.py        ← Lib-pipeline exclusive
│       ├── youtube_matcher.py        ← Lib-pipeline exclusive
│       └── ...non-scraper services
│
├── routers/
│   └── scraper_test.py               ← Imports from app.scraper.*
│
└── tasks.py                          ← Imports from app.scraper.*
```

### 6b. What Moves to `app/scraper/`

| File | From | Lines | Contains |
|------|------|:-----:|----------|
| `unified_metadata.py` | `pipeline_url/services/` | 2,466 | `resolve_metadata_unified()` — the central scraping orchestrator |
| `metadata_resolver.py` | `pipeline_url/services/` | 1,968 | `search_wikipedia()`, `scrape_wikipedia_page()`, `search_musicbrainz()`, `search_imdb_music_video()`, `extract_artist_title()`, `clean_title()`, `extract_wiki_infobox_links()`, `search_wikipedia_artist()`, `search_wikipedia_album()`, all infobox extraction functions |
| `artist_album_scraper.py` | `pipeline_url/services/` | 918 | `get_artist_artwork()`, `get_album_artwork_musicbrainz()`, `get_album_artwork_wikipedia()` |
| `source_validation.py` | `pipeline_url/services/` | 527 | `sanitize_album()`, source URL validation |
| `ai/source_resolution.py` | `pipeline_url/ai/` | 668 | `resolve_sources_with_ai()`, `SourceResolutionResult` |
| `ai/final_review.py` | `pipeline_url/ai/` | 749 | `run_final_review()`, `FinalReviewResult` |

**Total: ~7,296 lines consolidated from 12 copies → 6 files (one copy)**

### 6c. Import Changes Required

#### scraper_test.py (4 import changes)
```python
# BEFORE:
from app.pipeline_url.services.metadata_resolver import extract_artist_title, clean_title
from app.pipeline_url.services.unified_metadata import resolve_metadata_unified
from app.pipeline_url.services.artist_album_scraper import get_artist_artwork
from app.pipeline_url.services.artist_album_scraper import get_album_artwork_musicbrainz, get_album_artwork_wikipedia

# AFTER:
from app.scraper.metadata_resolver import extract_artist_title, clean_title
from app.scraper.unified_metadata import resolve_metadata_unified
from app.scraper.artist_album_scraper import get_artist_artwork
from app.scraper.artist_album_scraper import get_album_artwork_musicbrainz, get_album_artwork_wikipedia
```

#### tasks.py (3 import changes)
```python
# BEFORE:
from app.pipeline_url.services.unified_metadata import resolve_metadata_unified
from app.pipeline_url.services.metadata_resolver import (search_musicbrainz, ...)

# AFTER:
from app.scraper.unified_metadata import resolve_metadata_unified
from app.scraper.metadata_resolver import (search_musicbrainz, ...)
```

#### pipeline_url/stages.py (~8 import changes)
```python
# BEFORE:
from app.pipeline_url.services.unified_metadata import resolve_metadata_unified
from app.pipeline_url.services.metadata_resolver import (search_wikipedia_artist, ...)
from app.pipeline_url.services.artist_album_scraper import (get_artist_artwork, ...)

# AFTER:
from app.scraper.unified_metadata import resolve_metadata_unified
from app.scraper.metadata_resolver import (search_wikipedia_artist, ...)
from app.scraper.artist_album_scraper import (get_artist_artwork, ...)
```

#### pipeline_lib/stages.py (~8 import changes)
Same pattern — replace `app.pipeline_lib.services.*` → `app.scraper.*`

#### unified_metadata.py internal imports (2 changes)
```python
# BEFORE (in pipeline_url copy):
from app.pipeline_url.services.metadata_resolver import (...)
from app.pipeline_url.ai.source_resolution import (...)
from app.pipeline_url.ai.final_review import (...)

# AFTER (in app/scraper/):
from app.scraper.metadata_resolver import (...)
from app.scraper.ai.source_resolution import (...)
from app.scraper.ai.final_review import (...)
```

### 6d. What Gets Deleted After Migration

| Files to Delete | Count |
|----------------|:-----:|
| `pipeline_url/services/unified_metadata.py` | 1 |
| `pipeline_url/services/metadata_resolver.py` | 1 |
| `pipeline_url/services/artist_album_scraper.py` | 1 |
| `pipeline_url/services/source_validation.py` | 1 |
| `pipeline_url/ai/source_resolution.py` | 1 |
| `pipeline_url/ai/final_review.py` | 1 |
| `pipeline_lib/services/unified_metadata.py` | 1 |
| `pipeline_lib/services/metadata_resolver.py` | 1 |
| `pipeline_lib/services/artist_album_scraper.py` | 1 |
| `pipeline_lib/services/source_validation.py` | 1 |
| `pipeline_lib/ai/source_resolution.py` | 1 |
| `pipeline_lib/ai/final_review.py` | 1 |
| **Total files removed** | **12** |

---

## 7. Implementation Phases

### Phase 1: URL Pathway Integration — ✅ COMPLETED

**Goal:** Scraper tester and URL pathway share the same scraper code.

**Status:** All 12 steps completed. 84 import references updated. Server validated.

1. ✅ Created `backend/app/scraper/` with `__init__.py`
2. ✅ Created `backend/app/scraper/ai/` with `__init__.py`
3. ✅ Moved `pipeline_url/services/unified_metadata.py` → `scraper/unified_metadata.py`
4. ✅ Moved `pipeline_url/services/metadata_resolver.py` → `scraper/metadata_resolver.py`
5. ✅ Moved `pipeline_url/services/artist_album_scraper.py` → `scraper/artist_album_scraper.py`
6. ✅ Moved `pipeline_url/services/source_validation.py` → `scraper/source_validation.py`
7. ✅ Moved `pipeline_url/ai/source_resolution.py` → `scraper/ai/source_resolution.py`
8. ✅ Moved `pipeline_url/ai/final_review.py` → `scraper/ai/final_review.py`
9. ✅ Updated internal imports within moved files (`app.pipeline_url.*` → `app.scraper.*`)
10. ✅ Updated imports in all consumers (84 references across 16 files)
11. ✅ Old pipeline_url copies removed (files were moved, not copied)
12. ✅ All files pass `py_compile`, server starts and responds on port 6969

**Consumers after Phase 1:**
- Scraper Tester → `app.scraper.*` ✅
- URL Import → `app.scraper.*` ✅
- Rescan → `app.scraper.*` ✅
- Scrape Metadata → `app.scraper.*` ✅
- Library Import → `app.pipeline_lib.*` (unchanged, still separate)

### Phase 2: Library Import Integration (future)

1. Update `pipeline_lib/stages.py` imports to use `app.scraper.*`
2. Delete the pipeline_lib scraper copies (6 files)
3. Validate and test

### Phase 3: Entity Resolution Consolidation (future, optional)

The `metadata/` subdirectories (`resolver.py`, `providers/`) in each pipeline
are also duplicated. These could be shared but have different provider
configurations, so consolidation requires more careful analysis.

### Phase 4: Non-Scraper Service Consolidation (future, optional)

Services like `artwork_manager.py`, `media_analyzer.py`, `nfo_parser.py` etc.
are also duplicated. These are not scraper code and carry lower divergence risk
but could be shared to reduce maintenance burden.

---

## 8. Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Import path errors after move | `py_compile` every modified file before restart |
| Circular imports (unified_metadata ↔ metadata_resolver) | These already exist within pipeline_url and work fine — same structure preserved |
| Pipeline-specific behavior needed in future | Shared functions can accept optional callbacks or config objects |
| Library import breaks during Phase 1 | pipeline_lib keeps its own copies until Phase 2; zero impact |
| Scrape metadata task breaks | It already imports from pipeline_url, so it moves naturally with Phase 1 |

---

## 9. Verification Checklist

After Phase 1 completion, verify:

- [ ] `python -m py_compile app/scraper/unified_metadata.py`
- [ ] `python -m py_compile app/scraper/metadata_resolver.py`
- [ ] `python -m py_compile app/scraper/artist_album_scraper.py`
- [ ] `python -m py_compile app/scraper/source_validation.py`
- [ ] `python -m py_compile app/scraper/ai/source_resolution.py`
- [ ] `python -m py_compile app/scraper/ai/final_review.py`
- [ ] `python -m py_compile app/routers/scraper_test.py`
- [ ] `python -m py_compile app/tasks.py`
- [ ] `python -m py_compile app/pipeline_url/stages.py`
- [ ] Server starts without import errors
- [ ] Scraper tester returns valid results for a test URL
- [ ] URL import completes successfully
- [ ] Rescan completes successfully
