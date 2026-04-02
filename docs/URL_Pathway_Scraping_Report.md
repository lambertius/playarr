# URL Pathway Scraping — Comprehensive Investigation Report

**Date:** March 23, 2026  
**Scope:** URL import pathway only  
**Reference:** All findings cross-checked against `docs/FAILED_APPROACHES.md` to ensure no regressions

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Four Scraping Pathways — Intended vs Actual Behavior](#2-four-scraping-pathways)
3. [Frontend Toggle Behavior — Analysis & Issues](#3-frontend-toggle-behavior)
4. [MusicBrainz Pathway — Detailed Analysis](#4-musicbrainz-pathway)
5. [Wikipedia Pathway — Detailed Analysis](#5-wikipedia-pathway)
6. [AI Only Pathway — Detailed Analysis](#6-ai-only-pathway)
7. [AI Auto Pathway — Detailed Analysis](#7-ai-auto-pathway)
8. [Source Type Enforcement Audit](#8-source-type-enforcement)
9. [Fallback & Cross-Reference Pathway](#9-fallback-cross-reference)
10. [Art Priority Rules](#10-art-priority-rules)
11. [Identified Bugs](#11-identified-bugs)
12. [Simulated Success Scenarios](#12-simulated-success-scenarios)
13. [Simulated Failure Scenarios](#13-simulated-failure-scenarios)
14. [Recommendations](#14-recommendations)

---

## 1. Executive Summary

The URL import pipeline has four scraping modes controlled by four frontend toggles. Investigation reveals:

- **5 bugs** in the current toggle/flag logic
- **1 anti-pattern** in source type enforcement that could allow incorrect art assignment
- **Missing fallback pathway** — the cross-reference pathway (following links from album→single, single→artist) exists but is incomplete
- **Frontend toggle mutual exclusion is wrong** — it doesn't match the user's desired behavior

The user's desired behavior specifies:
- **AI Auto** and **AI Only** are mutually exclusive with each other
- **Wiki** and **MB** can coexist
- When **AI Auto** is selected, all toggles are off EXCEPT AI Only (which should be off too — AI Auto is exclusive)
- If **Wiki** is selected, **MB** can also be turned on (and vice versa)

Current behavior has all four modes as mutually exclusive with AI modes, but Wiki and MB already coexist. The main gaps are in backend flag computation and missing "user-provided link" support.

---

## 2. Four Scraping Pathways — Intended vs Actual

### 2a. User's Desired Behavior

| Mode | Wiki | MB | AI Source Res | AI Final Review | IMDB | Scrapers | Description Generation |
|------|------|----|---------------|-----------------|------|----------|----------------------|
| **Wiki** | ON | user choice | OFF | OFF | OFF | Wiki only | Yes (from Wikipedia) |
| **MB** | user choice | ON | OFF | OFF | OFF | MB only | No |
| **AI Only** | OFF | OFF | ON (IMDB search) | OFF | ON | None | Yes (AI generates) |
| **AI Auto** | ON | ON | ON | ON | ON | All + AI verification | Yes (AI improves wiki desc) |

### 2b. Current Actual Behavior

| Mode | `scrape` | `scrape_musicbrainz` | `ai_auto_analyse` | `ai_auto_fallback` | What actually runs |
|------|----------|---------------------|-------------------|-------------------|--------------------|
| **Wiki+MB** (default) | true | true | false | false | Wiki search → MB search → IMDB search. No AI. |
| **Wiki only** | true | false | false | false | Wiki search → IMDB search. Entity resolution still runs Wiki (**BUG: also MB entities**). |
| **MB only** | false | true | false | false | MB search only. BUT entity resolution and source collection still run Wiki (**BUG**). |
| **AI Auto** | false | false | true | false | AI Source Res → MB (all) → Wiki (all) → IMDB → AI Final Review. ✅ Correct. |
| **AI Only** | false | false | false | true | AI Source Res → AI Final Review. No scrapers. (**BUT** entity resolution and source collection still scrape Wiki — **BUG**). IMDB search skipped for AI Only by current code. |

### 2c. Gaps Between Intended and Actual

| Gap | Description | Impact |
|-----|-------------|--------|
| **AI Only doesn't run IMDB search** | User expects IMDB search in AI Only mode. Current code only searches IMDB for the video; AI Only has skip_wiki=True + skip_mb=True which gates out the IMDB search in `_step_collect_source_links`. | No IMDB source link in AI Only mode. |
| **AI Only description is AI-only** | User expects "generate a description and metadata". Current: AI Final Review can generate descriptions, but the prompt gets no scraped plot. | ✅ Correct — AI generates from scratch. |
| **MB doesn't generate descriptions** | User specified MB does NOT generate descriptions. Current: No description comes from MB. | ✅ Correct. |
| **Wiki generates descriptions** | User specified Wiki DOES scrape descriptions. Current: `scrape_wikipedia_page()` extracts `plot` from articles. | ✅ Correct. |
| **AI Auto second pass** | User expects: AI compares results to expectations, can direct scraper links for 2nd pass if wrong, generates metadata if nothing found. Current: AI Source Resolution provides links → scrapers use them → AI Final Review corrects. NO explicit "second pass" scrape with corrected links. | **Partial** — AI provides links upfront, but no second-pass rescrape if results are wrong. |
| **User-provided links** | User expects the ability to provide a single/album/artist release link to assist the scraper. No current UI for this. | **Missing feature.** |
| **Art priority MB>Wiki** | User expects: When Wiki+MB both enabled, art from MB has priority, Wiki is fallback. Current: `get_artist_artwork()` and `get_album_artwork()` use MB first, Wiki fallback. | ✅ Correct for entity art. Partially correct for poster (Wiki image_url in metadata, CAA upgrade in deferred). |

---

## 3. Frontend Toggle Behavior — Analysis & Issues

### Current Toggle Mutual Exclusion Logic

```
Scrape Wikipedia  ON → AI Auto OFF, AI Only OFF
Scrape MusicBrainz ON → AI Auto OFF, AI Only OFF
AI Auto ON → Wiki OFF, MB OFF, AI Only OFF
AI Only ON → Wiki OFF, MB OFF, AI Auto OFF
```

### User's Desired Behavior

```
Wiki ON  → can also turn ON MB (no restrictions)
MB ON    → can also turn ON Wiki (no restrictions)
AI Auto ON  → ALL others OFF (exclusive)
AI Only ON  → ALL others OFF (exclusive)
```

### Analysis

The current frontend behavior is **almost** correct:
- Wiki and MB turning off AI modes: ✅ Correct
- AI Auto turning off everything else: ✅ Correct
- AI Only turning off everything else: ✅ Correct

**BUT** there's a subtle issue: When Wiki is turned ON, it also turns OFF AI Auto and AI Only. But it does NOT turn OFF MB — that stays wherever it was. This is actually the **desired** behavior. Similarly for MB (doesn't turn off Wiki). **The frontend toggle logic already allows Wiki+MB coexistence.**

However, the toggle descriptions are misleading:
- "AI Auto" description says "Full AI-guided enrichment after scraping" but visually shows Wiki/MB as OFF
- "AI Only" description says "Skip all external scrapers" which is correct

### BUG: AI Auto shows Wiki/MB as OFF but backend enables them

When user selects AI Auto, the frontend turns off `scrapeWiki` and `scrapeMusicbrainz` toggles visually. But in the backend, `ai_auto_analyse=True` forces `_skip_wiki=False` and `_skip_mb=False` via the OR clause. The user sees toggles as OFF but scrapers actually run. This is **semantically correct** but **visually misleading**.

---

## 4. MusicBrainz Pathway — Detailed Analysis

### What it scrapes

MB searches for: **Singles/EPs → Albums → Artist** and retrieves associated art.

| Step | Function | What it finds | Art retrieved |
|------|----------|---------------|---------------|
| 1. Single/EP search | `_search_single_release_group()` | Single or EP release group | `mb_release_id`, `mb_release_group_id` → CoverArtArchive poster |
| 2. Parent album lookup | `_find_parent_album()` | Album release group containing the recording | `mb_album_release_id`, `mb_album_release_group_id` → CoverArtArchive album cover |
| 3. Album fallback | `_find_album_by_artist_browse()` | Album RG via artist's discography browse | Same as above |
| 4. Artist ID | From recording metadata | MusicBrainz artist entity | `mb_artist_id` → Artist image from MB/Wiki |

### Source Type Classification

| MB Result | Source Type | Used For |
|-----------|------------|----------|
| Single/EP release group | `"single"` | Video poster (CoverArtArchive single cover) |
| Album release group | `"album"` | Album entity artwork |
| Artist ID | `"artist"` | Artist entity artwork |
| Recording only (no single found) | `"recording"` | Informational link only, no art |

### Enforcement of "Only Single/EP → poster art"

**Current enforcement points:**
1. `_scrape_with_ai_links()` Step A: AI-provided MB recordings are **rejected if best release is not single/EP** (`allowed_types={"single", "ep"}`)
2. `_search_single_release_group()`: Only accepts `single` and `ep` primary types
3. `_pick_best_release()`: When called with `allowed_types={"single", "ep"}`, rejects albums/compilations
4. `_confirm_single_via_artist()`: Validates that a single actually exists before accepting it
5. Poster upgrade in `deferred.py`: Guard compares `mb_release_id` vs `mb_album_release_id` — if they point to the same release, skips poster upgrade (prevents album cover as video poster)

**Known documented issue (from FAILED_APPROACHES.md):** The poster upgrade chain previously had album cover art fallback that was removed. Only single/EP cover art and Wikipedia single cover are legitimate poster upgrades.

### "Only Album → album art" Enforcement

- Album art is fetched via `get_album_artwork()` using `mb_album_release_group_id` — this only uses the **parent album's** release group, never the single's
- CoverArtArchive album cover goes to `album_poster` asset type, not video `poster`

### "Only Artist → artist art" Enforcement

- Artist art is fetched via `get_artist_artwork()` using `mb_artist_id`
- Goes to `artist_poster` asset type only

---

## 5. Wikipedia Pathway — Detailed Analysis

### What it scrapes

Wiki scrapes: **Single/Song page → Album page (cross-reference) → Artist page → Description/Plot**

| Step | Function | What it extracts | Used for |
|------|----------|-----------------|----------|
| 1. Single/song search | `search_wikipedia()` | Song article URL | Single source link |
| 2. Page scrape | `scrape_wikipedia_page()` | title, artist, album, year, genres, **plot**, image_url, page_type | Video metadata + description |
| 3. Album cross-ref | `extract_album_wiki_url_from_single()` | Album wiki URL from infobox "from the album" | Album source link |
| 4. Album search | `search_wikipedia_album()` | Album article URL | Album source link |
| 5. Artist search | `search_wikipedia_artist()` | Artist article URL | Artist source link |
| 6. Single from album | `extract_single_wiki_url_from_album()` | Single wiki URL from album tracklist | Single source link (fallback) |

### Description Generation

Wikipedia is the **primary source of video descriptions**. The `plot` field is extracted from the first paragraph and any "music video" section of the Wikipedia article. This is then:
- Stored as `metadata["plot"]`
- Passed to AI Final Review (if AI is enabled) for improvement
- Exported to NFO for Kodi

### Page Type Classification

`classify_wikipedia_page()` determines whether a page is:
- `"single"` — infobox has "single by", "song by", "from the album"
- `"album"` — infobox has "studio album by", "compilation album by", or track count > 4
- `"artist"` — infobox has "genres", "years active", "labels" with musician indicators
- `"unrelated"` — none of the above

**Critical guard:** `if metadata.get("wiki_page_type") == "album": image_url = None` in `_step_fetch_artwork_url()` — prevents album cover from being used as video poster. This was a documented fix in FAILED_APPROACHES.md.

### Art from Wikipedia

- `image_url` from single/song pages → video poster (only if page_type != "album")
- Artist infobox photo → `artist_poster` via `WikipediaProvider.search_artist()`
- Album infobox photo → `album_poster` via `WikipediaProvider.search_album()`

---

## 6. AI Only Pathway — Detailed Analysis

### Current Implementation

When `ai_auto_fallback=True`:
1. AI Source Resolution runs — receives YouTube/Vimeo link, platform metadata
2. AI provides identity (artist, title, album, version_type), Wikipedia URL, MB IDs, IMDB URL
3. **No scraper fetch** — `skip_wikipedia=True`, `skip_musicbrainz=True`
4. AI Final Review runs — can generate description from scratch

### What it fills

| Field | Filled by AI Only? | Method |
|-------|-------------------|--------|
| Artist | ✅ | AI identity |
| Title | ✅ | AI identity |
| Album | ✅ | AI identity (but may hallucinate) |
| Year | ❌ | Not from AI Source Resolution |
| Genres | ❌ | Not from AI Source Resolution |
| Description/Plot | ✅ | AI Final Review generates |
| Sources (Wiki/MB/IMDB) | Partial | AI provides URLs but they're only stored if scraping would have used them |
| Artwork (poster) | ❌ | No scraper → no image_url → YouTube thumbnail only |
| Artwork (album/artist) | ❌ | Entity resolution may run but with stale data |

### User's Desired Behavior for AI Only

> "AI only will run an IMDB search. It will be provided a link to the youtube/vimeo video and use that to generate a description and metadata. It will not fill sources or artwork."

**Gaps:**
1. **IMDB search not running** — In AI Only mode, `_step_collect_source_links` has `_skip_wiki=True` and `_skip_mb=True`; the IMDB search is gated on `not (_skip_wiki and _skip_mb)` so it's skipped.
2. **Sources are currently created** — AI-provided Wikipedia/MB URLs are stored as sources even in AI Only mode (if the URL is valid). The user says "it will not fill sources or artwork."
3. **Artwork is NOT filled** — ✅ Correct, no scraper runs so no art URLs are generated.

---

## 7. AI Auto Pathway — Detailed Analysis

### Current Implementation

When `ai_auto_analyse=True`:
1. AI Source Resolution runs — provides identity + external links
2. All scrapers run (MB + Wiki + IMDB) — using AI-provided links first, then search fallback
3. AI Final Review runs — corrects/verifies scraped results, improves description

### User's Desired Behavior for AI Auto

> "AI Auto will use wiki, MB and AI to fully flesh out a track's details and to error check the scraper."
>
> 1. AI is sent the youtube link and asked for critical links to MB and Wiki
> 2. The MB and Wiki scrapers run
> 3. Results are compared to what the AI is expecting
> 4. If the results are wrong, AI can direct the scrape by supplying links for a second pass
> 5. If there are no results, the AI can generate the required metadata
> 6. AI generates description if none exists; if one exists from wiki, improves it

### Analysis of Each Step

| Step | Current Status | Details |
|------|---------------|---------|
| 1. AI provides links | ✅ **Implemented** | `resolve_sources_with_ai()` returns Wikipedia URL, MB IDs |
| 2. Scrapers run | ✅ **Implemented** | `_scrape_with_ai_links()` uses AI links first, search fallback second |
| 3. Results compared | ✅ **Implemented** | AI Final Review receives both scraped and resolved metadata, compares |
| 4. Second pass with corrected links | ❌ **NOT implemented** | If AI Final Review detects wrong results, it corrects fields inline but does NOT trigger a second scrape with corrected links. The identity change handler clears stale data and may re-resolve Wikipedia, but no MB rescrape. |
| 5. AI generates if no results | ✅ **Partially implemented** | AI Final Review can generate description, correct artist/title. But album/year/genres are only AI-guessed without scraper confirmation. |
| 6. Description handling | ✅ **Implemented** | AI Final Review prompt: generate new description if none exists, improve existing wiki description if present |

### Missing: Second-Pass Scrape

The biggest gap in AI Auto is the lack of a **second pass**. Currently:
- AI provides links → scrapers run → AI reviews
- If results are wrong, AI corrects fields but doesn't say "try scraping THIS link instead"

To implement the second pass, the AI Final Review would need to:
1. Detect when scraper results don't match expectations
2. Provide corrected Wikipedia/MB links
3. The pipeline would need to re-run `_scrape_with_ai_links()` with the corrected links

---

## 8. Source Type Enforcement Audit

### Rule: Only Single/EP → Poster Art

| Enforcement Point | Location | Status |
|-------------------|----------|--------|
| MB release type filtering | `_search_single_release_group()` | ✅ Only accepts single/ep |
| AI MB recording validation | `_scrape_with_ai_links()` Step A | ✅ Rejects non-single/ep |
| `_pick_best_release()` type filter | `metadata_resolver.py` | ✅ `allowed_types={"single", "ep"}` |
| Poster upgrade RG guard | `deferred.py` Section 3 | ✅ Skips if `mb_release_id == mb_album_release_id` |
| Album page type guard | `_step_fetch_artwork_url()` | ✅ `wiki_page_type == "album"` → no poster |
| Album cover cascade removed | `deferred.py` | ✅ Only single cover + wiki single cover are poster upgrades |

### Rule: Only Album → Album Art

| Enforcement Point | Location | Status |
|-------------------|----------|--------|
| Album entity creation | `resolve_album()` | ✅ Uses album release group IDs |
| `get_album_artwork()` | `artwork_manager.py` | ✅ Uses `mb_album_release_group_id` |
| CoverArtArchive album fetch | `deferred.py` Section 2 | ✅ Uses album entity's RG ID |

### Rule: Only Artist → Artist Art

| Enforcement Point | Location | Status |
|-------------------|----------|--------|
| Artist entity creation | `resolve_artist()` | ✅ Uses `mb_artist_id` |
| `get_artist_artwork()` | `artwork_manager.py` | ✅ Uses artist entity's MB ID |
| Wikipedia artist page | `search_wikipedia_artist()` | ✅ Validates artist page type |

### Gap: No **explicit** source_type guard at art assignment

The art routing in `_deferred_entity_artwork()` doesn't check `source.source_type` when deciding which art to use. Instead, it relies on the fact that:
- `mb_release_id` (single) and `mb_album_release_group_id` (album) are **separate fields**
- The poster upgrade only uses `mb_release_id` (single)
- Album art only uses `mb_album_release_group_id`

This implicit separation works but is fragile — a developer could introduce a path that conflates them.

---

## 9. Fallback & Cross-Reference Pathway

### Current Cross-Reference Links

The source collection step in `stages.py` (`_step_collect_source_links`) has these cross-references:

```
Single Wiki Page → "from the album" infobox → Album Wiki URL
Album Wiki Page → tracklist links → Single Wiki URL
Album Wiki Page → infobox → Artist Wiki URL
MB Single Release → recording browse → Parent Album RG
MB Artist → single browse → Confirm single exists
MB Artist → album browse → Find album containing track
```

### User's Desired Fallback Pathway

> "There needs to be a fallback pathway that can also be used for confirmation that follows page links. Eg, if an album is identified, a link to the artist can be followed from the album page where a search of that page can reveal a single/ep that wasn't found before. It can also be used to cross-reference the scraped artist actually matches the release. This can also be done from a single to follow a link to the artist."

### What EXISTS vs What's MISSING

| Cross-reference | Status | Location |
|-----------------|--------|----------|
| Single → Album (infobox "from the album") | ✅ Implemented | `extract_album_wiki_url_from_single()` |
| Album → Single (tracklist link) | ✅ Implemented | `extract_single_wiki_url_from_album()` |
| Album → Artist (infobox) | ❌ **NOT implemented** | No function extracts artist URL from album page |
| Single → Artist (infobox) | ❌ **NOT implemented** | No function extracts artist URL from single page |
| MB Album → Single (tracklist browse) | ❌ **NOT implemented** | Could be done via `browse_releases(release_group=album_rg_id)` |
| MB Single → Artist (credits) | ✅ Implicit | `mb_artist_id` is always set from MB recording credits |
| Artist cross-reference validation | ❌ **NOT implemented** | No validation that scraped artist matches album/single's artist |

### Missing: Artist URL Extraction from Album/Single Pages

Currently, `search_wikipedia_artist()` searches Wikipedia for the artist independently. It does NOT follow links FROM the single or album page to the artist page. This is a missed opportunity for verification and a potential source of false positives.

### Missing: MB Album → Single Discovery

When `_find_parent_album()` identifies an album, the code does not then browse the album's release group to discover singles that weren't found in the initial search. This is the "following page links" fallback the user described.

---

## 10. Art Priority Rules

### User's Desired Priority

> "When wiki and MB are enabled, art priority is from MB, but will fall back to Wiki if no art is available on MB."

### Current Implementation

| Art Type | Priority 1 | Priority 2 | Priority 3 |
|----------|-----------|-----------|-----------|
| **Video Poster** | CoverArtArchive single cover (MB) | Wikipedia single page image | YouTube thumbnail |
| **Album Art** | CoverArtArchive album cover (MB) | Wikipedia album page image | None |
| **Artist Art** | MusicBrainz image URL relations | Wikipedia artist page infobox photo | None |

This matches the user's desired priority: **MB first → Wiki fallback → default/none**.

The priority is correctly implemented in:
- `get_artist_artwork()`: MB images first, Wiki as fallback
- `get_album_artwork()`: CoverArtArchive first, Wiki as fallback
- Poster upgrade (`deferred.py`): CoverArtArchive single cover → Wiki single cover → YouTube thumbnail

---

## 11. Identified Bugs

### BUG 1: `scrape_wikipedia` key mismatch in entity resolution and source collection

**Location:** `stages.py` `_step_resolve_entities()` L587, `_step_collect_source_links()` L724  
**Issue:** Uses `opts.get("scrape_wikipedia", True)` but the opts key from frontend is `"scrape"`.  
**Impact:** Wikipedia entity resolution and source collection **always run** regardless of user's toggle setting.  
**Severity:** Medium — wastes network requests in MB-only mode; creates unexpected wiki sources in AI Only mode.

### BUG 2: `ai_auto_analyse` and `ai_auto_fallback` not persisted in `input_params`

**Location:** `routers/jobs.py` L47-51  
**Issue:** The AI flags are passed to `dispatch_task()` but NOT stored in `ProcessingJob.input_params`.  
**Impact:** Job retry from `input_params` loses AI mode. UI display of job options incomplete.  
**Severity:** Low — job retry is rare for URL imports.

### BUG 3: IMDB search gated out in AI Only mode

**Location:** `stages.py` `_step_collect_source_links()` — IMDB search is inside the `not (_skip_wiki and _skip_mb)` block.  
**Issue:** In AI Only mode both skip flags are True, so IMDB search never runs.  
**Impact:** Contradicts user's requirement that "AI only will run an IMDB search."  
**Severity:** Medium — missing IMDB source link.

### BUG 4: AI Only entity resolution still runs Wikipedia

**Location:** `stages.py` `_step_resolve_entities()` — uses `scrape_wikipedia` key (missing, defaults True).  
**Issue:** Even in AI Only mode, entity resolution queries Wikipedia for artist/album pages.  
**Impact:** Contradicts "Skip all external scrapers" intent. Creates unwanted network traffic + sources.  
**Severity:** Low-Medium.

### ~BUG 5~ (Not a bug): AI Auto UI shows Wiki/MB toggles as OFF

**Status:** By design. The user selects AI Auto and the backend handles enabling all scrapers automatically. The user is not expected to manually set Wiki/MB toggles when AI Auto is active. No change needed.

---

## 12. Simulated Success Scenarios

### Scenario S1: Wiki+MB — "Foo Fighters — Everlong"

**Path:** Default (Wiki=ON, MB=ON)

1. **MB Strategy 1:** Search `primarytype:single` for "Everlong" by "Foo Fighters"
   - Result: Release group "Everlong" (single, 1997) → `mb_release_group_id` set
   - Best release selected → `mb_release_id` set
   - Parent album found: "The Colour and the Shape" → `mb_album_release_id` + `mb_album_release_group_id` set
2. **Wiki search:** "Everlong (Foo Fighters song)" found
   - Page scraped: page_type="single", plot extracted from article, image_url = single cover
   - Cross-ref: "from the album The Colour and the Shape" → album wiki URL found
3. **IMDB search:** "Everlong Foo Fighters" → tt0189806 found
4. **Source collection:**
   - `musicbrainz/single` ✅, `musicbrainz/album` ✅, `musicbrainz/artist` ✅
   - `wikipedia/single` ✅, `wikipedia/album` ✅ (from cross-ref), `wikipedia/artist` ✅
   - `imdb/video` ✅
5. **Art results:**
   - Poster: Wikipedia single cover → upgraded to CoverArtArchive single cover in deferred
   - Album art: CoverArtArchive "The Colour and the Shape" cover
   - Artist art: Foo Fighters photo from MB/Wikipedia
6. **Description:** From Wikipedia article about the single

**Result:** ✅ All metadata populated. Description from Wiki. Art from MB (primary) with Wiki fallback.

---

### Scenario S2: MB Only — "Radiohead — Creep"

**Path:** Wiki=OFF, MB=ON

1. **MB Strategy 1:** "Creep" single found (1992/1993)
2. **Parent album:** "Pablo Honey" found via `_find_parent_album`
3. **Wiki skipped** in metadata resolution (correct: `skip_wikipedia=True`)
4. **BUT:** Entity resolution and source collection still query Wikipedia (**BUG 1**)
5. **IMDB search:** Runs (neither wiki nor MB skip is True since MB is ON)
6. **Source collection:**
   - `musicbrainz/single` ✅, `musicbrainz/album` ✅, `musicbrainz/artist` ✅
   - `wikipedia/*` sources created (**unexpected due to BUG 1**)
   - `imdb/video` ✅
7. **Art:** CoverArtArchive only (no Wiki image_url in metadata since wiki was skipped in metadata). Entity art may include Wiki fallback from the entity resolution step.
8. **Description:** None from scraper. No AI. Blank plot.

**Result:** ⚠️ Mostly correct but Wikipedia sources are created unexpectedly due to BUG 1. No description generated.

---

### Scenario S3: AI Only — "Daft Punk — Around the World"

**Path:** AI Only (wiki=OFF, MB=OFF, ai_auto_fallback=ON)

1. **AI Source Resolution:** Receives YouTube URL + platform metadata
   - AI identifies: artist="Daft Punk", title="Around the World", album="Homework"
   - AI provides: Wikipedia URL, MB recording ID, IMDB URL
2. **No scrapers run** — `skip_wikipedia=True`, `skip_musicbrainz=True`
3. **AI Final Review:** Receives AI-identified metadata only
   - Generates description from scratch (no wiki plot available)
   - Corrects album if needed
4. **Source collection:**
   - AI-provided URLs stored BUT IMDB search **does not run** (BUG 3)
   - Entity resolution still queries Wikipedia (**BUG 4**)
5. **Art:** No scraper art. YouTube thumbnail only. No CoverArtArchive.

**Result:** ⚠️ Description generated. Basic metadata OK. Missing IMDB source. Unexpected Wikipedia entity queries.

---

### Scenario S4: AI Auto — "Billie Eilish — bad guy"

**Path:** AI Auto (ai_auto_analyse=ON)

1. **AI Source Resolution:** Identifies track, provides:
   - Wikipedia: `https://en.wikipedia.org/wiki/Bad_Guy_(Billie_Eilish_song)`
   - MB recording ID: valid UUID
   - IMDB: tt9735318
2. **MB Step A:** AI-provided recording ID validated → single release confirmed
   - Parent album: "When We All Fall Asleep, Where Do We Go?" found
3. **Wiki Step A:** AI-provided Wikipedia URL scraped
   - page_type="single", plot extracted, image_url found
   - Cross-ref: album wiki URL from infobox
4. **IMDB:** AI-provided URL accepted
5. **AI Final Review:** Compares scraped results with AI expectations
   - All match → no corrections needed
   - Improves Wikipedia description with additional context
6. **Source collection:** All 7 source types populated
7. **Art:**
   - Poster: Wikipedia single cover → CoverArtArchive single cover upgrade
   - Album: CoverArtArchive album cover
   - Artist: MB image → Wiki fallback

**Result:** ✅ Full metadata. AI-enhanced description. All sources. Art priority correct.

---

## 13. Simulated Failure Scenarios

### Failure F1: Wiki+MB — "Obscure Artist — Rare Track" (No MB single, no Wiki article)

**Path:** Default (Wiki=ON, MB=ON)

1. **MB Strategy 1:** No single release group found
2. **MB Strategy 2:** Recording found on album "Greatest Indie Hits Vol. 3" (compilation)
   - `_confirm_single_via_artist()` → no matching single exists
   - **Album assignment skipped** (correct — no single = no album from MB)
   - `mb_recording_id` set, `mb_artist_id` set, `mb_release_group_id` NOT set
3. **Wiki search:** No article found (obscure track)
4. **IMDB:** No match
5. **Art:** YouTube thumbnail only. No MB art (no release group). No wiki art.
6. **Description:** None.

**Result:** ⚠️ Minimal metadata: artist + title from URL parsing. No album, no description, no art. Source: `musicbrainz/recording` only. This is the expected graceful degradation for unknown tracks.

**Risk assessment:** LOW — the system correctly abstains rather than hallucinating data.

---

### Failure F2: AI Auto — AI Provides Wrong Wikipedia Link

**Path:** AI Auto

1. **AI Source Resolution:** For "The Lonely Island — Jizz in My Pants"
   - AI provides Wikipedia URL: `https://en.wikipedia.org/wiki/Jizz_in_My_Pants` (correct)
   - AI provides MB recording: wrong UUID (hallucinated)
2. **MB Step A:** Recording lookup returns wrong track → title validation fails (sim < 0.5) → **REJECTED** ✅
3. **MB Step B:** Search fallback finds correct recording → single found
4. **Wiki Step A:** AI URL works → page scraped correctly
5. **AI Final Review:** Compares and confirms correct results

**Result:** ✅ Defense in depth works. AI hallucinated MB ID rejected, search fallback succeeds.

**BUT if AI provides wrong Wikipedia URL:**
1. AI URL scraped → `detect_article_mismatch()` checks artist match
2. If mismatch detected → URL rejected → search fallback runs
3. If mismatch NOT detected (wrong article about a same-named different artist) → wrong description used

**Risk assessment:** MEDIUM — depends on mismatch detection accuracy. Documented in FAILED_APPROACHES: artist verification via infobox is not 100% reliable.

---

### Failure F3: AI Only — No AI Provider Configured

**Path:** AI Only, but `ai_provider` setting = "none"

1. **AI Source Resolution:** `get_ai_provider()` returns None → AI skipped entirely
2. **No scrapers** (skip_wiki=True, skip_mb=True)
3. **AI Final Review:** Also skipped
4. **Result:** Video imported with only parsed metadata from URL. No description, no art, no sources.

**Risk assessment:** HIGH for user confusion — AI Only mode silently does nothing. Should warn user.

---

### Failure F4: Wiki Only — Wikipedia Returns Album Page Instead of Single

**Path:** Wiki=ON, MB=OFF

1. **Wiki search:** For "Adam Cohen — We Go Home"
   - Wikipedia article "We Go Home" is an **album**, not a single
   - `classify_wikipedia_page()` correctly detects `page_type="album"`
2. **Poster guard:** `wiki_page_type == "album"` → `image_url = None` → YouTube thumbnail used ✅
3. **Source classification:** Stored as `source_type="album"` (not "single") ✅
4. **BUT:** Album metadata (track listing, year) is extracted and applied to the video. Album name set correctly.
5. **Description:** Album description from Wikipedia, not single description. May be about the album as a whole, not the specific track.

**Result:** ⚠️ Correct source typing and poster handling. Incorrect description (album-level, not track-level). No single-specific Wikipedia content found.

**Documented in FAILED_APPROACHES:** Yes — "Album cover art used as video poster when Wikipedia page is an album" — fix applied (wiki_page_type guard).

---

### Failure F5: MB+Wiki — Artist with Unicode Hyphen (a‐ha — Take On Me)

**Path:** Default (Wiki=ON, MB=ON)

1. **MB search:** Artist "a‐ha" (U+2010 hyphen) vs MB "a-ha" (ASCII hyphen)
   - Without normalization: SequenceMatcher falls below 0.60 → artist match fails → wrong or no results
2. **Fix already applied:** Unicode hyphen normalization at search function entry points

**Result:** ✅ With the fix applied, this is a **documented solved case**. Would fail if normalization removed.

**Risk of regression:** If any new search function is added without `_UNICODE_HYPHENS` normalization.

---

### Failure F6: AI Auto — AI Identifies Wrong Song (Cover/Sample)

**Path:** AI Auto for "slackcircus — Fabulous Secret Powers"

1. **AI Source Resolution:** AI identifies sampled song "4 Non Blondes — What's Up?"
2. **MB scrape:** Finds "What's Up?" single by 4 Non Blondes
3. **Wiki scrape:** Finds "What's Up?" Wikipedia article
4. **AI Final Review:** Corrects artist to "slackcircus", title to "Fabulous Secret Powers"
5. **Identity change detected:** All MB IDs cleared, album cleared, IMDB cleared
6. **Re-resolution:** `search_wikipedia()` called for corrected identity

**Result:** ✅ Identity change handler works correctly. MB IDs cleared. Description re-derived. Documented in FAILED_APPROACHES.

**Risk of regression:** If identity change re-sync of `primary_artist` is removed (documented fix).

---

### Failure F7: AI Auto — Second Pass Needed But Not Available

**Path:** AI Auto for a track where AI provides correct identity but wrong Wikipedia link

1. **AI Source Resolution:** Correct identity, but Wikipedia URL points to wrong disambiguation
2. **Wiki Step A:** Scrapes wrong page → `detect_article_mismatch()` detects issue → rejects
3. **Wiki Step B:** Search fallback finds correct page
4. **AI Final Review:** Confirms correct results
5. **No second pass needed** in this case — search fallback handled it

**BUT what if Wiki search fallback ALSO fails?**
1. Steps 1-3 as above
2. Wiki Step B: Search also returns wrong page or nothing
3. AI Final Review: No wiki description available → AI generates from scratch
4. **No second-pass rescrape** — AI cannot say "try THIS URL instead" and trigger another scrape

**Result:** ⚠️ Falls back to AI-generated description. Metadata may be incomplete. Second pass would have improved results.

---

### Failure F8: Frontend — User Provides Release Link (Not Yet Supported)

**Path:** Any mode — user has the correct MB single URL

1. **User pastes:** `https://musicbrainz.org/release-group/abc123` in... where?
2. **No UI input** for user-provided links
3. **No backend parameter** to accept pre-supplied MB/Wiki/IMDB URLs from the user

**Result:** ❌ Feature not implemented. User cannot assist the scraper with known-good links.

---

## 14. Recommendations

### Priority 1: Fix Existing Bugs

1. **Fix key mismatch `"scrape"` vs `"scrape_wikipedia"`** in `_step_resolve_entities()` and `_step_collect_source_links()`. Change to `opts.get("scrape", True)` to match the frontend's key.

2. **Fix IMDB search gating in AI Only mode** — IMDB search should run in AI Only mode as the user requires. Separate the IMDB search gate from the wiki/MB skip flags.

3. **Persist `ai_auto_analyse` and `ai_auto_fallback` in `input_params`** for job persistence.

### Priority 2: Toggle Behavior Alignment

4. **Update AI Auto toggle visuals** — When AI Auto is selected, either:
   - Show Wiki/MB toggles as ON (greyed out, with tooltip "Automatically enabled by AI Auto")
   - Or keep them OFF but add a note "Wiki and MB will also run in AI Auto mode"

5. **Frontend mutual exclusion is already correct** — Wiki and MB can coexist. AI Auto and AI Only are exclusive. No change needed to the logic.

### Priority 3: Missing Features

6. **User-provided link input** — Add optional URL fields to AddVideoModal for:
   - MB release URL (single/album/artist)
   - Wikipedia URL (single/album/artist)
   - Pass as hints to `_scrape_with_ai_links()` to use as Step A sources

7. **Second-pass scrape for AI Auto** — Enhance AI Final Review to return corrected URLs when it detects scraper results are wrong. Add a conditional re-scrape step after the review.

### Priority 4: Fallback Pathway Enhancement

8. **Add artist URL extraction from single/album Wikipedia pages** — Follow infobox links to the artist page for cross-reference validation.

9. **Add MB album → single discovery** — After finding parent album via MB, browse the album's releases to discover singles not found in the initial search.

10. **Add artist cross-reference validation** — Verify that the scraped artist name matches the artist listed on album/single pages.

### Critical: No Regression on Documented Fixes

All changes MUST preserve these documented protections from FAILED_APPROACHES.md:
- Unicode hyphen normalization at search entry points
- `wiki_page_type == "album"` guard for poster art
- `allowed_types={"single", "ep"}` in `_pick_best_release()` for track contexts
- Identity change detection using `parse_multi_artist()` set comparison
- Poster upgrade RG guard (release-group comparison, not just release ID)
- `sanitize_album()` running after every album-modifying stage
- `primary_artist` re-sync after AI Final Review
- Album art cascade removed from poster upgrade chain
- `"& The"` band name protection in `_split_artists()`
- Parenthetical title handling in MB title similarity gates
