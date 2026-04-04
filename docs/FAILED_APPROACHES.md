# Failed Scraper Approaches — Do Not Repeat

This document tracks approaches that have been tried and failed. Any new fix
MUST NOT reuse a failed approach. Only forward-iterating solutions are allowed.

---

## YouTube Source Matching — General

### FAILED: Title similarity + artist-in-channel as sole scoring signals (Attempt 1)
- **What was tried:** `search_youtube()` scored candidates using only two signals: (1) SequenceMatcher ratio between `"{artist} - {title}"` and YouTube title, and (2) a flat +0.15 boost if the artist name appeared in the channel name. Overall score was `title_score × 0.6 + duration_score × 0.4`.
- **Why it failed:** Fan edits, lyric videos, covers, and unofficial re-uploads often have nearly identical titles to official videos. A fan video titled "Artist - Title (Fan Edit)" scores only marginally lower than the official upload. With no channel authority weighting, a fan channel's re-upload can outscore the official Vevo/artist channel upload when the title is a slightly closer string match.
- **Lesson:** YouTube matching must incorporate channel authority signals (Vevo, official channels, topic channels), penalize known unofficial markers in titles/descriptions ("fan edit", "cover", "lyric video"), and boost videos explicitly labeled "Official Video" or "Official Music Video". Title similarity alone is insufficient to distinguish official from unofficial content.
- **Fix applied:** Added multi-signal scoring: official title keywords (+0.10–0.15), trusted channel patterns like Vevo (+0.10–0.15), artist-matching channel names (+0.10–0.15), and negative penalties for fan/unofficial markers (-0.10–0.20). These adjust `title_score` before the final weighted combination.

---

## Track: AronChupa feat. Little Sis Nora — I'm an Albatraoz

### FAILED: Relying on AI Final Review to leave album blank (Attempt 1–3)
- **What was tried:** Trusted the AI to return null/empty for albums when scrapers found none.
- **Why it failed:** The AI prompt substituted "Unknown" for empty album, then AI echoed it back as a real value. Fixed by changing to "[not set]", but then AI invented `"I'm an Albatraoz - Single"` (with literal quote characters wrapping it) on the next import.
- **Lesson:** AI will ALWAYS try to fill in the album field. The AI cannot be trusted to leave album blank — it must be overridden by code regardless of what the AI returns.
- **Examples of failure:**
  - Job 61 (first): `album: 'None' → 'Unknown' (conf=0.80)`
  - Job 61 (second): `album: 'None' → '"I'm an Albatraoz - Single"' (conf=0.85)` — note the literal quote characters wrapping the value

### FAILED: Relying on sanitize_album regex to catch all single patterns (Attempt 1–2)
- **What was tried:** `_SINGLE_LABEL_PATTERNS` regex `^(.+?)\s*-\s*Single$` to detect and strip single-label albums.
- **Why it failed:** AI wrapped the value in literal double-quote characters: `"I'm an Albatraoz - Single"`. The leading `"` prevents the regex `^(.+?)` from matching the title correctly, so the single-label pattern doesn't fire, and the value passes through as a "real" album.
- **Lesson:** Must strip enclosing quotes before applying any regex patterns.
- **Fix applied (session 4):** Added quote-stripping at the start of `sanitize_album()` in `source_validation.py`. Also added `sanitize_album` as a guard inside the AI Final Review correction loop so AI-proposed albums are sanitized BEFORE acceptance.

### FAILED: Wikipedia artist source never updated on re-import (Attempt 1)
- **What was tried:** Import code used `if not _existing_wiki_artist: create()`. On re-import, the stale `Omi_(singer)` Wikipedia artist source persisted because the code only creates new sources, never updates existing ones.
- **Why it failed:** `search_wikipedia_artist("AronChupa")` was fixed (added -10 penalty for wrong artist), but existing wrong source in DB was never overwritten.
- **Lesson:** Source persistence must UPDATE existing records when the new value differs, not just skip creation.
- **Fix applied (session 4):** Changed to `if existing: update() else: create()` pattern in `tasks.py` Wikipedia artist source block.

### FAILED: Wikipedia artist search with featuring credits (Attempt 1–2)
- **What was tried (session 4):** Fixed source persistence to update stale records. But the call site passes `final_artist = "AronChupa featuring Little Sis Nora"` to `search_wikipedia_artist`. The function's name-matching check does `any(av in pt_lower for av in _artist_variants)` where the variant is the full featuring name — but `"aronchupa featuring little sis nora"` is NOT a substring of `"aronchupa"` (the page title). So the function returns None, and the update-on-reimport block never runs.
- **Why it failed:** `search_wikipedia_artist` only treated the full artist string as a single variant, never extracting the primary artist from featuring credits. All Wikipedia search terms used the full name, producing no relevant matches.
- **Lesson:** Functions that receive artist names must handle featuring credits by extracting the primary artist. A "featuring" suffix must NOT prevent matching the primary artist's Wikipedia page.
- **Fix applied (session 5):** Added `parse_multi_artist` extraction inside `search_wikipedia_artist()` itself, adding the primary artist as a variant and using it for search terms. This makes the function featuring-aware regardless of call site.

### FAILED: MusicBrainz zero diagnostic visibility (Attempt 1–2)
- **What was tried:** Added entry/exit logs inside `_scrape_with_ai_links`. However, when the server ran old bytecache, no logs appeared and the root cause was invisible.
- **Why it failed:** Scraper logs are only written to the job log AFTER `_scrape_with_ai_links` returns. No logging at the tasks.py level traced what flags were passed or what the pipeline produced.
- **Lesson:** Critical pipeline flags and results must be logged at the JOB level (in tasks.py) not just inside scraper functions, so they're always visible regardless of inner function behavior.
- **Fix applied (session 5):** Added flag logging before `resolve_metadata_unified` call and MB result logging after return, both written directly to job log.

### FAILED: `_album_is_title_duplicate` only checked one direction (Attempt 1)
- **What was tried:** Checked if album is a prefix of title (e.g. "Kazoo Kid" vs "Kazoo Kid - Trap Remix") but not the reverse.
- **Why it failed:** AI proposed album="I'm an Albatraoz - Single" vs title="I'm an Albatraoz" — the TITLE is a prefix of the ALBUM, not the other way around. The function returned False, so the AI album was accepted.
- **Lesson:** Title-duplicate check must be bidirectional.
- **Fix applied (session 4):** Added reverse prefix check to `_album_is_title_duplicate`.

### FAILED: No sanitize_album call after AI Final Review (Attempt 1)
- **What was tried:** Relied on `sanitize_album` being called in the scraper stage (before AI review). AI Final Review then re-introduced "Title - Single" album unchecked.
- **Why it failed:** The AI Final Review is the LAST stage — any album value it produces bypasses earlier sanitization. Needed a post-review sanitization guard.
- **Lesson:** Sanitization must run AFTER every stage that can modify the album, not just once.

### FAILED: Relying on stale-worker-will-be-restarted as a fix (Attempt 1)
- **What was tried:** Assumed the stale Celery worker bytecode caused MusicBrainz to be skipped, and restarting would fix it.
- **Why it failed:** MusicBrainz is still missing after the re-import. The actual issue is in the skip-flag computation logic, not stale bytecode.
- **Lesson:** Must trace the actual code path for skip_musicbrainz flag computation instead of blaming the cache.

### FAILED: Relying on AI prompt instructions to prevent bad plot output (Attempt 1)
- **What was tried:** Added "CRITICAL" instructions to the AI prompt saying not to use YouTube description as the plot.
- **Result on re-import:** AI now rewrites the plot with video credits from the YouTube description appended ("Video credits (per the official upload): Directed by Aron Ekberg..."). Better than raw YouTube dump, but still incorporates YouTube description content into the plot.
- **Lesson:** Prompt-level controls are unreliable. Code guards must be the primary defense.

---

## Track: Kishi Bashi — I Am the Antichrist to You

### FAILED: No title similarity gate on MusicBrainz single search (Attempt 1)
- **What was tried:** `_search_single_release_group()` scored candidates primarily by artist match (+50) and picked highest scorer even with terrible title matches.
- **Why it failed:** For a non-existent single, it returned the highest-scored but wrong release group (e.g., "Comin' To You" instead of "I Am the Antichrist to You").
- **Fix applied:** Added title similarity gate >= 0.6.

### FAILED: No validation on recording fallback (Attempt 1)
- **What was tried:** Recording fallback in `search_musicbrainz()` accepted any result without checking title/artist similarity.
- **Why it failed:** Returned completely wrong recordings.
- **Fix applied:** Added title >= 0.6 AND artist >= 0.5 validation, switched to keyword args.

---

## Track: Modest Mouse — Float On

### FAILED: Relying solely on `_find_parent_album` for album release-group (Attempt 1)
**Problem:** `_find_parent_album(recording_id)` browses MusicBrainz releases for the recording to find an Album-type release group. For "Float On", the single's recording ID is different from the album's recording ID — MusicBrainz treats them as separate recordings. So `browse_releases` only returns Single + Compilation release groups, never the Album. `mb_album_release_group_id` stays None and no musicbrainz/album source is created.
**Fix:** Added fallback in entity enrichment (Step 8c.6): when `album_entity.mb_release_id` exists but `mb_album_release_group_id` is None, look up the release-group directly from the release via `get_release_by_id(includes=["release-groups"])`.

---

## General Anti-Patterns

### ANTI-PATTERN: Trusting AI output without code-level validation
Every AI-generated field must be validated by deterministic code AFTER the AI returns. Prompt instructions are advisory; code enforcement is mandatory.

### ANTI-PATTERN: Adding defense layers that share the same bypass
Multiple defense layers are useless if they all fail to the same input pattern (e.g., quote-wrapped strings bypassing all regex patterns). Each layer must handle different failure modes.

### ANTI-PATTERN: Assuming worker restart fixes code issues  
If a bug manifests, the code path must be traced to the actual logic error, not attributed to stale cache unless conclusively proven by comparing timestamps AND confirming the code logic is actually correct.

---

## Track: Modest Mouse — Float On / Fedde Le Grand — Put Your Hands Up for Detroit

### FAILED: Gating single cover art fetch on album existence (Attempt 1)
**Problem:** Step 8c.7 poster upgrade had `_poster_mb_release_id = metadata.get("mb_release_id") if metadata.get("album") else None`. When no album is resolved (Fedde Le Grand: album=None), the single cover check is skipped entirely and the poster falls back to a YouTube thumbnail. Even when an album IS known (Float On), the `mb_release_id` could be None in metadata if the earlier unified_metadata gate (`if _mb_album_accepted or metadata.get("album")`) also failed to store it.

Additionally, `unified_metadata.py` only stored `metadata["mb_release_id"]` when `_mb_album_accepted or metadata.get("album")` — meaning if the album hadn't yet been resolved at the time of MB search, the single's release ID was discarded.

**Fix:** (a) Always store `metadata["mb_release_id"]` from MB search results — it's the single's release ID needed for CoverArtArchive, independent of album status. (b) Remove the album gate in Step 8c.7 — the single cover art is the ideal poster regardless of album resolution. (c) Add release-group fallback: when `_fetch_front_cover(release_id)` returns None, try `_fetch_front_cover_by_release_group(release_group_id)` which aggregates cover art across all releases in the group.

### ANTI-PATTERN: Gating single-related operations on album existence
The single's metadata (mb_release_id, cover art, etc.) is independent of album resolution. Gating single operations on album status creates fragile coupling where album resolution failures cascade into unrelated poster failures.

### FAILED: AI Final Review clearing scraper image_url when "no artwork provided" (Attempt 2)
**Problem:** Wikipedia scraper successfully sets `metadata["image_url"]` to the single's cover art (e.g. `Modest_Mouse-Float_on-_album_cover.jpg`, `PYHU4D.jpg`). However, the AI Final Review doesn't receive `image_url` as a parameter — it has no artwork to review. It responds with `artwork_approved=False, rejection_reason="No artwork provided for review"`. The unified_metadata handler then **unconditionally** clears `metadata["image_url"] = None`, destroying the valid Wikipedia image. With `image_url=None`, tasks.py falls back to YouTube thumbnail (`_poster_from_scraper=False`) and the CoverArtArchive upgrade runs instead.

**Fix:** Only clear `metadata["image_url"]` when the AI **actually reviewed and rejected** artwork. When the rejection reason contains "no artwork provided", the AI never saw an image — preserve the existing scraper-sourced image_url. This makes Wikipedia single cover art the highest priority poster source, followed by CoverArtArchive, then album art, then YouTube thumbnail.

### ANTI-PATTERN: Unconditionally clearing metadata on negative AI review
AI review results that indicate "nothing to review" are NOT the same as "reviewed and rejected". Defensive code must distinguish between absence of input and active rejection.

---

## Track: Audrey Hobert — Thirst Trap

### FAILED: Recording search accepting album release as single (Attempt 1)
**Problem:** When Strategy 1 (single release-group search) finds no matching single, Strategy 2 (recording search) falls back to `search_recordings`. For "Thirst Trap" by Audrey Hobert, the recording exists on the **album** "Who's the Clown?" — but no single release exists. Strategy 2 found the recording (title sim=1.0, artist match=exact), then `_pick_best_release` selected the album release "Who's the Clown?" as the best release. The album title was stored as `album`, and the album's release ID was stored as `mb_release_id`, making it appear as if the track had a confirmed single release pointing to "Who's the Clown?".

**Why it failed:** The recording search has no mechanism to distinguish "album track with no single" from "track that has a single release". It blindly accepts whatever release the recording appears on. The title/artist gates (≥0.6 and ≥0.5) only validate the recording match quality, not whether the release is an appropriate single match.

**Fix:** Added `_confirm_single_via_artist(mb_artist_id, title)` — after Strategy 2 finds a recording whose best release is NOT a single, browse the artist's release groups (`release_type=["single"]`) and check if any single title matches (sim ≥ 0.6). If no matching single exists, skip assigning `album`, `mb_release_id`, and `mb_release_group_id` from the album release. The recording ID and artist ID are still stored (they're correct). If a matching single IS found, use its release group ID.

### ANTI-PATTERN: Trusting recording-to-release linkage as proof of single existence
A recording appearing on a release does not mean that release is the appropriate single for the track. Many recordings only exist as album tracks. The release type must be validated against the artist's actual discography before being accepted as a single.

---

## Tracks: BABYMETAL — KARATE / MattstaGraham — Caffeine

### FAILED: Pre-populating metadata["image_url"] with yt-dlp thumbnail (Attempt 1)
**Problem:** `unified_metadata.py` set `metadata["image_url"] = ytdlp_metadata.get("thumbnail")` as a "baseline". In `tasks.py`, the poster upgrade (Step 8c.7) checks `_poster_from_scraper = bool(image_url)` and skips the CoverArtArchive upgrade when True (`not _poster_from_scraper`). Since yt-dlp always provides a thumbnail, `_poster_from_scraper` was always True, and the poster upgrade **never ran** — even though CoverArtArchive had correct single cover art for both tracks.

**Why it failed:** The code conflated "any image_url exists" with "a real scraper (Wikipedia/IMDB) provided the image". The yt-dlp thumbnail is a low-quality YouTube screenshot, not authoritative cover art, but it was indistinguishable from a Wikipedia single cover once stored in `metadata["image_url"]`.

**Fix:** Removed `metadata["image_url"] = ytdlp_metadata.get("thumbnail")` from both `unified_metadata.py` and `metadata_resolver.py`. Now `metadata["image_url"]` is only set by real scrapers (Wikipedia, IMDB). The yt-dlp thumbnail fallback in `tasks.py` (`if not image_url and ytdlp_meta: image_url = get_best_thumbnail_url(...)`) handles YouTube thumbnails, keeping `_poster_from_scraper = False` so the CoverArtArchive upgrade runs.

### ANTI-PATTERN: Mixing authoritative and fallback data in the same field
When a field is used to control downstream decisions (like skipping an upgrade), fallback/default values must not be stored in it alongside authoritative values. Use separate fields or a provenance flag to distinguish sources.

---

## Track: The Lonely Island — Jizz in My Pants

### FAILED: Wikipedia artist search without name-in-title enforcement (Attempt 1)
- **What was tried:** `search_wikipedia_artist` scored Wikipedia search results without penalizing pages where the artist name doesn't appear in the page title.
- **Why it failed:** "Ash Island (rapper)" scored 5 (+3 for `(rapper)` tag, +2 for "rapper" snippet keyword) while "The Lonely Island" scored only 4 (+4 for name match, but no snippet bonus because Wikipedia describes them as a "comedy group" and "group" was not in the keyword list). "Ash Island" won 5-to-4, passed the 0.5 similarity gate (sim=0.59), and the Korean rapper's infobox photo was saved as The Lonely Island's artist poster.
- **Lesson:** Page title matching must be mandatory — if the search target doesn't appear in the page title at all, it's almost certainly the wrong page. A heavy penalty (not just missing a bonus) is needed to prevent unrelated pages from winning on incidental keyword matches.
- **Fix already applied:** `-10` penalty added to `search_wikipedia_artist` in `metadata_resolver.py`. "Ash Island" now scores -5 instead of 5. Additionally, the same `-10` penalty was missing from `_scored_wiki_search` in `metadata/providers/wikipedia.py` (entity resolver path) and was added to close that gap.
- **Data fix:** Re-ran `process_artist_album_artwork` with `overwrite=True` to replace the Korean poster with the correct Lonely Island photo.

### ANTI-PATTERN: Scoring by keyword presence alone without validating the target identity
Wikipedia search returns results based on text relevance, not identity matching. Keyword-based scoring (e.g., `(rapper)` +3, snippet "rapper" +2) can easily outweigh a name match (+4) when the correct page's snippet uses unexpected vocabulary (e.g., "comedy group" instead of "band"). Always enforce a hard identity check (name-in-title) as a prerequisite, not just a bonus.

---

## Track: ROLE MODEL — Sally, When The Wine Runs Out

### FAILED: Assuming mb_release_id always points to a single release (Attempt 1)
- **What was tried:** The poster upgrade code (tasks.py step 8c.7) used `metadata["mb_release_id"]` to fetch "single cover art" from CoverArtArchive. The comment stated "The video's mb_release_id points to the single release."
- **Why it failed:** For this track, no standalone single exists on MusicBrainz. The recording only appears on the deluxe album "Kansas Anymore (The Longest Goodbye)" (release `3515d6bd`). `_pick_best_release()` returned the album release since no single was available. Entity enrichment then set `metadata["mb_release_id"]` = `3515d6bd` (the album). The poster upgrade code fetched the album's cover art via CoverArtArchive, mislabeled it "single cover art," and overwrote the YouTube thumbnail with the album cover as the video poster.
- **Lesson:** `_pick_best_release()` prefers singles but falls back to albums. `metadata["mb_release_id"]` can contain an album release ID when no single exists. Any code that assumes it's a single must validate this assumption.
- **Fix applied:** Added a guard in the poster upgrade block: if `mb_release_id == mb_album_release_id` (both point to the same release), the "single cover art" priority is skipped. The guard reads `mb_album_release_id` from metadata with a direct fallback to `album_entity.mb_release_id` to avoid breakage when the metadata field isn't populated by the enrichment block.

### FAILED: Guard relying solely on metadata["mb_album_release_id"] (Attempt 2)
- **What was tried:** The guard compared `metadata["mb_release_id"]` against `metadata["mb_album_release_id"]`. But `mb_album_release_id` is only set inside an enrichment block gated by `not metadata.get("mb_album_release_group_id")`. If that RG field was populated by an earlier pipeline step (e.g. canonical track inheritance), the enrichment block is skipped and `mb_album_release_id` is never set — causing the guard to fall through.
- **Why it failed:** The guard had a single source of truth (`metadata` dict) that isn't always populated. The server also wasn't restarted after the fix, so job 102 ran stale code.
- **Lesson:** When guarding against entity comparisons, use the authoritative object directly (`album_entity.mb_release_id`) rather than relying on metadata dict fields that may not always be populated.
- **Fix applied:** Changed guard to use `metadata.get("mb_album_release_id") or album_entity.mb_release_id` as the comparison value.

### ANTI-PATTERN: Treating a best-effort field as a typed guarantee
`_pick_best_release()` returns the *best available* release, not necessarily a single. Downstream code that labels the result "single cover art" without checking the release-group type creates false positives when the best release is an album. Always validate the release type before assigning semantic meaning to data from heuristic functions.

---

## Wikipedia Search Scoring

### FAILED: Literal "(song)" tag bonus without artist check (Bug)
- **What was tried:** `search_wikipedia()` gave +3 bonus to any page with `(song)` or `(X song)` disambiguation. No check whether the artist inside the disambiguation matched the search artist.
- **Why it failed:** For "Here Without You" by 3 Doors Down, the page "Here Without You (The Byrds song)" scored 10 (+3 from `(song)` tag) while the correct page "Here Without You" (no suffix) scored only 9. The wrong artist's song page beat the correct page.
- **Lesson:** Disambiguation tags containing artist names must be validated against the search artist. A `(song)` tag for a different artist should be a penalty, not a bonus.
- **Fix applied:** Extract artist name from `(X song)` disambiguation via regex. If the disambig names an artist that doesn't match the search artist, apply -6 penalty (net -3 for wrong-artist song pages).

### FAILED: Literal "(album)" string match for album disambiguation (Bug)
- **What was tried:** `search_wikipedia_album()` checked `"(album)" in pt_lower` to give +3 bonus. Only exact `(album)` suffix matched.
- **Why it failed:** For "Youngblood" by 5 Seconds of Summer, the page "Youngblood (5 Seconds of Summer album)" has disambiguation `(5 Seconds of Summer album)` which doesn't contain the literal substring "(album)". Meanwhile "The Youngbloods (album)" does match literally. Result: wrong album page "The Youngbloods" (score 8) beat correct page (score 7).
- **Lesson:** Album disambiguation matching must use regex to match any disambiguation *containing* "album" (e.g. `\(([^)]*)\b(?:album|ep)\)$`), not just the exact literal `(album)`.
- **Fix applied:** Replaced literal check with regex. Added +3 bonus when the search artist name appears inside the disambiguation text (e.g. "5 Seconds of Summer" in "(5 Seconds of Summer album)").

### FAILED: Album disambiguation bonus without wrong-artist penalty (Bug)
- **What was tried:** `search_wikipedia_album()` gave +3 bonus for "(X album)" disambiguation and +3 if the search artist appeared in X. No penalty when X explicitly named a *different* artist.
- **Why it failed:** For "What If?" by Luca Stricagnoli, the page "What If... (Mr. Big album)" scored +3 (album disambig) with no penalty for "Mr. Big" ≠ "Luca Stricagnoli". Combined with album-title-in-page-title (+3) and snippet keywords (+2), total score = 8 — well above the threshold=4. The cross-fallback accepted this wrong-artist album page, stored it as `wikipedia_album`, and displayed Mr. Big's album artwork.
- **Lesson:** Mirror the song search pattern: when the disambiguation text before "album"/"ep" contains text that is NOT a common qualifier (debut, live, compilation, etc.) and does NOT match the search artist, apply a heavy penalty (-10). This is the same fix pattern as the "(X song)" wrong-artist penalty.
- **Fix applied:** Added `-10` penalty when disambiguation text is non-empty, not a common album qualifier, and doesn't contain the search artist. Common qualifiers (debut, live, compilation, deluxe, self-titled, soundtrack, studio, etc.) are excluded from the penalty via a set.

### Cross-verification strategy: single infobox → album URL
- **What it does:** After `search_wikipedia_album()` returns a candidate, the pipeline also fetches the single's Wikipedia page and extracts the album link from the infobox ("from the album" row). If found, this infobox link is authoritative and replaces the search result.
- **Why:** Search-based album matching is inherently fragile (fuzzy title matching, disambiguation scoring). The single's infobox directly links to the correct album page — it's a structured, verifiable relationship rather than a search heuristic.
- **Implementation:** `extract_album_wiki_url_from_single()` in metadata_resolver.py. Integrated into both `library_import_video_task` and `_collect_wiki_source_proposals` in tasks.py.

### FAILED: Guard comparing release IDs when album has multiple editions (Attempt 3)
- **What was tried:** After Attempt 2, the guard compared `mb_release_id` (3515d6bd, deluxe edition) vs `album_entity.mb_release_id` (e464a3fb, standard edition). It also had a release-group guard comparing `metadata["mb_release_group_id"]` vs `metadata["mb_album_release_group_id"]`.
- **Why it failed (three compounding bugs):**
  1. **`resolve_track()` never returns `mb_release_group_id`** — the resolver's output dict doesn't include this field, so `resolved_track` never has it.
  2. **Entity enrichment copies `mb_release_id` but not `mb_release_group_id`** — so `metadata["mb_release_group_id"]` stays None and the RG guard can never fire.
  3. **The RG guard only nullified `_poster_mb_rg_id`, not `_poster_mb_release_id`** — even if the RG check could fire, the release-level CAA fetch would still proceed.
- **Result:** The deluxe album release (3515d6bd) and standard release (e464a3fb) are DIFFERENT release IDs for the SAME album (same RG 6756fe87). The release ID check found them different → passed. The RG check couldn't fire → passed. CAA returned the album cover → false positive.
- **Lesson:** When checking if two releases belong to the same album, comparing release IDs is insufficient — albums have multiple editions with different release IDs. Must compare release groups. And the RG must be fetched if not already present.
- **Fix applied:** (a) When `mb_release_group_id` is missing, the poster guard now looks up the release's RG from MusicBrainz directly. (b) The RG guard now also nullifies `_poster_mb_release_id` (not just `_poster_mb_rg_id`) since a RG match means the release IS the album.

### FAILED: Guard blocks single cover but album cover cascades through priority 3/4 (Attempt 4)
- **What was tried:** Attempt 3 fixed the single cover guard correctly (both release ID and RG comparisons now work). But the poster upgrade chain had 4 priorities: (1) single cover art, (2) Wikipedia single cover, (3) album cover from entity resolution, (4) cached album cover. When the guard blocked priority 1, the cascade fell through to priority 3, re-introducing the same album art.
- **Why it failed:** Priorities 3 and 4 explicitly fetch album cover art regardless of the guard's decision. The guard only blocks priority 1 — it doesn't express "this track has no single-specific art, so don't use album art as a substitute."
- **Lesson:** For music videos, the YouTube thumbnail is more appropriate than a generic album cover. Album art should only be saved on the album entity, not used as a video poster upgrade. Only track-specific art (single covers, Wikipedia single page covers) should replace the YouTube thumbnail.
- **Fix applied:** Removed priorities 3 and 4 (album cover from entity resolution, cached album cover) from the poster upgrade chain. Only single cover art (priority 1) and Wikipedia single cover (priority 2) remain as legitimate poster upgrades.

---

## Track: sombr — back to friends

### FAILED: UNIQUE(provider, source_video_id) constraint preventing shared artist/album sources (Attempt 1)
- **What was tried:** The `sources` table had a `UNIQUE(provider, source_video_id)` constraint. Each source row also carries a `video_id` foreign key. When two videos by the same artist (e.g. sombr — "undressed" and sombr — "back to friends") both try to create artist-level and album-level sources (MusicBrainz artist ID, MusicBrainz album release group, Wikipedia artist page, Wikipedia album page), the second video's `INSERT` hits the unique constraint because the first video already owns that `(provider, source_video_id)` combination.
- **Why it failed:** The source creation code wraps each `INSERT` in `try: db.begin_nested(); db.add(Source(...)); except IntegrityError: pass`. The `IntegrityError` is silently swallowed — no log, no warning. Video 70 (back to friends) ended up with only 3 sources (YouTube, IMDB, MB single) instead of the expected 8, because the 5 shared artist/album sources were silently dropped. The silent `except IntegrityError: pass` pattern made this invisible during debugging.
- **Compounding issue:** Job 103 had TWO import attempts: the first crashed with a `FOREIGN KEY constraint` on `match_results` (referencing deleted `video_id=65`), and the rollback destroyed the in-flight Wikipedia single source. The second attempt ran with `skip_wiki=True`, so the Wikipedia single page for "Back_to_Friends" was never fetched.
- **Lesson:** The unique constraint must be scoped per-video. Each video should have its own source records for shared entities (artist, album). A global `UNIQUE(provider, source_video_id)` makes shared entities a race condition — whichever video imports first owns the source.
- **Fix applied:** Alembic migration `006_source_unique_per_video` changed the constraint from `UNIQUE(provider, source_video_id)` to `UNIQUE(video_id, provider, source_video_id)`. This allows multiple videos to each have their own source row for the same provider/source_video_id combination.

### ANTI-PATTERN: Silently catching IntegrityError without logging
`except IntegrityError: pass` hides constraint violations that may indicate real bugs. At minimum, constraint violations should be logged as warnings so they're visible in job logs. Silent swallowing turns schema design flaws into invisible data loss.

---

## Track: slackcircus — Fabulous Secret Powers

### FAILED: primary_artist not re-synced after AI Final Review (Attempt 1)
- **What was tried:** `_scrape_with_ai_links()` set `metadata["primary_artist"]` via `parse_multi_artist(metadata["artist"])` at the end of the scraper stage. This runs BEFORE the AI Final Review.
- **Why it failed:** AI Source Resolution identified the sampled song as "4 Non Blondes - What's Up?", so `_scrape_with_ai_links()` received `artist="4 Non Blondes"` and set `metadata["primary_artist"] = "4 Non Blondes"`. The AI Final Review then corrected `metadata["artist"]` to "slackcircus", but `primary_artist` was never updated. Downstream code (`process_artist_album_artwork`, `search_wikipedia_artist`) used the stale `primary_artist`, fetching 4 Non Blondes artwork and Wikipedia pages instead of slackcircus.
- **Lesson:** Derivative metadata fields (`primary_artist`, `featured_artists`) must be re-synced after every stage that can modify their source field (`metadata["artist"]`). Setting them once early in the pipeline creates a stale-data window.
- **Fix applied:** Added `primary_artist` re-sync block in `resolve_metadata_unified()` AFTER the AI Final Review, using `parse_multi_artist(metadata["artist"])` to recompute from the corrected artist.

### FAILED: Album from wrong AI identity persists after artist correction (Attempt 1)
- **What was tried:** AI Source Resolution identified "4 Non Blondes - What's Up?" and provided album "Bigger, Better, Faster, More!" (the 4 Non Blondes album). AI Final Review corrected artist/title back to "slackcircus / Fabulous Secret Powers" but did NOT clear the album.
- **Why it failed:** The AI Final Review corrects individual fields independently. When the artist changed from "4 Non Blondes" to "slackcircus", the album "Bigger, Better, Faster, More!" was a 4 Non Blondes album — not a slackcircus album. But the review didn't clear it because it only proposes field-level changes, not identity-level invalidation. The wrong album then cascaded into entity resolution (`resolve_album("slackcircus", "Bigger, Better, Faster, More!")` found the 4 Non Blondes album) and Wikipedia/IMDB source recording.
- **Lesson:** When the AI Final Review changes the artist identity, metadata resolved under the OLD identity (album, MBIDs, IMDB URL from search) should be invalidated unless confirmed by an authoritative source (MusicBrainz with `mb_album_release_group_id`).
- **Fix applied:** Added identity-change detection in `resolve_metadata_unified()` after the AI Final Review. When the artist changes: (1) clear `metadata["album"]` if not MB-confirmed, (2) clear `metadata["imdb_url"]` if it was found via search (not AI-provided URL).

### FAILED: IMDB search under wrong identity (Attempt 1)
- **What was tried:** IMDB search inside `_scrape_with_ai_links()` used AI-resolved identity (`search_artist="4 Non Blondes"`, `search_title="What's Up?"`). Found tt6860618 which was the IMDB entry for the wrong song.
- **Why it failed:** The IMDB search runs during the scraper stage, before the AI Final Review can correct the identity. By the time the review changes the artist to "slackcircus", the IMDB URL is already in `metadata["imdb_url"]`.
- **Lesson:** External searches performed under an AI-proposed identity that is later corrected should be invalidated along with the identity change.
- **Fix applied:** Same as above — IMDB URL from search is cleared when artist identity changes. AI-provided IMDB URLs (`imdb:ai_url` provenance) are preserved since the AI specifically provided them.

### ANTI-PATTERN: Setting derivative metadata fields before all mutation stages complete
Fields like `primary_artist` that are computed from other fields (`metadata["artist"]`) must not be set until ALL stages that can modify the source field have completed. Setting them inside an intermediate stage creates a stale-data window where downstream consumers use outdated values. Either compute them lazily at point-of-use, or re-compute after every mutating stage.

### ANTI-PATTERN: Not invalidating cross-dependent metadata when identity changes
When the AI identifies a sampled/covered song and then corrects the identity, metadata resolved under the old identity (album, IMDB link, Wikipedia sources) belongs to a DIFFERENT entity. These must be invalidated as a group, not left as orphaned data from the rejected identity. Only MB-confirmed data with authoritative IDs should survive an identity switch.

---

## Tracks: 5 Seconds of Summer — She Looks So Perfect / 3 Doors Down — Here Without You

### FAILED: search_musicbrainz() Strategy 2 not calling _find_parent_album() (Attempt 1)
- **What was tried:** `search_musicbrainz()` has two strategies: Strategy 1 searches for single release groups directly, Strategy 2 falls back to recording search. Strategy 2 used `best_rel.get("title")` (the release title from `_pick_best_release()`) as the album name. For 5SOS, this was "Mastermix: Pro Disc 166" (a DJ compilation). For 3 Doors Down, this was "The Better Life" (wrong album — song is from "Away from the Sun").
- **Why it failed:** Strategy 2 never called `_find_parent_album()` or `_find_album_by_artist_browse()`. When Strategy 1 failed (MB search API didn't return the single), Strategy 2 set `result["album"]` from the recording's best release title — which was a compilation or wrong album. Strategy 1 correctly calls `_find_parent_album()` + `_find_album_by_artist_browse()` to find the actual parent album.
- **Root cause:** Two buggy code paths in Strategy 2:
  1. **confirmed_single path** (best release is NOT a single): `result["album"] = best_rel.get("title")` — compilation/wrong album title. Never calls `_find_parent_album()`.
  2. **else path** (best release IS a single): `result["album"] = best_rel.get("title")` — single release title (= song name, rejected by `_album_is_title_duplicate()`). Album left as None.
  Both paths also never set `mb_album_release_id` or `mb_album_release_group_id`.
- **Lesson:** When both Strategy 1 and Strategy 2 can produce album data, they must use the same album-finding logic. A recording's best release title is NOT a reliable album source — it could be a compilation, reissue, or the single itself. Only `_find_parent_album()` can correctly find the album by looking for Album-typed release groups containing the recording.
- **Fix applied:** Added `_find_parent_album()` + `_find_album_by_artist_browse()` calls to both Strategy 2 code paths (confirmed_single and single-type-else). Now returns `album`, `mb_album_release_id`, `mb_album_release_group_id` matching Strategy 1's behavior.

### ANTI-PATTERN: Asymmetric logic between primary and fallback code paths
When a function has two strategies (primary + fallback) that should produce equivalent output, the fallback must perform the same validation and enrichment steps as the primary. Skipping steps in the fallback (e.g. not calling `_find_parent_album()`) creates bugs that only manifest when the primary strategy fails — making them hard to detect during normal testing.

---

## Track: A Great Big World & Christina Aguilera — Say Something

### FAILED: Identity change detection comparing raw artist strings without normalizing featuring separators (Attempt 1)
- **What was tried:** `unified_metadata.py` compares the artist string before and after AI Final Review using simple `lower()` comparison. If "A Great Big World featuring Christina Aguilera" (AI source resolution) becomes "A Great Big World & Christina Aguilera" (AI final review), this was treated as a full identity change.
- **Why it failed:** The AI source resolution used "featuring" while the AI final review used "&" — both referring to the same two artists. A raw string comparison sees these as different, triggering full identity change logic: all 6 MB IDs cleared, album cleared, IMDB cleared, Wikipedia URLs cleared. Entity enrichment only partially recovers (mb_recording_id, mb_release_id) but not mb_artist_id, and doesn't back-populate to the artist/album entity DB objects.
- **Lesson:** Identity change detection must compare the *set of artists*, not the raw string. Collaboration separators ("featuring", "feat.", "&", "and", "vs") are formatting differences, not identity differences.
- **Fix applied:** Use `parse_multi_artist()` to extract (primary, featured) artists from both pre/post strings, build lowercase sets, and compare the sets. Same set = formatting change (no invalidation), different set = true identity change.

---

## Track: Amanda Palmer (feat. The Grand Theft Orchestra) — Do It With a Rockstar

### FAILED: `_FEAT_PATTERNS` requiring whitespace before featuring keyword (Attempt 1)
- **What was tried:** `parse_multi_artist()` used `_FEAT_PATTERNS = [r'\s+feat\.?\s+', ...]` — patterns require whitespace before "feat".
- **Why it failed:** Artist formatted as `"Amanda Palmer (feat. The Grand Theft Orchestra)"` — parenthesized style has `(` before "feat", not whitespace. The regex `\s+feat` doesn't match, so the entire string is returned as the primary artist. This cascaded into artwork search, poster logic, and entity resolution all using the full "(feat. ...)" name.
- **Lesson:** Featuring credits can appear in parenthesized format `(feat. X)`, not just space-delimited `feat. X`. The feat patterns must handle both formats.
- **Fix applied:** Added parenthesized patterns to `_FEAT_PATTERNS`: `r'\s*\(feat\.?\s+'`, `r'\s*\(featuring\s+'`, `r'\s*\(ft\.?\s+'`. Also strip trailing `)` from the featured_part result.

### FAILED: `_re_resolve_sources()` relying solely on `_saved_mb_sources` for single/album MB links (Attempt 1)
- **What was tried:** `_re_resolve_sources()` saved existing MB release-group sources from stale records, then restored them after deleting and recreating sources. For the artist, it created a new source from `item.mb_artist_id`. For single/album, it relied on `_saved_mb_sources` from the original import.
- **Why it failed:** When the initial import didn't have MB IDs populated at source-collection time (e.g. the scraper stage failed to resolve MB but the deferred fresh-search later succeeded), no musicbrainz/single source existed to save. The `_saved_mb_sources` dict was empty, so no single/album link was created despite `item.mb_release_group_id` being populated.
- **Lesson:** MB source creation must not rely exclusively on preserved stale sources. When `item.mb_release_group_id` and `item.album_entity.mb_release_group_id` exist, create sources from them directly as a fallback.
- **Fix applied:** After restoring `_saved_mb_sources`, also create `musicbrainz/single` from `item.mb_release_group_id` (if not already in `_saved_mb_sources`) and `musicbrainz/album` from `item.album_entity.mb_release_group_id`.

### FAILED: `_deferred_kodi_export()` not passing `folder_path` to `export_video()` (Attempt 1)
- **What was tried:** `_deferred_kodi_export()` called `export_video(db, video_id, artist, title, album, year, genres)` without passing `folder_path`, `plot`, or `source_url`.
- **Why it failed:** `export_video()` has `folder_path: Optional[str] = None` and immediately returns `[]` when `not folder_path`. The NFO was never re-exported after AI enrichment, leaving the stale initial-import NFO on disk with old title/artist/album/no plot.
- **Lesson:** When functions have optional parameters with guard clauses, all callers must pass required values. A missing parameter can silently no-op the entire function.
- **Fix applied:** Pass `folder_path=item.folder_path`, `plot=item.plot`, `source_url` (from primary video source), and `resolution_label` to `export_video()`.

### FAILED: `_has_parent_album` guard only checking MB IDs (Attempt 1)
- **What was tried:** `_has_parent_album = bool(mb_album_release_id or mb_album_release_group_id)` — only checked MB album identifiers from the (stale) scraper_results artifact.
- **Why it failed:** When the album entity was created during entity resolution but had no MB IDs populated (Wikipedia-only album resolution), both `mb_album_release_id` and `mb_album_release_group_id` were None. The guard concluded "no parent album" and fell through to treating the album artwork pipeline result as "single cover", applying the album cover art as the video poster.
- **Lesson:** The existence of an album entity in the DB is proof of a parent album, independent of whether MB IDs populate. The guard must check `item.album_entity` in addition to MB fields.
- **Fix applied:** Expanded guard to `bool(mb_album_release_id or mb_album_release_group_id or item.album_entity)`. Also added `mb_release_group_id` lookup from album entity as a fallback.

### ANTI-PATTERN: Reading metadata from stale pipeline artifacts in deferred tasks
Deferred tasks that run after AI enrichment must not rely on `scraper_results` artifacts for entity MB IDs, because AI enrichment can change the identity and entity resolution may populate IDs that weren't present during the scraper stage. Always read current data from the DB entity objects, falling back to artifacts only when DB data is unavailable.

### ANTI-PATTERN: Optional function parameters silently disabling core functionality
When a function has `param: Optional[T] = None` with `if not param: return`, the caller's failure to pass the parameter causes a silent no-op. These parameters should be required when the function is useless without them, or the caller should be audited to ensure all necessary values are provided.

### FAILED: Wikipedia URL construction using only `replace(' ', '_')` without encoding special characters (Attempt 1)
- **What was tried:** All four Wikipedia URL construction sites (`metadata_resolver.py` search functions, `artist_album_scraper.py`, `wikipedia.py` provider) built URLs with `f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"`. For "Is There Anybody Out There? (album)", this produces `…/Is_There_Anybody_Out_There?_(album)` — the `?` is raw.
- **Why it failed:** An unencoded `?` in a URL starts a query string. The browser/HTTP client interprets `?_(album)` as a query parameter, breaking the Wikipedia link. The actual wiki page slug requires `%3F` for the question mark.
- **Lesson:** Wikipedia page titles can contain URL-special characters (`?`, `+`, `#`, `%`, `!`). Only spaces→underscores is insufficient; the path component must be properly percent-encoded.
- **Fix applied:** New `_build_wikipedia_url(page_title)` function in `metadata_resolver.py` using `urllib.parse.quote(slug, safe='/:_(),-')`. All four construction sites now call this function.

### FAILED: Wikipedia single source URL cleared by identity change with no re-resolution path (Attempt 1)
- **What was tried:** When identity change was detected, `source_url` (the Wikipedia URL for the single/song) was cleared to `None`. No code existed to re-resolve it under the corrected artist identity. The pipeline then checked `metadata.get("source_url")` and found nothing, so no Wikipedia single source was created.
- **Why it failed:** The identity change handler treated clearing as sufficient — assuming downstream would re-resolve. But no downstream stage re-resolves `source_url`; it's only set during AI source resolution or scraper stages, both of which run *before* the identity change detection.
- **Lesson:** When invalidating metadata due to identity change, fields that cannot be re-resolved by downstream stages must be actively re-resolved at the point of invalidation, not just cleared and hoped for.
- **Fix applied:** After clearing `source_url`, call `search_wikipedia(title, new_artist)` immediately to re-resolve the Wikipedia single URL under the corrected identity. The result is stored back into `metadata["source_url"]`.

### ANTI-PATTERN: Clearing metadata without re-resolution after identity change
When identity change invalidates metadata, simply setting fields to `None` is only safe if a downstream pipeline stage will re-derive them. For fields that are only populated by upstream stages (already completed), clearing without re-resolution creates permanent data loss. The invalidation handler must either re-resolve immediately or schedule re-resolution.

---

## Staged Pipeline Architecture — Bugs from Workspace Refactor (Session 20)

The pipeline was refactored from serial DB operations to a staged workspace architecture
(Stage A: status → Stage B: workspace build → Stage C: serial DB apply → Stage D: deferred threads).
This dramatically improved performance (~11x faster parallel imports) but introduced multiple bugs.

### FAILED: Workspace cleanup racing with deferred threads (Attempt 1)
- **What was tried:** `ws.cleanup_on_success()` was called in the main pipeline flow after Stage C (DB apply), before deferred threads started or while they were still running.
- **Why it failed:** `_deferred_entity_artwork` reads `entity_resolution` and `scraper_results` workspace artifacts. When cleanup runs before or during deferred execution, `shutil.rmtree` deletes the workspace directory, causing FileNotFoundError in the deferred threads. This silently broke all entity artwork, poster upgrades, and scene analysis.
- **Lesson:** Workspace lifecycle must extend beyond deferred task completion. Cleanup must be gated on ALL deferred threads finishing.
- **Fix applied:** Removed `ws.cleanup_on_success()` from main pipeline. Added a cleanup-coordinator thread in `dispatch_deferred()` that `join()`s all task threads (600s timeout) before calling cleanup.

### FAILED: `scrape_musicbrainz` default set to False in 3 places (Attempt 1)
- **What was tried:** `opts.get("scrape_musicbrainz", False)` in `_step_resolve_metadata`, `_step_resolve_entities`, and `_step_collect_source_links`.
- **Why it failed:** The `ImportOptions` schema has `scrape_musicbrainz: bool = True` as default, but opts.get with `False` fallback overrides this when the key isn't explicitly present in the options dict. MB was silently skipped for all imports unless explicitly enabled.

---

## Concurrency — Session 25: Global Serial Queue Bottleneck & ws.log() Contention

### FAILED: Global single-thread deferred queue for all jobs (Attempt 3 — Session 22→24)
- **What was tried:** After per-job threads caused cross-job SQLite contention (Attempt 2), all deferred tasks across all jobs were funnelled into a single global `queue.Queue` processed by one daemon worker thread (`deferred-global`). This guaranteed zero DB write contention from deferred tasks.
- **Why it failed:** With 10 videos, each video's deferred tasks take ~84s (preview 30s + matching 5s + kodi 1s + entity_artwork 40s + AI 5s + scene 5s). Total serial time: ~840s (14 minutes). Entity artwork for early jobs didn't complete until 6-8 minutes after import dispatch — users checking the UI during this window saw empty artwork tiles and reported "missing" artwork. Combined with ThreadPoolExecutor reduced from 4→2 (Session 24), total import time approximately doubled.
- **Evidence:** Job 8 (+44) dispatched deferred at 07:22:00, entity_artwork completed at 07:28:48 — 6m48s delay. All artwork confirmed valid in DB and on disk; it just took too long to appear.
- **Lesson:** Cross-job serialisation prevents DB lock errors but destroys throughput. The deferred queue must parallelise I/O-heavy work while serialising only the brief DB write phases.
- **Fix applied (Session 25):** Replaced global serial queue with per-video coordinator threads using `ThreadPoolExecutor(max_workers=4)`. All deferred tasks (preview, entity_artwork, scene_analysis, matching, AI, kodi_export) run in parallel within each video AND across videos. Entity artwork DB writes protected by `_apply_lock` (same lock as Stage C). Other DB-writing tasks use SQLite busy_timeout (30s) + retry-with-backoff. Combined with Stage B workers increased from 2→8 for maximum pipeline throughput.

### FAILED: ws.log() writing to DB on every log message (Attempt 1 — Session 20→24)
- **What was tried:** `ImportWorkspace.log()` wrote each log line to CosmeticSessionLocal (5s busy_timeout) on every call, appending to `ProcessingJob.log_text`.
- **Why it failed:** With 8 parallel Stage B workers + multiple deferred threads + Stage C, dozens of threads called `ws.log()` concurrently. Each call opened a CosmeticSessionLocal, did `job.log_text += line`, and committed. This created massive SQLite write contention — 60-90 cosmetic DB writes per import × 10+ concurrent videos = hundreds of short-lived write transactions competing for the process-global SQLite lock. The 5s busy_timeout on CosmeticSessionLocal was often insufficient.
- **Lesson:** High-frequency per-message DB writes from multiple threads are incompatible with SQLite's single-writer model. Batch writes to reduce transaction count.
- **Fix applied (Session 25):** Removed CosmeticSessionLocal DB write from `ws.log()`. Logs now write only to files (NDJSON + per-job file log). Added `ws.sync_logs_to_db()` which bulk-reads the per-job file log and writes to `ProcessingJob.log_text` in a single transaction — called once by the deferred coordinator after all tasks complete, and once before marking the job complete in stages.py.

### FAILED: Poster skip logic treating library_source same as scraper/artwork_pipeline (Attempt 1)
- **What was tried:** Entity artwork poster upgrade checks `_existing_poster and file_path and os.path.isfile(file_path)` — if any poster exists on disk, the CoverArtArchive upgrade is skipped.
- **Why it failed:** For library imports, the poster copied from the source directory gets `provenance="library_source"`. This is a user-provided file, potentially a random thumbnail or low-quality image. The skip logic treated it identically to a scraper-verified poster, preventing CoverArtArchive from upgrading it with correct album artwork. Example: 2CELLOS – Thunderstruck kept the wrong poster from the source directory.
- **Lesson:** Poster provenance must be checked before deciding to skip upgrades. Only authoritative sources (scraper, artwork_pipeline with verified art) should block CoverArtArchive upgrades.
- **Fix applied:** Added `and getattr(_existing_poster, "provenance", None) not in ("library_source",)` to the poster skip condition.

### ANTI-PATTERN: Global serialisation to avoid SQLite contention
Serialising all work through one thread eliminates contention but at unacceptable throughput cost. The correct approach is to parallelise I/O-heavy work (which doesn't need DB) and use targeted locking or retry only for the brief DB write phases. With SQLite WAL mode + 30s busy_timeout, concurrent short writes from retry-equipped workers are safe — the old 5s-timeout cosmetic writes and per-message logging were the real problem, not the deferred tasks themselves.

### ANTI-PATTERN: Per-message DB writes for log text
Appending to a DB field on every log message creates O(N) write transactions per import, where N is the number of log lines. With M concurrent imports, this produces O(N×M) competing write transactions. Instead, write logs to files during processing and bulk-sync to DB once at the end. This reduces DB write transactions from hundreds to one per import.
- **Lesson:** Default values in `opts.get()` must match the schema defaults. Better: use the Pydantic model directly instead of a raw dict.
- **Fix applied:** Changed all 3 occurrences from `opts.get("scrape_musicbrainz", False)` to `opts.get("scrape_musicbrainz", True)`.

### FAILED: YouTube source matching disabled in advanced mode (Attempt 1)
- **What was tried:** `_step_youtube_match` checked `if not opts.get("find_source_video", False): return` — skipping YouTube matching unless the option was explicitly set.
- **Why it failed:** `find_source_video` defaults to `False` in ImportOptions. Advanced mode users expect YouTube matching to run automatically.
- **Lesson:** Advanced mode should auto-enable features that are part of the advanced workflow.
- **Fix applied:** Changed to `if mode != "advanced" and not opts.get("find_source_video", False): return` — always runs in advanced mode.

### FAILED: Pipeline step status `"ok"` not recognized by frontend (Attempt 1)
- **What was tried:** `_coarse_update()` wrote `{"step": step, "status": "ok"}` to `pipeline_steps`.
- **Why it failed:** Frontend `PipelineStepsView` only recognizes `"success"` (green) and `"failed"` (red). `"ok"` fell through to gray badges, making all steps look inactive/pending.
- **Fix applied:** Changed `"ok"` to `"success"` in `_coarse_update()`.

---

## Track-Specific Metadata Fixes (Session 20)

### FAILED: MusicBrainz title similarity gate ignoring parenthetical subtitles (Attempt 1)
- **Track:** A Flock of Seagulls — I Ran (So Far Away)
- **What was tried:** `SequenceMatcher("i ran (so far away)", "i ran").ratio()` ≈ 0.40, failing the 0.6 title similarity gate. The title similarity check compared full strings without handling parenthetical subtitle differences.
- **Why it failed:** MusicBrainz may return the title as just "I Ran" while the query includes "(So Far Away)". The subtitle is not part of the canonical title on some MB entries.
- **Lesson:** Title comparison must handle parenthetical subtitles gracefully. If the base titles match after stripping parentheticals, the similarity should pass.
- **Fix applied:** Added parenthetical-aware comparison in 4 places: (1) title similarity gate in `_search_single_release_group`, (2) scoring loop in `_search_single_release_group`, (3) recording filter in Strategy 2, (4) `_confirm_single_via_artist`. When `title_sim < 0.6`, strips parentheticals from both strings; if base titles match or `sim >= 0.85`, uses the higher similarity.

### FAILED: Wikipedia `_pt_base` regex stripping essential parenthetical content from titles (Attempt 1)
- **Track:** Aerosmith — Dude (Looks Like a Lady)
- **What was tried:** `_pt_base = re.sub(r"\s*\(.*?\)\s*$", "", pt_lower)` strips trailing parenthetical content. For `"dude (looks like a lady)"`, `_pt_base = "dude"`, losing the actual title.
- **Why it failed:** The exact match bonus (`_pt_base == title_lower`) tries to compare the stripped page title against the full song title. When the song title itself contains parentheses, stripping them destroys essential information.
- **Lesson:** When the search query itself contains parentheses, the stripping heuristic is counterproductive. Must award exact-match bonus when `pt_lower == title_lower` directly.
- **Fix applied:** Added check: when `title_lower` contains `(` and `pt_lower == title_lower`, award +2 exact match bonus regardless of `_pt_base` result.

### FAILED: Wikipedia artist search for short/hyphenated names (Attempt 1)
- **Track:** a-ha — Take On Me
- **What was tried:** Searched Wikipedia for `"a-ha (band)"`. The hyphen in "a-ha" may be interpreted as a search operator by Wikipedia's API, splitting it into "a" minus "ha".
- **Why it failed:** Short names (≤4 chars) and hyphenated names produce ambiguous or empty Wikipedia search results.
- **Lesson:** Short/hyphenated artist names need a quoted search variant to prevent search operators.
- **Fix applied:** (1) Added quoted search term `'"a-ha" band'` at top of search_terms for short (≤4 chars) or hyphenated names. (2) Added hyphen-stripped and hyphen-to-space variants in `_artist_variants` for both `search_wikipedia()` and `search_wikipedia_artist()`.

### FAILED: CoverArtArchive poster overwriting valid scraper poster (Attempt 1)
- **Track:** 2CELLOS — Thunderstruck
- **What was tried:** `_deferred_entity_artwork` Section 3 fetches CoverArtArchive front cover for `item.mb_release_id` and creates new poster/thumb MediaAsset records, **deleting** existing pending ones. A valid scraper poster (from Wikipedia infobox) was overwritten by a different/wrong CAA cover.
- **Why it failed:** The poster upgrade unconditionally replaced any existing poster with the CAA cover, with no validation that the new poster was better or even correct. For cover songs (2CELLOS covering AC/DC's Thunderstruck), the MB release ID might map to a different edition's cover.
- **Lesson:** Poster upgrade should not overwrite an existing valid poster from the scraper pipeline. The scraper poster (from Wikipedia infobox) is typically more representative of the actual music video.
- **Fix applied:** Added check before poster upgrade: if a poster MediaAsset already exists with a valid on-disk file, skip the CoverArtArchive upgrade entirely.

### ANTI-PATTERN: Unconditional poster replacement in deferred enrichment
Deferred enrichment tasks that "upgrade" assets should not unconditionally replace existing valid assets. The upgrade should only fire when no asset exists or the existing one is invalid/missing.

---

## Deferred Task Concurrency — SQLite Locking (Session 21)

### FAILED: Running all deferred tasks as parallel threads with SQLite
- **What was tried:** `dispatch_deferred()` spawned one `threading.Thread` per deferred task (7 total: preview, matching, kodi_export, entity_artwork, orphan_cleanup, ai_enrichment, scene_analysis), all running concurrently. A cleanup thread joined all task threads (600s timeout each) then deleted the workspace.
- **Why it failed:** SQLite does not support concurrent writers. Multiple threads all attempted DB writes (INSERT, UPDATE, COMMIT) simultaneously, causing `sqlite3.OperationalError: database is locked`. Scene analysis failed on INSERT into `ai_scene_analyses`, matching failed on INSERT into `match_results`. Both errors were caught and logged to the per-job file log but some `ws.log()` DB writes also failed, so the DB `job.log_text` showed an incomplete picture — the per-job file log (`logs/jobs/{id}.log`) contained the full error trail.
- **Lesson:** With SQLite, deferred tasks that write to the DB must run sequentially, not in parallel. The per-job file log is more reliable than `job.log_text` for debugging deferred task failures because `ws.log()` itself can fail due to DB locking.
- **Fix applied:** Changed `dispatch_deferred()` to run all tasks sequentially in a single background thread instead of spawning one thread per task.

### FAILED: Orphan cleanup using `.videos` relationship on entity models
- **What was tried:** `_deferred_orphan_cleanup` queried `AlbumEntity.videos.any()` and `ArtistEntity.videos.any()` to find entities with zero linked videos.
- **Why it failed:** Neither `AlbumEntity` nor `ArtistEntity` have a `videos` relationship. `VideoItem` has `artist_entity_id` and `album_entity_id` FKs but no `back_populates` on the entity side, so `.videos` doesn't exist as a relationship attribute.
- **Lesson:** Always verify ORM relationships exist before using `.any()` or `.has()` in queries. Check both sides of the relationship definition.
- **Fix applied:** Replaced `.videos.any()` with explicit `EXISTS` subqueries: `~db.query(VideoItem).filter(VideoItem.album_entity_id == AlbumEntity.id).exists()`.

### FAILED: Mutation plan reading `mode` from wrong nesting level
- **What was tried:** `mutation_plan.py` line 79 read `input_data.get("mode", "simple")` to determine simple vs advanced mode for selecting deferred tasks.
- **Why it failed:** Child job `input_params` structure is `{"file_path": "...", "directory": "...", "options": {"mode": "advanced", ...}}`. The `mode` field is nested inside `options`, not at the top level. So `input_data.get("mode")` always returned `None`, defaulting to `"simple"`. This meant advanced deferred tasks (scene_analysis, kodi_export, entity_artwork, orphan_cleanup, ai_enrichment) were never dispatched — only `['preview', 'matching']` ran.
- **Lesson:** Always trace the actual data structure from creation to consumption. The `input_params` dict is built by `_dispatch_child_jobs()` in stages.py and consumed by `build_mutation_plan()` — these must agree on structure.
- **Fix applied:** Changed to `opts = input_data.get("options") or {}; mode = input_data.get("mode") or opts.get("mode", "simple")` to check both levels.

### FAILED: Per-job sequential deferred tasks still contend cross-job (Attempt 2)
- **What was tried:** Fix 9 (above) changed `dispatch_deferred()` to run all tasks sequentially in a single background thread per job. This eliminated intra-job concurrency but each job still spawned its own thread.
- **Why it failed:** When 10+ videos are imported in a batch, all jobs reach Stage D around the same time. Each job spawns its own background thread, and all threads write to SQLite concurrently. Despite WAL mode (`PRAGMA journal_mode=WAL`) and 30-second `busy_timeout`, the contention from 10+ concurrent writer threads exceeds the timeout. Entity artwork, matching, AI enrichment, and orphan cleanup all fail with `sqlite3.OperationalError: database is locked`. Critically, entity artwork downloads art files to disk successfully but the `db.commit()` to create `MediaAsset` records fails — so artwork exists on disk but is invisible to the application because no DB records point to it.
- **Evidence:** Jobs 2, 4, 5, 6, 8, 10, 11, 12 all hit DB lock errors during deferred tasks. Jobs 2, 6, 8 failed specifically on `DELETE FROM media_assets ... RETURNING id` in entity_artwork. Job 9 failed entirely in Stage C due to DB lock.
- **Lesson:** Per-job sequential is insufficient with SQLite during bulk imports. The serialisation boundary must be cross-job: ALL deferred tasks across ALL jobs must flow through a single writer thread.
- **Fix applied:** Changed `dispatch_deferred()` to enqueue tasks into a global `queue.Queue` processed by a single daemon worker thread (`deferred-global`). All deferred tasks from all jobs are serialised through this one thread, completely eliminating cross-job SQLite contention. Also added retry-with-exponential-backoff to entity_artwork, matching, AI enrichment, and orphan cleanup DB operations as a safety net against residual contention from API requests or main pipeline threads.

### ANTI-PATTERN: Assuming per-job serialisation is sufficient with SQLite
SQLite's write lock is process-global, not thread-local. Serialising tasks within a single job's thread only prevents intra-job contention. When multiple jobs run concurrently (batch imports), their individual threads still contend for the global write lock. The fix must match the lock's scope — a single global writer thread for all deferred DB operations.

### FAILED: Entity artwork status="pending" instead of "valid" (Attempt 1)
- **What was tried:** `MediaAsset` records were created with `status="pending" if (vr and vr.valid) else "invalid"` — inverted logic that set valid assets to "pending".
- **Why it failed:** Frontend's `assetUrl()` in `ArtworkTiles.tsx` filters for `status === "valid"`. Assets with `status="pending"` were invisible in the UI even though the files existed and were properly validated.
- **Lesson:** Always match DB status values to the frontend's filter expectations. Test the full stack (DB → API → frontend render) when assets appear missing.
- **Fix applied:** Changed to `status="valid" if (vr and vr.valid) else "invalid"` in both entity artwork creation (~line 228) and poster upgrade (~line 319) in `deferred.py`.

### FAILED: Poster upgrade only deleting status="pending" records, leaving stale library_source poster (Attempt 2 — Session 25)
- **Track:** 2CELLOS — Thunderstruck
- **What was tried:** The poster upgrade in `deferred.py` Section 3 correctly identified that a `library_source` poster should be replaced (the provenance check excluded `"library_source"` from the skip condition). It correctly fetched the single cover from CoverArtArchive (`release 5fe97f2e`). But the delete filter before inserting the new `MediaAsset` only removed records with `status="pending"`.
- **Why it failed:** The `library_source` poster had `status="valid"`, so it survived the delete. This created **two** poster records for the same video: the old wrong `library_source` one (lower ID) and the new correct `artwork_pipeline` one (higher ID). Both the backend's `get_poster()` endpoint (`.first()` → lower ID) and the frontend's `assetUrl()` (`.find()` → first in array) returned the old wrong poster.
- **Evidence:** Video ID=2 had poster ID=4 (`library_source`, wrong image from source directory) and poster ID=11 (`artwork_pipeline`, correct CoverArtArchive single cover). The frontend always displayed the wrong one.
- **Lesson:** When the skip check has already decided a poster upgrade should proceed, the delete before insert must remove ALL existing records of that asset type — not just pending ones. The skip check is the gatekeeper; the delete is the cleanup. Limiting the delete to `status="pending"` defeats the gatekeeper's decision.
- **Fix applied:** Changed the delete filter in Section 3 from `MediaAsset.status == "pending"` to no status filter — deletes all existing poster/thumb records for the video before inserting the upgraded version.

### FAILED: Poster upgrade Section 3 DB write with no retry — silently lost to SQLite contention (Attempt 3 — Session 26)
- **Track:** 2CELLOS — Thunderstruck
- **What was tried:** Section 3 of `_deferred_entity_artwork` in `deferred.py` correctly identified the single cover (`Using single cover art for video poster`), downloaded it, and attempted to DELETE the old `library_source` poster + INSERT the new `artwork_pipeline` poster. The entire Section 3 was wrapped in a single `try/except` with no retry loop.
- **Why it failed:** During batch imports, multiple deferred tasks across all jobs contend for the SQLite write lock. Section 2b (entity artwork persist) already had retry logic (`for _attempt in range(_MAX_DB_RETRIES + 1)`), but Section 3 (poster upgrade) did not. The `db.commit()` inside `with _apply_lock:` raised `sqlite3.OperationalError: database is locked`, the except clause rolled back and logged a warning, and the function exited — leaving the wrong `library_source` poster in place.
- **Evidence:** Job 4 file log (`logs/jobs/4.log`) shows the pattern repeated across ALL four import attempts for 2CELLOS: `[14:32:32]`, `[14:48:24]`, `[15:00:28]`, `[15:55:15]` — each time `Using single cover art for video poster` followed by `Poster upgrade: (sqlite3.OperationalError) database is locked`. The poster was **never** replaced despite the logic being correct.
- **Lesson:** Every DB write in deferred tasks must have retry-with-backoff. SQLite contention during batch imports is not a rare edge case — it is the normal operating condition. A single-attempt DB write inside a deferred task will fail silently under load.
- **Fix applied:** Added retry loop (`for _poster_attempt in range(_MAX_DB_RETRIES + 1)`) with exponential backoff (1s, 2s, 4s) around the poster upgrade DB write in Section 3, matching the pattern already used in Section 2b.

### ANTI-PATTERN: Retry inconsistency within the same function
When a function has multiple DB write sections, all sections must have the same retry strategy. Section 2b had retry-with-backoff but Section 3 (just 30 lines below) did not. During code review, verify all DB writes in deferred tasks have retry logic — not just the ones that failed first.

---

## Import Queue Hang — Recurring SQLite DB Lock in Stage C (Sessions 23–24)

### FAILED: `_coarse_update` silently swallowing DB lock errors on terminal transitions (Attempt 1 — Session 23)
- **What was tried:** `_coarse_update(job_id, JobStatus.failed, ...)` in `stages.py` attempted to write the terminal status to the DB. When the write failed due to `database is locked`, the exception was caught and logged but the job status was never updated to `failed`.
- **Why it failed:** The parent batch watcher (`complete_batch_job_task`) counts terminal-state children to determine when the batch is complete. Children stuck in `analyzing` (because their `_coarse_update(failed)` silently failed) were never counted as terminal, so the parent watcher looped forever — the import queue appeared hung.
- **Evidence (Session 23):** 4 child jobs (11, 20, 23, 25) stuck in `analyzing`. Parent Job 1 showed "25/27 complete (1 failed)" but never progressed.
- **Fix applied (Session 23):** Added retry logic to `_coarse_update()` — 5× with exponential backoff (0.5s, 1s, 2s, 4s) for terminal status transitions (`failed`, `complete`). Each retry creates a fresh DB session to avoid stale connection state.

### FAILED: `_coarse_update` retry alone insufficient — Stage C operation itself fails (Attempt 2 — Session 24)
- **What was tried:** Session 23's `_coarse_update` retry ensured terminal status writes eventually succeed. But the import queue hung again.
- **Why it failed:** The `_coarse_update` retry only fixes the *symptom* (status not written) not the *cause* (Stage C's `apply_mutation_plan()` failing with `database is locked`). When `_execute_plan()` inside `apply_mutation_plan()` fails, it raises an exception. The exception handler calls `_coarse_update(failed)` — which now succeeds thanks to the Session 23 retry. But the *job's actual work* was lost. More critically, if the DB lock contention is severe enough, even the retried `_coarse_update` can fail (all 5 attempts exhausted), leaving the job stuck.
- **Evidence (Session 24):** Job 21 (Aerosmith — Dude Looks Like a Lady) failed with `sqlite3.OperationalError: database is locked` on `INSERT INTO video_items` during Stage C. Job 19 (Adele — Rolling in the Deep) stalled between entity resolution and Stage C — likely hit the same contention. Parent Job 1 stuck at "25/27 complete (1 failed)".
- **Root cause:** ThreadPoolExecutor ran 4 concurrent worker threads. Stage C's `_execute_plan()` holds a SQLite RESERVED lock from first `db.flush()` through entity creation to final `db.commit()`. With 4 threads, up to 4 Stage C operations could contend simultaneously. Additionally, cosmetic writes from other threads (`_coarse_update`, `_append_job_log`, workspace `ws.log()`) via `CosmeticSessionLocal` (5s busy_timeout) compete for the same write lock. The combination of long Stage C transactions + concurrent cosmetic writes from multiple threads can exhaust even the 30s main busy_timeout.
- **Lesson:** Defense-in-depth is required. Retrying the status write is necessary but not sufficient. The Stage C operation itself must retry on DB lock, AND the source of contention (too many concurrent threads) must be reduced.
- **Fix applied (Session 24):**
  1. **`apply_mutation_plan()` retry** in `db_apply.py` — 3× with exponential backoff (1s, 2s, 4s) on `database is locked`. Releases `_apply_lock` between retries so other threads can make progress.
  2. **ThreadPoolExecutor concurrency reduced** in `tasks.py` — `max_workers` changed from 4 to 2. Halves the number of concurrent Stage C operations competing for the SQLite write lock.
  3. **Stuck-child watchdog** (from Session 23) — `complete_batch_job_task` force-fails children stuck in the same non-terminal state for >5 minutes. Acts as a safety net if all other retries fail.

### ANTI-PATTERN: Retrying only the error reporter, not the failing operation
When an operation fails and the error-reporting mechanism also fails (e.g., Stage C fails with DB lock, then `_coarse_update(failed)` also fails with DB lock), fixing only the error reporter creates a false sense of safety. The next failure will successfully report "failed" but the job's work is still lost. The operation itself must also retry to actually complete the work, not just report the failure more reliably.

### ANTI-PATTERN: High thread concurrency with SQLite write-heavy workloads
SQLite's write lock is process-global — only one writer at a time. Running 4+ concurrent threads that all perform write-heavy operations (Stage C mutations, cosmetic status updates, job log writes) creates a contention storm. With WAL mode and `busy_timeout=30000ms`, SQLite can handle modest concurrency, but 4 threads each holding RESERVED locks for multi-second transactions will exhaust the timeout. For SQLite-backed applications with long write transactions, limit concurrency to 2 threads maximum and add retry-with-backoff to all critical write paths.

---

## AJR — "I'm Ready" — Incorrect MB Single Link (Compilation Instead of EP)

### Root Cause: Strategy 1 rejects EPs; Strategy 2 picks compilations

**Problem:** AJR "I'm Ready" has `mb_release_id` pointing to "So Fresh: The Hits of Spring 2014" (`a7e1a4a8`) — an Album+Compilation by Various Artists. The correct release is the "I'm Ready" EP by AJR.

**Two bugs:**

1. **Strategy 1 (`_search_single_release_group`) rejects EPs.** The MB search uses `primarytype: "single"` and the scoring loop has `if rtype != "single": continue`. AJR's "I'm Ready" release group on MB has type **"EP"**, not "single". The EP is the #1 search result (score=100, exact title+artist match) but is filtered out. All other "I'm Ready" results are by different artists and fail artist similarity checks. Strategy 1 returns None.

2. **Strategy 2 (`_pick_best_release`) prefers compilations over EPs.** `_RELEASE_TYPE_PRIORITY` has `album: 1, ep: 2` — so album-type compilations (like "So Fresh: The Hits of Spring 2014", primary-type "album") beat EP releases (primary-type "ep"). The recording appears on both EP releases and compilation albums; the compilation wins.

**Fix applied:**
- **`_pick_best_release`**: Added `allowed_types` parameter. When set (e.g., `{"single", "ep"}`), only releases with matching release-group primary-type are considered. Returns None when no release passes the filter.
- **Strategy 1**: Changed search to try `primarytype: "single"` first, then fall back to `primarytype: "ep"` if no match. EPs get a -20 score penalty so singles are still preferred when both exist.
- **Strategy 2 in `musicbrainz.py`**: `search_track` and `get_track` now call `_pick_best_release(releases, allowed_types={"single", "ep"})` — compilations and albums can never be selected as `mb_release_id`.
- **`unified_metadata.py`**: AI-provided recording validation now accepts EPs in addition to singles.

---

## Track: A Great Big World & Christina Aguilera — Say Something (Session 27)

### FAILED: Duplicate detection using exact artist string comparison (Attempt 1)
- **What was tried:** All three duplicate detection layers (scan endpoint `_find_existing_video`, pipeline `_step_duplicate_precheck`, db_apply TOCTOU defense) compared the incoming artist string against DB records using exact case-insensitive matching (`func.lower(artist) == artist.lower()` or `VideoItem.artist.ilike(artist)`).
- **Why it failed:** Filename `A Great Big World Christina Aguilera - Say Something [1080p].mp4` is parsed to artist=`A Great Big World Christina Aguilera` (no `feat.`/`featuring` separator). DB records have `A Great Big World feat. Christina Aguilera` and `A Great Big World featuring Christina Aguilera`. Exact string comparison fails for all three variants because the separator formats don't match. `parse_multi_artist` correctly handles `feat.` and `featuring` but NOT a space-only separator (the filename form).
- **Lesson:** Duplicate detection must normalize featured artist separators. When the incoming artist has no recognized separator, the full string may be `{primary} {featured1} {featured2}` — a prefix of the primary artist from the DB record should still match.
- **Fix applied:** Added primary-artist-prefix fallback to all three duplicate check locations. After exact match fails, query by title only, then use `parse_multi_artist` on both the query and each candidate's artist string. If either primary artist starts with the other (case-insensitive), treat as duplicate. This catches `A Great Big World Christina Aguilera` matching `A Great Big World` (from `A Great Big World feat. Christina Aguilera`).

### ANTI-PATTERN: Relying on exact string matching for collaborative artist names
Artist names with featured credits can be formatted many ways (`feat.`, `featuring`, `ft.`, `&`, space-only, etc.). Any comparison that uses the full formatted string will fail when the same collaboration is written differently. Always normalize to primary artist before comparison.

---

## Track: Adam Cohen — We Go Home (Session 27)

### FAILED: Poster upgrade has no fallback when mb_release_id is None but _has_parent_album is True (Attempt 1)
- **What was tried:** `_deferred_entity_artwork` in `deferred.py` has a poster upgrade block. When `_has_parent_album` is True, it only tries CoverArtArchive single cover via `item.mb_release_id`. When `_has_parent_album` is False, it uses `art_result["album_image_url"]` as the poster.
- **Why it failed:** After the parent album resolution fix (adding `mb_album_release_group_id` in Strategy 2), Adam Cohen's `_has_parent_album` became True. But `item.mb_release_id` remained None (no single release found on MusicBrainz). The code enters the `_has_parent_album` branch, checks `if item.mb_release_id:` → False, and falls through without setting `_video_poster_url`. No poster is created. The video has `thumb` and `video_thumb` assets but no `poster` asset — the frontend's `/poster/{id}` endpoint returns 404.
- **Lesson:** The poster upgrade logic must have a fallback for when no CoverArtArchive source is available. If no poster URL can be resolved, the existing video thumbnail should be promoted to a poster asset.
- **Fix applied:** Added a "thumb fallback" block after the poster upgrade section. If no poster asset exists after the upgrade attempt, copies the existing `thumb` asset to a `-poster.jpg` file and creates a `poster` MediaAsset with provenance `thumb_fallback`.

### ANTI-PATTERN: Missing fallback in conditional chains
When a conditional chain (`if has_album: try CAA`, `elif no album: use art_result`) has branches that can produce no output (CAA tried but mb_release_id is None), a final fallback must ensure the minimum expected output (a poster) is always produced. Conditional chains without a catch-all fallback create silent gaps where the expected artifact is never created.
- **`tasks.py`**: Direct `_pick_best_release` call also uses `allowed_types={"single", "ep"}`.
- **Old pipeline guard**: `_confirm_single_via_artist` guard condition updated to also accept EPs (`rel_type not in ("single", "ep")`).

### ANTI-PATTERN: Allowing `mb_release_id` to point to compilations or albums for music video tracks
Music video tracks should only have `mb_release_id` pointing to single or EP releases. When `_pick_best_release` is used without type filtering in track contexts, it can select compilations (which have primary-type "album") because they have higher priority than EPs in the default sort order. Always pass `allowed_types={"single", "ep"}` in track search contexts.

---

## Import Pipeline — Job Hang on Failure (Adam Cohen – We Go Home)

### FAILED: Relying on pipeline exception handler to mark jobs as failed during batch contention (Attempt 1)
- **What was tried:** `run_library_import_pipeline` has `except Exception` → `_coarse_update(job_id, JobStatus.failed, ...)` (10 retries, exponential backoff) → finally `_ensure_terminal(job_id)` (20 retries). This is the only mechanism that transitions a failed child job to `JobStatus.failed`.
- **Why it failed:** During a 29-video batch import, SQLite write contention was so severe that ALL retries across both `_coarse_update` (10 attempts) and `_ensure_terminal` (20 attempts) failed with `database is locked`. The job remained stuck in `JobStatus.analyzing` forever. The batch monitor detected the stuck child (>300s) but only logged warnings — it never force-failed the child.
- **Root error:** `'NoneType' object has no attribute 'get'` in `_step_build_mutation_plan` (stage B15). A workspace artifact (`parsed_identity` or `organized`) was None because the first pipeline run failed partway through, leaving incomplete artifacts. The second run hit the same NoneType error on a different code path.
- **Lesson:** The pipeline exception handler cannot be the sole mechanism for marking failed jobs — DB locks during batch contention can prevent ALL retries from succeeding. A separate, independent mechanism (like the batch monitor) must be able to force-fail stuck children.
- **Fix applied:**
  - **Batch monitor force-fail** (`tasks.py`): Added `FORCE_FAIL_THRESHOLD = 600` (10 minutes). When the batch monitor detects a child stuck in `analyzing` longer than this threshold, it opens a fresh DB session and force-sets `status = JobStatus.failed` directly, bypassing the pipeline's exception handler entirely. The existing `STUCK_THRESHOLD = 300` (5 minutes) still generates log warnings.
  - **Defensive artifact guards** (`stages.py`): All `ws.read_artifact()` calls that lacked null guards now use `or {}` fallback. Specifically: `parsed_identity`, `organized` (both library and URL flows). Access via `.get()` instead of `[]` with explicit `RuntimeError` if the file path is missing after normalization.

### ANTI-PATTERN: Batch monitor that only warns about stuck children but never acts
A batch monitor that detects stuck children but only logs warnings provides no recovery. If the only mechanism to mark a job as failed (the pipeline exception handler) cannot reach the DB, the job will hang indefinitely. The monitor must have a force-fail capability with its own independent DB session.

---

## Poster Upgrade — DB Lock Prevents CoverArtArchive Swap (2CELLOS – Thunderstruck)

### FAILED: Poster upgrade with 3 retries and exponential backoff during batch import (Attempt 1)
- **What was tried:** `_MAX_DB_RETRIES = 3` with `delay = 2 ** attempt` (1s, 2s, 4s total = 7s of waiting). After downloading the CoverArtArchive single cover to a `-pending-{ts}.jpg` file, the code retries the DB transaction (delete old MediaAsset, insert new) up to 3 times.
- **Why it failed:** During a 29-video batch import, 7 seconds of total retry time was insufficient. The poster swap DB transaction failed all 3 attempts. The new 67KB CoverArtArchive poster was left on disk as a `-pending-` file, and the DB still pointed to the old 14KB NFO-source poster (with empty `source_url`).
- **Lesson:** Poster upgrade retry timing must be generous enough for batch contention. Exponential backoff with a low retry count (3) and short base (2^n) is insufficient. Linear backoff with more retries provides better coverage.

---

## Track: Julien Baker — Something

### FAILED: AI recording title validation alone prevents wrong MB association (Attempt 1)
- **What was tried:** Added SequenceMatcher title validation (threshold 0.5) in Step A of `_scrape_with_ai_links` to reject AI-provided MusicBrainz recording IDs when the recording title doesn't match. This correctly rejected the "Intro" recording (from the Spotify Sessions EP) that the AI provided.
- **Why it failed:** Step A rejection worked, but Step B (search-based MB fallback) used `ai_result.identity.title` as the search query. The AI had identified the title as "Spotify Sessions" (the EP name, not the track name), so Step B searched MusicBrainz for "Spotify Sessions" and found the EP as a perfect match. The original parsed title "Something" was already overwritten by the AI title in `resolve_metadata_unified` (line ~658: `title = ai_source_result.identity.title`), so Step B had no access to the correct title.
- **Cascading effects:** Wrong MB single (Spotify Sessions EP d37e2d6a) → wrong album entity "Spotify Sessions" → wrong CoverArtArchive poster → wrong poster art and album art.
- **Lesson:** When the AI's recording ID is rejected by title validation, the AI's proposed title must also be distrusted for the search fallback. The original parsed title (from filename/platform) must be preserved before AI override and passed through to `_scrape_with_ai_links` as a fallback for Step B searches.
- **Fix applied:** (1) Preserve `_original_parsed_title = title` before AI override in `resolve_metadata_unified`. (2) Add `parsed_title` parameter to `_scrape_with_ai_links`. (3) In Step B, detect when AI recording was rejected (`_ai_recording_rejected`) and use `parsed_title` instead of `ai_result.identity.title` for the MB search query. Applied to both `pipeline_url` and `pipeline_lib`.
- **Fix applied:**
  - **Increased retries** (`deferred.py`): `_MAX_DB_RETRIES` changed from 3 to 5. Delay changed from `2 ** attempt` (1,2,4s) to `2 * (attempt + 1)` (2,4,6,8,10s = 30s total waiting).
  - **Cleanup on failure**: If poster upgrade fails after all retries, pending files (`*-poster-pending-*.jpg`, `*-thumb-pending-*.jpg`) are removed from disk to prevent orphans.
  - **Tracking flag**: Added `_poster_upgraded` boolean to track success vs exhausted retries.

### FAILED: Poster upgrade leaves pending files with "-pending-" suffix in final paths (Attempt 2)
- **What was tried:** The poster upgrade downloads to `-poster-pending-{ts}.jpg`, then on successful DB commit, stores the pending path as the final `file_path` in the MediaAsset record. No rename step.
- **Why it failed:** The DB points to files with `-pending-{ts}` in their names. While functionally correct (the files exist and contain the right data), the naming is inconsistent with library conventions (`-poster.jpg`, `-thumb.jpg`). If the system later checks for standard poster names, these files may be missed.
- **Lesson:** After a successful poster upgrade DB commit, pending files must be renamed to their canonical names (`-poster.jpg`, `-thumb.jpg`), the old files must be deleted, and the DB records must be updated with the final paths.
- **Fix applied:** Added a finalize step after successful DB commit: renames pending files to canonical names (overwriting old files), then updates DB `file_path` records. If the rename fails, the system still works via the pending path — the rename is best-effort.

---

## Adam Cohen — "We Go Home" — Wikipedia and MB Links Misclassified as "Single"

### FAILED: Hardcoding source_type="single" for all Wikipedia and MusicBrainz links (Attempt 1)
- **What was tried:** `_step_collect_source_links` in `stages.py` stored all Wikipedia URLs as `source_type: "single"` and all MusicBrainz recording URLs as `source_type: "single"`, regardless of what the page/release actually was.
- **Why it failed:** For Adam Cohen – "We Go Home", Wikipedia only has an article about the **album** (not the song/single). The page at `https://en.wikipedia.org/wiki/We_Go_Home` is clearly classified as an album ("Studio album by Adam Cohen", 11 tracks, Wikipedia category "2014 albums"). `classify_wikipedia_page()` correctly detects `"album"` via the "studio album by" infobox indicator — but the page_type was never propagated from the scraper result into the metadata dict, and `_step_collect_source_links` hardcoded `"single"` anyway. Similarly, the MB recording `af30077d-f280-446e-98b1-7c95834c53f3` only appears on a compilation album ("Mehmet Scholl: Miss Milla 2") — there is no single release of this track on MusicBrainz. The pipeline stored it as `source_type: "single"` because it treated all MB recordings as singles.
- **Lesson:** Source links must be classified by their actual content type, not assumed to be "single". Wikipedia pages have a `page_type` from `classify_wikipedia_page()` that must be propagated and used. MB recordings without a release group (no single/EP found by Strategy 1) should be labeled as "recording", not "single". This is the same anti-pattern as the AJR compilation fix: assuming everything is a single without verifying the actual release type.
- **Additional bug:** The `Source` model had a `@validates("source_type")` coercion that converted `"recording"` → `"single"`, making it impossible to store the correct type.
- **Fix applied:**
  - **`unified_metadata.py`**: Both AI-URL and search-based Wikipedia scraping paths now propagate `wiki["page_type"]` into `metadata["wiki_page_type"]`.
  - **`stages.py`**: `_step_collect_source_links` reads `metadata.get("wiki_page_type")` and classifies Wikipedia links as `"album"`, `"artist"`, or `"single"` accordingly. MB recording links are stored as `source_type: "recording"` when no `mb_release_group_id` exists (no single release group found); `"single"` only when `mb_release_group_id` is set (confirmed single/EP).
  - **`tasks.py`**: Old code path updated — MB recording source_type determined by whether `video_item.mb_release_id` is set (single/EP found → "single") or not (recording only → "recording").
  - **`models.py`**: Removed `"recording"` → `"single"` coercion from `Source._validate_source_type`.
  - **Frontend**: Added `"recording"` as a display category with label "Recording" in `MetadataEditorForm.tsx`, `ActionsPanel.tsx`, and `SourceEditorModal.tsx`. Removed `recording → single` remapping.

### ANTI-PATTERN: Assuming all Wikipedia pages about a song's title are about the single
When a song shares its name with an album, Wikipedia search may return the album article. `classify_wikipedia_page()` correctly detects the page type, but this classification must be propagated and used for `source_type` — never hardcode `"single"` for Wikipedia links.

### ANTI-PATTERN: Labeling MB recordings as "single" when no single release exists
A MusicBrainz recording is not a single — it's a recording that may appear on any type of release (single, EP, album, compilation). When `_search_single_release_group` finds no single and `mb_release_group_id` is null, the recording link should be typed as `"recording"`, not `"single"`.

---

## Batch Import Bug Fixes (March 2026)

**Investigation context**: 29-video batch import from `D:\MV2` completed successfully but revealed 4 code bugs:

### BUG FIX: `scenes_analyzed` processing state never set (Scene button always white)
- **Symptom**: Frontend ThumbnailsPanel "Analyze Scenes" button stayed white/ghost for all 29 imports despite scene analysis completing successfully.
- **Root cause**: `_deferred_scene_analysis` in `deferred.py` marked `"thumbnail_selected"` but never marked `"scenes_analyzed"`. The frontend checks `isStepDone("scenes_analyzed")`.
- **Evidence**: All 29 videos had `scenes_analyzed: {}` (empty) but all had `thumbnail_selected` populated.
- **Fix applied**: Added `_mark_processing_state(db, video_id, "scenes_analyzed", method="scene_analysis")` before the existing `thumbnail_selected` mark in `_deferred_scene_analysis`.

### BUG FIX: Unicode hyphens break artist searches (a‐ha missing artist art)
- **Symptom**: a-ha "Take On Me" had no artist art despite MB/wiki sources existing. Job log: `No artist image found for: a‐ha`.
- **Root cause**: NFO/metadata contained Unicode hyphen U+2010 (`‐`) in "a‐ha". All search functions only normalized ASCII hyphen `-`, so:
  - MusicBrainz `SequenceMatcher` comparing "a‐ha" vs "a-ha" fell below 0.60 threshold
  - Wikipedia `if "-" in artist` check missed U+2010, so no hyphen variants were generated
- **Fix applied**:
  - `metadata_resolver.py`: Added `_UNICODE_HYPHENS = re.compile(r'[\u2010\u2011\u2013\u2014\u2212]')` normalization at entry of `search_wikipedia()`, `search_wikipedia_artist()`, and `search_wikipedia_album()`.
  - `artist_album_scraper.py`: Added same normalization before `SequenceMatcher` comparison in `search_artist_musicbrainz()`.

### ANTI-PATTERN: Only normalizing ASCII hyphens in text matching
Unicode text from NFO files, web scraping, and metadata APIs can contain Unicode hyphens (U+2010 HYPHEN, U+2011 NON-BREAKING HYPHEN, U+2013 EN DASH, U+2014 EM DASH, U+2212 MINUS SIGN) that look identical to ASCII `-` but fail string comparisons. Always normalize Unicode hyphens to ASCII at search function entry points.

### BUG FIX: AI enrichment silently skipped with no warning
- **Symptom**: All 29 videos showed "ai_enrichment completed" same second as dispatch, with no AI-generated descriptions.
- **Root cause**: `get_ai_provider()` returned None (settings table empty, no API key). `_deferred_ai_enrichment` called `_mark_processing_state("ai_enriched")` even when `enrich_video_metadata` returned None.
- **Fix applied**: Added check for None return from `enrich_video_metadata()` — logs `"AI enrichment skipped: no AI provider configured"` at warning level and returns early (no false `ai_enriched` state).

### BUG FIX: No `search_wikipedia` call for single/song pages
- **Symptom**: Only 2 of 29 tracks had `wikipedia_single` sources. The rest only had artist and album Wikipedia links.
- **Root cause**: `_step_collect_source_links` only ran `search_wikipedia_artist()` and `search_wikipedia_album()`. The `wikipedia_single` source was only created when the main `source_url` from unified metadata happened to be a single/song page — no dedicated single search existed.
- **Fix applied**: Added a `search_wikipedia(title, artist)` call in `_step_collect_source_links` when `"wikipedia_single"` is not already in links, creating the source with `source_type: "single"`.

### ANTI-PATTERN: Assuming source_url covers all Wikipedia page types
The scraper's `source_url` covers whichever page type the scraper happened to find first (often the album or artist). A dedicated search for each page type (artist, album, single) is needed to populate all three source categories reliably.

---

## Track: Adam Cohen — We Go Home

### BUG FIX: recording→single coercion hid MusicBrainz recording sources
- **Symptom**: MusicBrainz recording link for Video 15 appeared in the "single" column in the frontend instead of "recording". The DB stored `source_type="recording"` correctly, but the API returned `"single"`.
- **Root cause**: Four separate places coerced `"recording"` → `"single"`:
  1. `schemas.py` `SourceOut._normalize_source_type()` — API serialization layer changed recording to single before sending to frontend
  2. `metadata_service.py` — AI source matching coerced recording→single when building match keys and proposed source keys
  3. `source_validation.py` `normalize_source_type()` — legacy coercion in the validation function
  4. `source_validation.py` `infer_source_type_from_url()` — returned "single" for MusicBrainz recording URLs
  Additionally, `"recording"` was missing from `VALID_SOURCE_TYPES` in source_validation.py.
- **Fix applied**:
  - `schemas.py`: Removed `if self.source_type == "recording": self.source_type = "single"` from `_normalize_source_type()`
  - `metadata_service.py`: Removed recording→single coercion in `requested_keys` and `_key` construction
  - `source_validation.py`: Added `"recording"` to `VALID_SOURCE_TYPES`, removed legacy coercion, fixed `infer_source_type_from_url()` to return `"recording"` for MB recording URLs
  - Frontend already supported "recording" as a category — no frontend changes needed

### BUG FIX: Album cover art used as video poster when Wikipedia page is an album
- **Symptom**: Video 15 poster showed the album cover art instead of a YouTube thumbnail or proper single cover.
- **Root cause**: `stages.py` `_step_fetch_artwork()` unconditionally used `metadata["image_url"]` from Wikipedia as the poster. When the Wikipedia scraper found an album page (not a single page), the album cover was downloaded and set as the video poster. The poster upgrade in `deferred.py` Section 3 correctly skips upgrade when `mb_release_id == mb_album_release_id` (no separate single release), but the initial poster was already wrong.
- **Fix applied**: Added guard in both `_step_fetch_artwork()` and `_step_fetch_artwork_url()`: `if metadata.get("wiki_page_type") == "album": image_url = None`. This skips using the Wikipedia image when it's from an album page, falling back to YouTube thumbnail for URL imports or no poster for library imports.
- **Data fix**: Removed incorrect poster asset (id=80) from Video 15 via script.

### ANTI-PATTERN: Coercing recording→single across multiple layers
The recording→single coercion existed in 4 separate places (serialization, AI matching, validation, URL inference). When a new source type is introduced, all layers must be updated consistently. Coercion at the API serialization layer (`schemas.py`) is especially dangerous because it silently transforms correct DB values into incorrect API responses — making the bug invisible to backend debugging.

### ANTI-PATTERN: Using Wikipedia image as poster without checking page type
Wikipedia infobox images are page-type-dependent. An album page's image is the album cover, not a suitable video poster. The pipeline must check `wiki_page_type` before using the scraped image as a poster — album covers should only be used for album entity art, never as video posters.


---

## Entity Artwork Cross-Contamination -- Race Between ai_enrichment and entity_artwork (Session 27)

### Affected: 100 gecs, 5 Seconds of Summer, 4 Non Blondes, Adele, and others across entire library

### FAILED: Running ai_enrichment and entity_artwork concurrently in deferred dispatch (Attempt 1)

**Symptom:** After a full re-import of ~17 videos, nearly ALL artist CachedAssets had the wrong image. 100 gecs had an unrelated artist's photo. 5 Seconds of Summer had 4 Non Blondes' image. Two Adele songs had album art from "I Don't Want to Miss a Thing". SHA-256 hashes confirmed every entity N's disk file contained entity (N-1)'s original image -- a systematic one-off chain shift.

**Bug A (Chain Shift):** Entity N's CachedAsset file on disk contained entity (N-1)'s image. This affected almost all artist entities created during the import.

**Bug B (Same URL):** Some CachedAsset records had identical `source_url` values for completely different entities -- a consequence of Bug A contaminating the source_url metadata.

**Root cause -- race condition in `dispatch_deferred()`:**

1. Stage C creates entities with auto-increment IDs. E.g., Video 7 (5 Seconds of Summer) triggers `get_or_create_artist()` which creates `ArtistEntity#6` with name "5 Seconds of Summer".

2. `dispatch_deferred()` submits ALL deferred tasks to `ThreadPoolExecutor(max_workers=4)` in parallel -- including both `entity_artwork` and `ai_enrichment`.

3. `entity_artwork` for Video 7 reads `item.artist_entity_id` (entity #6), reads the workspace `entity_resolution` artifact (which has 5SOS artwork URLs from Phase 1 resolver), and starts downloading the 5SOS image to `PlayarrCache/assets/artists/6/poster.jpg`.

4. **CONCURRENTLY**, `ai_enrichment` for Video 6 (+44) runs AI correction. The AI calls `get_or_create_artist(db, corrected_name)` which reassigns entity #6 from "5 Seconds of Summer" to "+44" (or creates a new entity and re-links the video).

5. Now entity #6's cache directory contains the 5SOS image, but entity #6 IS +44 -- cross-contamination. 5SOS gets a new entity #7, but entity #7's cache may already have the NEXT entity's image from the same race -- hence the chain shift pattern.

6. Additionally, the workspace `entity_resolution` artifact (written during Stage C) becomes stale after AI correction changes artist identity, but `entity_artwork` still reads it, downloading artwork for the pre-correction identity.

**Log evidence (2025-03-16 reimport):**
- 20:45:25: ALL entities 1-16 deleted ("Cleaned up orphaned artist entity")
- 20:47-20:55: Reimport creates new entities sequentially via Stage C
- 20:55:39: Video 7 (5SOS) Stage C creates entity #6
- 20:55:42: `artists/6/poster.jpg` saved with 5SOS URL (entity_artwork Phase 1)
- 20:56:15: AI correction for Video 6 changes entity #6's identity (ai_enrichment)
- 20:56:46: 5SOS gets NEW entity #7 (auto_import)
- 20:56:47: `artists/7/poster.jpg` saved with 4 Non Blondes URL (chain shift -- entity #7 got entity #6's pre-correction artwork)

**Fix applied (three changes in `deferred.py`):**
1. **Phase 1/Phase 2 split in `dispatch_deferred()`:** AI enrichment now runs synchronously FIRST (Phase 1) before all other deferred tasks (Phase 2). This ensures entity IDs are stable before `entity_artwork` reads them.
2. **`db.refresh(item)` in `_deferred_entity_artwork()`:** After initial DB query, refresh the VideoItem to pick up any entity reassignments from Phase 1.
3. **Staleness guard:** Compare workspace `entity_resolution` artifact artist/album names against current DB entity names. If AI enrichment changed the identity (names don't match), skip the Phase 1 resolver asset download -- the stale URLs would download the wrong artist's artwork.

**Data fix:** Deleted all 43 CachedAsset records and cleared 409 files from `PlayarrCache/assets/`. Next import will re-download correct artwork with the race condition eliminated.

### ANTI-PATTERN: Running identity-mutating tasks concurrently with identity-consuming tasks
`ai_enrichment` can reassign entity IDs (identity mutation). `entity_artwork` reads entity IDs and downloads artwork (identity consumption). Running them concurrently via ThreadPoolExecutor creates a TOCTOU race -- entity_artwork reads entity ID X, then ai_enrichment changes what entity X represents, then entity_artwork writes artwork for the old identity to entity X's cache path. Identity-mutating tasks must complete before any identity-consuming task begins.

### ANTI-PATTERN: Trusting workspace artifacts after identity mutation
Workspace artifacts (like `entity_resolution`) are written during Stage C and are immutable thereafter. When `ai_enrichment` changes an entity's identity in the DB, the workspace artifact still contains the pre-correction entity resolution data (artwork URLs, names). Any deferred task that reads workspace artifacts must validate them against current DB state, not trust them blindly.

---

## Wikipedia Search: Parenthetical Title Matching

### FAILED: Title similarity gate stripping parens from page title only (Attempt 1)
- **What was tried:** `search_wikipedia()` had a title similarity gate at 0.6. The gate stripped parentheticals from the **page title** only (e.g. "Scatman (Ski-Ba-Bop-Ba-Dop-Bop)" → "Scatman") then compared against the full search title "scatman (ski-ba-bop-ba-dop-bop)". Similarity was 0.37 < 0.6, so the correct page was rejected.
- **Why it failed:** The stripping was asymmetric — page title was cleaned but search title was not. When both titles have parentheticals that match, stripping parens from only one side produces a misleading low similarity.
- **Lesson:** Title normalization for comparison must be symmetric. Either strip both sides or compare both raw and normalized forms, taking the best match.
- **Fix applied:** Added three-way comparison: (1) `_sim_full`: full title vs full page title (catches exact parenthetical matches); (2) `_sim`: symmetric paren-stripping on both title and page title; (3) `_sim_norm`: existing normalized comparison. Final similarity = max of all three. Applied to all 3 metadata_resolver.py copies.

---

## Wikipedia Search: Disambiguation Page False Positives via Query-Dependent Snippets

### FAILED: Relying solely on "may refer to" snippet detection for disambiguation (Attempt 1)
- **What was tried:** `search_wikipedia()` detected disambiguation pages by checking if the Wikipedia API snippet contained "may refer to" (Layer B snippet check). For "Oh Lord", query "Oh Lord" returned snippet with "may refer to" → correctly penalized. But query "Oh Lord (Foxy Shazam song)" returned a different snippet excerpt highlighting the Foxy Shazam entry, omitting "may refer to" entirely.
- **Why it failed:** Wikipedia's search API returns **query-dependent snippet excerpts**. When a query matches a specific entry on a disambiguation page, the API highlights that entry's text rather than the page preamble ("may refer to"). The snippet then contains artist keywords and "song" keywords that boost the score instead of penalizing.
- **Lesson:** Disambiguation detection cannot rely on a single pattern. Wikipedia snippets are contextual — the same page produces different snippets for different queries. Secondary indicators (multiple "a song by" entries in snippet) must be used to catch disambiguation pages when the primary "may refer to" pattern is absent.
- **Fix applied:** Added secondary disambiguation detection: when `len(re.findall(r"a song by", snippet_lower)) >= 2`, apply -10 penalty (same as "may refer to"). This catches truncated disambiguation snippets where the API highlights one entry out of many.

---

## Wikipedia: Album → Single Fallback via Track Listing

### APPROACH: Extract single wiki URL from album page tracklist
- **Context:** When `search_wikipedia()` fails to find a single's Wikipedia page (due to title matching, disambiguation, or other gates), but the album Wikipedia page IS found, the album's track listing may contain a direct link to the single's article.
- **Implementation:** `extract_single_wiki_url_from_album(album_wiki_url, track_title)` scrapes the album page, finds `<table class="tracklist">` tables, and matches track titles (normalized, ignoring quotes). If the matching track has an `<a href="/wiki/...">` link, returns that URL.
- **Limitation:** Not all album tracklist entries are linked — only tracks with their own Wikipedia articles have links. When the track appears in the listing but has no link, returns None.
- **Integration:** Runs as a fallback AFTER `search_wikipedia()` returns None for the single, in both stages.py (initial import) and deferred.py (AI enrichment re-resolution).

---

## Track: Eminem — Rap God

### Problem
Scraper test returned MB poster art for *The Marshall Mathers LP 2* (album) instead of the *Rap God* single (release-group `26b50a2d-d1fd-42ca-8370-4b71cd5ff8a5`).

**Root cause (two bugs):**

1. **Strategy 2 (recording search) had no type filtering.** `_pick_best_release(releases)` was called without `allowed_types={"single", "ep"}`, so the first release returned (an Album release of MMLP2) was selected. The correct single RG existed but was never preferred.

2. **`_confirm_single_via_artist()` set the wrong release ID.** When the function confirmed that the artist had a matching Single release group, the code set `result["mb_release_id"] = best_rel.get("id")` — but `best_rel` was still the *album* release from the recording search, not a release from the confirmed single's RG. This means CoverArtArchive fetched album art, not single art.

3. **AI artist cross-reference never used.** The AI source resolution can provide `musicbrainz_artist_id`, but `unified_metadata.py` never consumed it. When the AI recording ID was missing (or rejected), the code fell directly to `search_musicbrainz()` instead of first trying `_confirm_single_via_artist(ai_artist_id, title)` to locate the single via the known artist.

### Fix (applied to all 3 pipeline variants)

**metadata_resolver.py — Strategy 2 type preference:**
- `_pick_best_release(releases)` now tries `allowed_types={"single", "ep"}` first; falls back to unfiltered only if no single/EP releases exist.
- When `_confirm_single_via_artist()` succeeds, the code browses the confirmed single's RG via `musicbrainzngs.browse_releases(release_group=...)`, picks the release with `cover-art-archive.front=true` (or first available), and uses *that* release ID + recording ID — not the album release.

**unified_metadata.py — Step A.5 (AI artist cross-reference):**
- Between the AI recording path (Step A) and the search fallback (Step B), added a new step: if AI provided `musicbrainz_artist_id` but Step A was skipped or failed, call `_confirm_single_via_artist(ai_artist_id, title)`.
- On match, browses the single's RG for releases/recordings, sets all MB fields, finds parent album, and marks `mb_used_ai_ids = True` so Step B is skipped.
- Source tag: `musicbrainz:ai_artist_xref`.

---

## Sequential Individual Download DB Lockouts

### FAILED: Per-module deferred task semaphores allowing 18 concurrent DB writers (Attempt 1)
- **What was tried:** Each pipeline module (pipeline_url, pipeline_lib, pipeline) had its own `_GLOBAL_DEFERRED_SLOTS = threading.Semaphore(6)`. This allowed up to 18 concurrent deferred-task threads when different pipeline types overlapped.
- **Why it failed:** When multiple individual URL downloads were added sequentially, their deferred tasks (AI enrichment, entity artwork, scene analysis, matching, orphan cleanup) all competed for SQLite write access. With 6 concurrent writers per pipeline type, even SQLite WAL mode + busy_timeout(30s) + exponential backoff retries couldn't prevent write-storm failures.
- **Fix applied (three changes):**
  1. **Shared semaphore:** Replaced 3 per-module `_GLOBAL_DEFERRED_SLOTS = Semaphore(6)` with a single `GLOBAL_DEFERRED_SLOTS = Semaphore(3)` in `worker.py`, imported by all 3 deferred.py files. Total concurrent DB-writing deferred tasks reduced from 18 → 3.
  2. **Increased retry count:** `_MAX_DB_RETRIES` increased from 5 → 7, providing more headroom under heavy contention (total worst-case retry time ~255s with jitter).
  3. **Jittered exponential backoff:** `_retry_delay(attempt)` adds random jitter (up to 50% of base delay) to de-synchronize retries. Without jitter, all blocked threads wake up at exactly the same time and immediately re-contend.

### ANTI-PATTERN: Per-module concurrency limits for a shared resource
SQLite allows only one concurrent writer. When multiple modules independently limit their own concurrency but all target the same SQLite database, the effective writer count is the SUM of all module limits, not the MAX. Concurrency limits for shared resources must be shared.

## Track: Amanda Palmer & The Grand Theft Orchestra — Do It With a Rockstar

### FAILED: Artist name splitting on "&" breaks MusicBrainz search for band names (Attempt 1)
- **What was tried:** `parse_multi_artist()` in `source_validation.py` calls `_split_artists()` which splits artist strings on `&`, `and`, and `,` separators. The code then uses only the "primary artist" (first part before the separator) for MusicBrainz searches.
- **Why it failed:** "Amanda Palmer & The Grand Theft Orchestra" was split into `["Amanda Palmer", "The Grand Theft Orchestra"]`, with only "Amanda Palmer" sent to MusicBrainz. The search for `artist:"Amanda Palmer" + title:"Do It With a Rockstar"` returned no results because the MB entry is registered under the full band name "Amanda Palmer & The Grand Theft Orchestra" — a single artist entity, not a collaboration.
- **Affected patterns:** Any band using "& The" format: Tom Petty & The Heartbreakers, Prince & The Revolution, Diana Ross & The Supremes, Bruce Springsteen & The E Street Band, Sly & The Family Stone, Katrina & The Waves, Iggy Pop & The Stooges, etc.
- **Existing protection:** The code already protected `"+ the"` patterns (Florence + the Machine) but NOT `"& The"` patterns.
- **Lesson:** "& The" in an artist name almost always indicates a single band/project, not a collaboration between separate artists. The `_split_artists()` function must protect this pattern the same way it protects "+ the".
- **Fix applied:**
  - **`source_validation.py` (all 3 copies: services, pipeline_url, pipeline_lib):** Added `if re.search(r'&\s+the\s+', artist_str, re.IGNORECASE): return [artist_str.strip()]` protection in `_split_artists()`, directly after the existing `+ the` check.
  - **`metadata_resolver.py` (all 3 copies):** Added fallback in `_search_single_release_group()`: when primary_artist differs from the original artist AND the search returns no results, retry the search with the full original artist string. This provides defense-in-depth for any other band name patterns that might be incorrectly split.

### Verification:
```python
parse_multi_artist("Amanda Palmer & The Grand Theft Orchestra")
# Before: ("Amanda Palmer", ["The Grand Theft Orchestra"])
# After:  ("Amanda Palmer & The Grand Theft Orchestra", [])

parse_multi_artist("DJ Snake & Lil Jon")
# Still correctly splits collaborations without "The":
# ("DJ Snake", ["Lil Jon"])
```

### ANTI-PATTERN: Splitting artist names on separators without protecting band name patterns
Collaboration separators (`&`, `and`, `feat.`) can appear as part of official band names. Before splitting, the code must check for known band name patterns like "X & The Y" and "X + the Y" where the separator is part of the name, not a collaboration indicator. Blindly splitting on separators truncates the artist name and breaks downstream searches that require the full band name.

### FAILED: `search_wikipedia_artist()` searching with full "X & The Y" name without lead-artist fallback (Attempt 2 — Session 4)
- **What was tried:** `search_wikipedia_artist()` used `parse_multi_artist()` to extract a primary artist, then searched Wikipedia with that name. For "Amanda Palmer & The Grand Theft Orchestra", `parse_multi_artist` (correctly) returns the full name as a single entity due to the `& The` protection. Wikipedia has no page for the full band name — the page is under "Amanda Palmer".
- **Why it failed:** `parse_multi_artist` is designed for collaboration splitting, not lead-artist extraction. It deliberately keeps "X & The Y" as one entity. But Wikipedia needs the lead artist's name for search, since the Wikipedia page is typically under the lead artist, not the full "with backing band" name.
- **Lesson:** Wikipedia artist search must extract the lead artist from "X & The Y" patterns as a search variant, independent of `parse_multi_artist`'s collaboration splitting logic. Different consumers of artist names have different needs.
- **Fix applied:** Added `re.match(r'^(.+?)\s*(?:&|and)\s+the\s+', artist)` in `search_wikipedia_artist()` to extract lead artist. Added as variant and used as search name.

### FAILED: Album entity `mb_release_group_id` never populated from `mb_release_id` (Attempt 2 — Session 4)
- **What was tried:** `resolve_album()` in `metadata/resolver.py` returns `mb_release_id` but NOT `mb_release_group_id`. `get_or_create_album()` only stores `mb_release_id` on the `AlbumEntity`. The `_re_resolve_sources()` deferred function tries to create a `musicbrainz/album` source from `item.album_entity.mb_release_group_id` — which is None.
- **Why it failed:** The expected MB album link (`e2c16c13-46a1-4f86-97c4-79450edc4d87`) is a release GROUP, but the entity resolver only stores the release ID. No code path derives the release-group from the release.
- **Lesson:** When creating musicbrainz/album sources, the code must look up the release-group from the release ID via MusicBrainz API when `mb_release_group_id` is missing.
- **Fix applied:** In `_re_resolve_sources()`, when `album_entity.mb_release_group_id` is None but `album_entity.mb_release_id` exists, call `musicbrainzngs.get_release_by_id(includes=["release-groups"])` to derive and populate the release-group ID.

### FAILED: Poster CoverArtArchive fetch with no release-group fallback (Attempt 2 — Session 4)
- **What was tried:** Section 3 poster upgrade calls `_fetch_front_cover(item.mb_release_id)` for the single's release ID (`abf8c3df`). When CAA has no cover for this specific release, the poster upgrade silently falls through with no cover art.
- **Why it failed:** The release-group `0416ad28` aggregates covers across all releases in the group. `_fetch_front_cover_by_release_group` DOES return a valid cover URL. But the poster code never tries the release-group fallback.
- **Lesson:** CoverArtArchive covers may not exist for every release, but the release-group endpoint aggregates across releases and is more reliable. Always fall back to release-group when release-level fetch returns None.
- **Fix applied:** After `_fetch_front_cover(release_id)` returns None, try `_fetch_front_cover_by_release_group(item.mb_release_group_id)`.

### FAILED: `_re_organize_file()` renaming files but not updating `media_assets` DB paths (Attempt 2 — Session 4)
- **What was tried:** `_re_organize_file()` renames folder, video file, and auxiliary files (poster, thumb, NFO) on disk, then updates `item.folder_path` and `item.file_path` in the DB. But `media_assets` records referencing the old folder path are NOT updated.
- **Why it failed:** The poster `MediaAsset` (id=21) had `file_path` pointing to the old pre-rename folder. After rename, the old path no longer exists. The frontend/API trying to serve this asset would get a 404.
- **Lesson:** When renaming files that are referenced by DB records, ALL referencing records must be updated — not just the primary entity.
- **Fix applied:** After updating `item.folder_path` and `item.file_path`, query all `MediaAsset` records for the video and update any `file_path` values that reference the old folder.

### ANTI-PATTERN: Using `parse_multi_artist` for Wikipedia lead-artist extraction
`parse_multi_artist` is designed for collaboration splitting — it correctly keeps "X & The Y" as one entity. But Wikipedia pages are typically under just the lead artist's name. Functions that need lead-artist names must use separate extraction logic, not rely on `parse_multi_artist` to split band names it intentionally keeps intact.

### ANTI-PATTERN: Renaming files without updating all DB references
When renaming library folders/files, `video_items.folder_path` and `video_items.file_path` are the obvious records to update. But `media_assets.file_path` also references files in the same folder. Any rename operation must audit ALL tables with file path columns and update them accordingly.

---

## Track: B. Lucas (PianoCovers CB) — The Book of Love (Piano Cover)

### FAILED: AI enrichment assigning original artist's album to covers (Attempt 1)
- **What was tried:** The AI enrichment prompt asked for "The original album this song appeared on". For a piano cover by B. Lucas of The Magnetic Fields' "The Book of Love", the AI returned album="69 Love Songs" — the original artist's album, not anything the cover artist released.
- **Why it failed:** `_auto_apply_fields` had guards for compilations and MB album overwrites, but NO guard for `version_type == "cover"`. The AI's album suggestion was blindly accepted. The cover artist (a YouTube pianist) has no album; the original's album is always a false positive.
- **Lesson:** For covers, AI-suggested albums must ALWAYS be rejected by code — the AI will consistently return the original artist's album, which is a false positive for the cover performer. Prompt instructions alone are insufficient; code-level guards are mandatory.
- **Fix applied:** Added `_is_cover = video.version_type == "cover"` guard in `_auto_apply_fields` that rejects AI-suggested albums for all covers. Also updated the AI enrichment prompt to explicitly instruct against assigning original artist albums to covers.

### FAILED: AI enrichment adding version-type suffixes to titles (Attempt 1)
- **What was tried:** AI enrichment was told "no suffixes like '(Official Video)' or '[HD]'" but the prompt didn't mention cover/version suffixes. For a cover, the AI produced title="The Book of Love (Piano Cover — in the style of Peter Gabriel)" instead of just "The Book of Love".
- **Why it failed:** The `version_type` field already captures that this is a cover. Adding "(Piano Cover — in the style of Peter Gabriel)" to the title is redundant and introduces incorrect provenance claims (the song is by The Magnetic Fields, not Peter Gabriel). The AI will always try to add context to the title unless explicitly told not to.
- **Lesson:** AI prompts must explicitly list version-type suffixes (Cover, Piano Cover, Acoustic, Live, in the style of X) as forbidden title additions. Additionally, code-level sanitisation must strip these patterns as a safety net when `version_type` is already set.
- **Fix applied:** Added regex sanitisation in `_auto_apply_fields` that strips trailing parenthetical content containing "cover", "in the style of", "live at/from", or "remix" when `version_type` is already classified. Also updated the prompt.

### FAILED: Stale MusicBrainz IDs surviving AI artist identity change (Attempt 1)
- **What was tried:** AI source resolution initially resolved "Book of Love" → The Magnetic Fields (MB artist ID `874b3a85`). AI enrichment later corrected artist to "B. Lucas". Entity re-resolution in `_auto_apply_fields` then called `resolve_artist("B. Lucas", mb_artist_id="874b3a85")` — passing The Magnetic Fields' MB ID for a completely different artist.
- **Why it failed:** `video.mb_artist_id` was set during the initial pipeline under the wrong artist identity. When AI enrichment changed the artist, the stale MB IDs were never cleared. Entity resolution trusts MB IDs as authoritative, so it resolved entities for The Magnetic Fields instead of B. Lucas.
- **Lesson:** When AI enrichment changes the artist identity, ALL MusicBrainz IDs on the video item (`mb_artist_id`, `mb_recording_id`, `mb_release_id`, `mb_release_group_id`) must be cleared BEFORE entity re-resolution runs. These IDs were resolved under the old identity and are now stale/misleading.
- **Fix applied:** Before entity re-resolution in `_auto_apply_fields`, when `"artist" in applied`, clear all MB ID fields and log the cleared values.

### ANTI-PATTERN: Trusting AI to handle cover metadata without code guards
The AI enrichment will ALWAYS try to "help" by filling in album, adding descriptive suffixes, and resolving against the well-known original rather than the obscure cover artist. For covers: (1) reject AI albums by code, (2) sanitise titles by code, (3) clear stale IDs by code. Prompt instructions are advisory; code guards are mandatory.

---

## Track: Blackshape — ITIIITIATIIHYLIHYL

### FAILED: Wikipedia artist search accepting partial title matches (Attempt 1)
- **What was tried:** Wikipedia artist search scored candidates using `if artist_name in page_title` (+4 points, minimum score of 4 to accept). For artist "Blackshape", the Wikipedia search returned "Blackshape Prime" (an Italian aircraft manufacturer). Since "blackshape" appears in "blackshape prime", it scored +4 and passed the minimum threshold. SequenceMatcher validation (0.5 threshold) also passed since "blackshape" vs "blackshape prime" gives ~0.77.
- **Why it failed:** Substring matching is far too loose for Wikipedia disambiguation. "Blackshape Prime" contains "Blackshape" but is a completely different entity (aircraft, not music). The artist image fetched from this page showed an airplane, not the band. There was also no content validation — the code never checked if the Wikipedia page was actually about a musician/band.
- **Lesson:** Wikipedia artist matching must verify that the page title (stripped of disambiguation suffixes) *closely* matches the artist name, not just contains it. Additionally, after fetching the page, the infobox content should be validated for music-related fields ("genres", "labels", "years active", etc.) to catch non-music entities that slip through textual matching.
- **Fix applied:** (1) Added title-completeness penalty in both `search_wikipedia_artist()` (metadata_resolver.py) and `_scored_wiki_search()` (wikipedia.py): when the stripped title's SequenceMatcher similarity to the artist name is below 0.85, apply a -3 score penalty. (2) Added music-content infobox validation in `WikipediaProvider.search_artist()` and `get_artist_assets()`: if a page has an infobox but no music-related fields (genres, label, years active, etc.), discard it. (3) Raised the SequenceMatcher threshold in `search_artist()` and `get_artist_assets()` from 0.5 to 0.85.

### ANTI-PATTERN: Substring matching for Wikipedia page disambiguation
`if name in page_title` will match any page whose title *contains* the search name. "Blackshape" matches "Blackshape Prime", "Blackshape S.p.A.", etc. Wikipedia disambiguation requires comparing the *full* stripped page title against the search name with a tight similarity threshold, not just checking containment.

---

## SYSTEMIC: Fixes only applied to one pipeline copy

### FAILED: Applying Wikipedia/MB validation fixes only to `app/services/` and `app/metadata/providers/`
- **What was tried:** Session 1 applied all Wikipedia false-positive fixes (title-completeness penalty, music infobox validation, similarity threshold 0.85) to `app/services/metadata_resolver.py` and `app/metadata/providers/wikipedia.py`. These are the BASE pipeline copies.
- **Why it failed:** The project has THREE parallel pipeline copies: `app/pipeline/` (base), `app/pipeline_lib/` (library import), and `app/pipeline_url/` (URL import). Library imports go through `pipeline_lib/`, which has its own copies of `metadata_resolver.py`, `wikipedia.py`, `unified_metadata.py`, `stages.py`, and `deferred.py`. The fixes in `app/services/` were never exercised during library import — `pipeline_lib/` has completely separate code that was still running the old, vulnerable logic.
- **Lesson:** **ALL THREE pipeline copies must be patched for any scraper/validation fix.** Always check `app/services/`, `app/pipeline_lib/services/`, and `app/pipeline_url/services/` — plus the corresponding `metadata/providers/` directories. The code duplication is a known architectural debt.
- **Fix applied (Session 2):** All Wikipedia fixes and MB artist validation applied across all 3 pipeline copies (6+ files): `metadata_resolver.py` (×3), `wikipedia.py` (×3), `stages.py` (×3), `unified_metadata.py` (×3).

### ANTI-PATTERN: Code duplication across pipeline copies
Any fix applied to one pipeline copy MUST be checked and replicated across all three: `app/services/`, `app/pipeline_lib/services/`, `app/pipeline_url/services/`. The providers under `app/metadata/providers/` vs `app/pipeline_lib/metadata/providers/` vs `app/pipeline_url/metadata/providers/` also differ.

---

## Track: B. Lucas — The Book of Love (MB artist false positive)

### FAILED: AI-provided MB recording accepted without artist validation
- **What was tried:** When AI source resolution provides a `musicbrainz_recording_id`, the code in `unified_metadata.py._scrape_with_ai_links()` looks up the recording directly from MusicBrainz. It validates the recording *title* matches (SequenceMatcher ≥ 0.5) and the release type is single/EP. It then unconditionally accepts the `artist-credit` from the recording, setting `metadata["mb_artist_id"]` without checking if the artist matches.
- **Why it failed:** The AI provided a recording ID for "The Book of Love" by "Lucas Peire" (MB ID `4aefd6e5`), a completely different artist. The title matched ("The Book of Love" ≈ "The Book of Love" → sim=1.0), but the artist "Lucas Peire" does not match "B. Lucas" — they just share the substring "Lucas". Without artist validation, the wrong `mb_artist_id` propagated through to `stages.py` (which created a `musicbrainz_artist` source) and `deferred.py` (which re-created it from `item.mb_artist_id`).
- **Lesson:** AI-provided MusicBrainz recording IDs must be validated on BOTH title AND artist similarity before acceptance. Title match alone is insufficient because many songs share the same title across different artists.
- **Fix applied:** Added artist validation in `_scrape_with_ai_links()` across all 3 `unified_metadata.py` copies: after extracting `artist-credit`, compare the recording's artist to the expected artist using SequenceMatcher (threshold 0.6) + `_tokens_overlap` (threshold 0.4). Similarity of "b lucas" vs "lucas peire" = 0.556 < 0.6 with token overlap 0.33 < 0.4 → recording rejected, falls back to validated search.

### FAILED: AI-provided Wikipedia URL bypass via `metadata["source_url"]` in stages.py
- **What was tried:** `stages.py._step_collect_source_links()` created `links["wikipedia_artist"]` from `metadata["source_url"]` (the AI-provided Wikipedia URL) if it contained "wikipedia.org". Later, `search_wikipedia_artist()` validated candidates and returned None for false positives. But the code only added the validated result — it never removed the pre-existing AI-provided entry.
- **Why it failed:** For Blackshape, the AI returned `wikipedia_url: "Blackshape_Prime"`. `unified_metadata.py` accepted it (the `detect_article_mismatch()` check was insufficient), set `metadata["source_url"]`. `stages.py` created `links["wikipedia_artist"]` from it. The validated `search_wikipedia_artist("BLACKSHAPE")` returned None, but didn't remove the already-created AI entry.
- **Fix applied:** Added guard in all 3 `stages.py` files: when `search_wikipedia_artist()` returns None AND `"wikipedia_artist" in links`, delete the unvalidated AI entry with a log message.

---

## Track: Fedde Le Grand — Put Your Hands Up for Detroit (missing description)

### FAILED: Literal substring matching in `search_wikipedia()` — word/number title variants (Attempt 1)
- **What was tried:** `search_wikipedia(title, artist)` scores Wikipedia search results using `title_lower in pt_lower` (title is substring of page title) for +3 points and `_pt_base == title_lower` (exact base match) for +2 points. Both comparisons are literal string comparisons.
- **Why it failed:** The track title is "Put Your Hands Up **for** Detroit" but the Wikipedia page is titled "Put Your Hands Up **4** Detroit". The word "for" ≠ the digit "4", so `"put your hands up for detroit" in "put your hands up 4 detroit"` → False. The page loses +3 (title contains) and +2 (exact base) = 5 points, falling below MIN_SCORE=6 → discarded. No Wikipedia single source → no description scraped → empty `plot` field.
- **Lesson:** Title matching must account for common word↔number substitutions in song titles (for→4, to→2, you→u, are→r, one→1, etc.). Literal string matching silently fails for a whole class of titles that use numerical shorthand.
- **Fix applied:** Added `_normalize_title_for_match(s)` helper in all 3 `metadata_resolver.py` copies. The function normalizes common word↔number substitutions using `\b` word boundaries (for→4, to→2, too→2, you→u, are→r, and→&, one→1, two→2, four→4, eight→8, etc.). All scoring comparisons now check BOTH the literal match AND the normalized match as a fallback: `(title_lower in pt_lower or _title_norm in _pt_norm)`. Search queries also add a normalized variant to increase Wikipedia API recall.

### ANTI-PATTERN: Literal string matching for fuzzy title data
Song titles commonly use number/word substitutions ("4" for "for", "2" for "to", "U" for "you"). Any title-matching logic that relies solely on literal equality or substring matching will miss these variants. Always normalize titles before comparison when matching against external sources like Wikipedia.

### FAILED: Normalized title matching without updating ALL penalty blocks (Attempt 2)
- **What was tried:** After adding `_normalize_title_for_match()` in Attempt 1, the title-contains (+3) and exact-base (+2) checks correctly use the normalized form. However, the "neither artist nor title" penalty block at the end of `search_wikipedia()` in `pipeline_url/services/metadata_resolver.py` still uses the literal `title_lower not in pt_lower` without also checking the normalized variant.
- **Why it failed:** For "Put Your Hands Up for Detroit" vs page "Put Your Hands Up 4 Detroit": the normalized checks award +3 +2 = 5. Artist in snippet +2, song keywords +2 = total 9. BUT the final penalty `title_lower not in pt_lower` is True (literal "for" ≠ "4") AND `not any(av in pt_lower for av in _artist_variants)` is True (artist not in page title) → -4 penalty → score drops to 5 < MIN_SCORE=6 → page rejected despite being the correct result.
- **Lesson:** When adding normalized matching to scoring functions, EVERY penalty and comparison block that uses the original (literal) title/artist must also check the normalized variant as a fallback. Partial normalization is worse than none — it gives false confidence.
- **Fix applied:** Added `and _title_norm not in _pt_norm` to the "neither artist nor title" penalty condition in `pipeline_url/services/metadata_resolver.py`. The penalty now only fires when both the literal AND normalized forms fail to match.

---

## Track: BLACKSHAPE — ITIIITIATIIHYLIHYL (false positive Wikipedia album)

### FAILED: Wikipedia album search accepts non-music pages via substring match
- **What was tried:** `search_wikipedia_album(artist, album)` scores pages using `album_lower in pt_lower` (+3) and music-keyword snippet checks (+2). The `_non_music_tags` list only includes TV/film/game tags, and the similarity gate threshold is 0.5.
- **Why it failed:** Album "BLACKSHAPE" by artist "BLACKSHAPE" searches Wikipedia and finds "Blackshape Prime" (an Italian ultralight aircraft). "blackshape" is a substring of "blackshape prime" → +3. "Blackshape" appears in the snippet (as the manufacturer) → +2 for artist match. Total score = 5 ≥ 4 threshold → passes. Similarity: "blackshape" vs "blackshape prime" = 0.77 > 0.5 → passes. The wikipedia/album source is created pointing to an aircraft page.
- **Lesson:** Album Wikipedia search must (1) de-score snippets containing non-music indicators like "aircraft", "vehicle", "automobile", etc., (2) include expanded page-title non-music tags like "(aircraft)", "(vehicle)", "(company)", and (3) use a higher similarity threshold (≥0.7) to reject pages with extra unrelated words in the title.
- **Fix applied (Attempt 1 – INCOMPLETE):** Added `_non_music_album_snippet` keywords and extended `_non_music_tags` and raised similarity gate to 0.7 — but only applied to `pipeline_lib` copy. The `app/services` and `app/pipeline_url` copies were left with the original 6-tag `_non_music_tags` and no snippet scoring. BLACKSHAPE was imported via `pipeline_url`, so the fix never ran.
- **Fix applied (Attempt 2):** Applied the same `_non_music_album_snippet` keywords, extended `_non_music_tags`, to all 3 pipeline copies (`app/services`, `app/pipeline_url`, `app/pipeline_lib`).

---

## UI: Jobs stuck in "Finalizing" state after server restart

### FAILED: No recovery for completed jobs with incomplete deferred tasks
- **What was tried:** When a URL import completes, `db_apply.py` sets `status='complete'` with `current_step='Finalizing'` and `progress=90-99%` when deferred tasks exist (preview generation, matching, kodi export, entity artwork). A daemon thread runs these deferred tasks and should update `current_step='Import complete'` and `progress=100%` in the finally block.
- **Why it failed:** If the server restarts before the daemon thread finishes, the `current_step` remains "Finalizing" permanently. The frontend's `isFinalizing` check (`status === "complete" && currentStep !== "Import complete"`) shows an amber pulsing badge indefinitely. The existing `_cleanup_stale_jobs()` only fixes ACTIVE-status jobs (queued, downloading, etc.) — it doesn't touch completed jobs with stale `current_step`.
- **Lesson:** Any background processing that updates job metadata must have a recovery mechanism on startup. Jobs in terminal states (complete, failed, cancelled) should be checked for inconsistent sub-state fields.
- **Fix applied:** Added cleanup block in `_cleanup_stale_jobs()` to find completed jobs where `current_step != 'Import complete'` and fix them to `current_step='Import complete'` with `progress=100%`.

---

## Track: Gang of Youths — Blood

### FAILED: No title similarity gate on `search_wikipedia()` song search
- **What was tried:** `search_wikipedia(title, artist)` scores Wikipedia candidates using substring matching (`title_lower in pt_lower` → +3), disambiguation tags like "(song)" → +3, and snippet keyword matches. No similarity gate between the searched title and the page's base title.
- **Why it failed:** "Blood" (5 chars) is a substring of "Back in Blood (song)" → +3. The "(song)" disambiguation fires → +3. Snippet mentions "song" → +2. Total = 8 ≥ MIN_SCORE=6 → accepted. But "Back in Blood" is a completely different song by Pooh Shiesty. The empty disambiguation text "(song)" triggers no wrong-artist penalty. Short, common titles like "Blood", "Fire", "Home" are especially vulnerable to substring-matching against longer, unrelated song titles.
- **Lesson:** Song Wikipedia search needs a title similarity gate (like `search_wikipedia_album` already has) to reject pages where the base title diverges significantly from the searched title. Substring matching alone is insufficient for short titles.
- **Fix applied:** Added title similarity gate after MIN_SCORE check: strips disambiguation, compares via SequenceMatcher (also checks normalized variant), rejects if best similarity < 0.6. "blood" vs "back in blood" = 0.56 → rejected. Applied to all 3 pipeline copies.

### FAILED: Album artwork fetched using video's single release ID instead of album's own release ID
- **What was tried:** `process_artist_album_artwork()` in artwork_manager.py fetches album cover art from Cover Art Archive. When the release-group lookup fails, it falls back to `_album_caa_id = mb_album_release_id or mb_release_id` — where `mb_release_id` is the video item's release (typically a single), not the album's release.
- **Why it failed:** For Salvatore Ganacci – Horse, the album entity (Boycycle EP) has its own `mb_release_id` and `mb_release_group_id`. However, in deferred.py, the album entity's IDs were only extracted AFTER `process_artist_album_artwork` was called. So `mb_album_release_id` was None at call time, and the fallback used `mb_release_id` (the Horse single's release `817b703f`), fetching the single's cover art instead of the Boycycle EP's cover art.
- **Lesson:** Album entity IDs must be extracted before the artwork pipeline call, not after. Additionally, the video item's `mb_release_id` (which is a single/recording release) should never be used as a fallback for album cover art — these are semantically different releases.
- **Fix applied:** (1) In all 3 deferred.py files, moved the album entity MB ID fallback extraction to before the `process_artist_album_artwork` call. (2) In all 3 artwork_manager.py files, removed `or mb_release_id` from the album CAA fallback and `ensure_album_artwork` call.

---

## Track: Ronan Keating — When You Say Nothing At All (Session 29)

### FAILED: `detect_article_mismatch` rejecting valid Wikipedia articles for cover songs (Attempt 1)
- **What was tried:** `detect_article_mismatch()` checked the infobox artist against the expected artist. When they didn't match (normalized, substring, or token overlap), it immediately flagged "artist mismatch" and rejected the article.
- **Why it failed:** "When You Say Nothing at All" is a cover song — the Wikipedia article's infobox lists the original artist (Keith Whitley) since that's the primary release. But the article has an entire "Ronan Keating version" section with extensive chart data, credits, and critical reception. The scraper rejected the correct article because `scraped_artist='Keith Whitley'` vs `expected_artist='Ronan Keating'` failed all matching checks. The fallback search also failed, resulting in no Wikipedia data at all (`WIKI_SCRAPE_FAILED`).
- **Lesson:** Wikipedia song articles about covers list the original artist in the infobox but discuss all notable cover versions in the article body. When the infobox artist doesn't match, the article body must be checked for the expected artist before rejecting. This pattern applies to all cover songs where the Wikipedia article is about the song itself, not a specific version.
- **Fix applied:** Added a cover song body-text check to `detect_article_mismatch()` in both `app/scraper/metadata_resolver.py` and `app/services/metadata_resolver.py`. When the infobox artist doesn't match the expected artist, the function now checks if the expected artist (with name variants) appears anywhere in the article body text (`scraped["plot"]`). If found, the article is accepted as a cover song article instead of being rejected.

### ANTI-PATTERN: Rejecting Wikipedia articles based solely on infobox metadata without consulting article body
Wikipedia song articles often cover multiple versions by different artists. The infobox metadata (artist, release date, label) reflects the original version, but the article body discusses all notable cover versions. Rejecting an article based on infobox artist mismatch alone discards valid articles for any cover song. Always check if the expected artist appears in the article body before flagging an artist mismatch.

---

## Track: Noisestorm — Crab Rave

### FAILED: No-album singles never get CAA poster — gate requires art_result that is empty when album is missing
- **What was tried:** In the poster upgrade section of deferred.py, the `elif not _has_parent_album` branch was gated by `art_result.get("album_image_url")`. The intent was to use the artwork pipeline's album image result as the video poster when there's no parent album entity.
- **Why it failed:** For Noisestorm – Crab Rave, the `album` field is empty and there is no `album_entity`. So `_has_parent_album = False`. However, `process_artist_album_artwork()` doesn't produce `album_image_url` when there's no album name to look up — the key simply isn't in `art_result`. The `elif` condition `art_result.get("album_image_url")` evaluates to None, so the entire branch is skipped. `_video_poster_url` stays None and the YouTube thumbnail (`sddefault.jpg`, provenance `youtube_thumb`) is kept as the poster. The item has valid MB IDs (`mb_release_id`, `mb_release_group_id`) that could fetch the single's cover from Cover Art Archive, but this was never attempted.
- **Lesson:** Items with no parent album but valid MusicBrainz IDs should still attempt CAA cover lookup directly for their own release/release-group. The artwork pipeline's art_result is not a reliable source for these items — the poster upgrade logic needs a direct CAA fallback.
- **Fix applied:** Expanded the `elif not _has_parent_album` branch in all 3 deferred.py files to: (1) use `art_result["album_image_url"]` if present, (2) otherwise call `_fetch_front_cover(item.mb_release_id)`, (3) fallback to `_fetch_front_cover_by_release_group(item.mb_release_group_id)`. This ensures no-album singles with MB IDs get proper CAA cover art as their poster.

---

## Track: Adam Cohen — We Go Home

### FAILED: Wikipedia search accepting album pages as song pages (Attempt 1)
- **What was tried:** `search_wikipedia()` Phase 2 scraped candidate pages and called `classify_wikipedia_page()`, which correctly identified `We_Go_Home` as `page_type="album"`. However, Phase 2 only verified the artist matched — it never checked `page_type`. Also, `detect_article_mismatch()` had no album/artist page rejection.
- **Why it failed:** The Wikipedia album page `https://en.wikipedia.org/wiki/We_Go_Home` was accepted as the song's Wikipedia source URL because the artist "Adam Cohen" matched. The page's album page type was ignored, causing the infobox image (album cover) to be used as the song's poster — a false positive that happened to look correct but was semantically wrong.
- **Fix applied:** Added `page_type` checks in `search_wikipedia()` Phase 2 and `detect_article_mismatch()` to reject candidates with `page_type in ("album", "artist")`. Applied in both `app/scraper/metadata_resolver.py` and `app/services/metadata_resolver.py`.

### FAILED: Requiring both single RG and album RG to accept title-track albums (Attempt 1 — regression from above fix)
- **What was tried:** The `_has_distinct_rg` check in `unified_metadata.py` required BOTH `mb_release_group_id` (single's release group) AND `mb_album_release_group_id` to be present and different. This was used to decide whether to accept an album name that matches the song title (title-track case).
- **Why it failed:** For tracks that only exist as album tracks (no standalone single release), `mb_release_group_id` is None. The condition `mb.get("mb_release_group_id")` evaluated to False, making `_has_distinct_rg` False even though the album was a genuine parent album. This caused the album name to be discarded as a "title duplicate", which cascaded into: (1) no Wikipedia album URL (cross-fallback requires album name), (2) no album artwork (dedicated fetch gated on `resolved_album_name`), (3) no Wikipedia poster art at all.

---

## Track: Alanis Morissette — Hand in My Pocket

### FAILED: Preferring specific MB Release over Release Group for single poster art (Attempt 1)
- **What was tried:** `fetch_caa_artwork()` in `artwork_selection.py` tried the specific release endpoint (`/release/{mb_release_id}`) first for singles, then fell back to the release-group endpoint (`/release-group/{mb_release_group_id}`) only if the release had no art. The comment justified this: "the release-group endpoint can redirect to any release in the group (e.g. a 'music video' release with a video screenshot instead of the actual single cover)."
- **Why it failed:** For "Hand in My Pocket", the specific release `02aeb9cb` had cover art — but it was from an obscure pressing with different artwork than the canonical single cover. Since step 1 succeeded, the release-group endpoint (which returns the curated/canonical cover) was never reached. The defensive logic against music-video screenshots caused the opposite problem: picking an obscure pressing's art over the well-known single cover. The music-video screenshot concern is real but rare; the release-group endpoint's curation heavily favors actual cover art.
- **Fix applied:** Flipped the preference: try release-group first (curated canonical cover), fall back to specific release only when the release-group has no art. Applied in `app/scraper/artwork_selection.py`.

### FAILED: Using specific release CAA endpoint for album artwork in `search_album_musicbrainz()` (Attempt 1)
- **What was tried:** `search_album_musicbrainz()` searched MB for releases matching an album name, picked the best one (sorted by release-group type), then fetched cover art from `/release/{release_id}`. The `_get_cover_art_url()` function always used the release-level endpoint.
- **Why it failed:** The MB search returned release `06bb9094` for "Jagged Little Pill" — a different pressing than the main pipeline's `630617b0`. Different pressings of the same album can have different cover art on CAA (remastered editions, regional variants, etc.). The art was edition-dependent and non-deterministic across searches. The release-group endpoint (`/release-group/{rg_id}`) returns the curated "best" cover across all releases in the group, avoiding pressing-specific variations.
- **Fix applied:** Extract `release-group.id` from the MB search result, fetch art from `/release-group/{rg_id}` first, fall back to `/release/{release_id}` only when the release-group has no art. Added `_get_cover_art_url_by_release_group()`. Applied in both `app/scraper/artist_album_scraper.py` and `app/services/artist_album_scraper.py`.

### ANTI-PATTERN: Preferring a specific entity over an aggregate when the specific entity is non-deterministic
When the specific MB Release selected depends on search relevance ordering (which varies by query phrasing, date, and region), its cover art is non-deterministic. The Release Group aggregates across all releases and is curated for the canonical cover — it's a stable, deterministic choice. Prefer the aggregate (release-group) endpoint unless there's a specific reason to target a known release.
- **Lesson:** When a track has `mb_album_release_group_id` but no `mb_release_group_id`, the track only exists on this album — the album IS the genuine parent even when names match. The absence of a single release group is itself evidence that this is an album track, not a coincidental name match.
- **Fix applied:** Changed `_has_distinct_rg` to accept when `mb_album_release_group_id` exists and EITHER `mb_release_group_id` is None (album-only track) OR the two IDs differ. Applied in both `app/scraper/unified_metadata.py` and `app/services/unified_metadata.py`.

### ANTI-PATTERN: Requiring both sides of a comparison to be non-null before accepting a valid entity
When checking whether entity A is distinct from entity B, the absence of entity B does not mean A is invalid — it may mean B simply doesn't exist. For MusicBrainz release groups, a track with no single release group but a valid album release group is an album-only track, and the album should be accepted.

---

## Track: Kirin J. Callinan — Big Enough (feat. Alex Cameron, Molly Lewis, Jimmy Barnes)

### FAILED: Featuring suffixes in title poison Wikipedia search queries and similarity gate
- **What was tried:** `search_wikipedia("Big Enough (feat. Alex Cameron, Molly Lewis, Jimmy Barnes)", "Kirin J. Callinan")` searches Wikipedia and then compares the input title against the best candidate page title using `SequenceMatcher` with a 0.6 similarity threshold.
- **Why it failed:** The Wikipedia page is titled "Big Enough" (10 chars). The input `title_lower` is `"big enough (feat. alex cameron, molly lewis, jimmy barnes)"` (58 chars). SequenceMatcher ratio ≈ 0.29, far below the 0.6 gate. The `(feat. ...)` suffix is not part of the actual song title — it's a featuring credit that YouTube and MusicBrainz append — but `search_wikipedia()` never stripped it. This also pollutes search queries (e.g. `"Big Enough (feat. Alex Cameron, Molly Lewis, Jimmy Barnes) (song)"`) making it harder to find the right page in the first place.
- **Lesson:** Featuring suffixes `(feat. ...)`, `(ft. ...)`, `(featuring ...)` must be stripped from the title at the top of `search_wikipedia()` before building search terms and before the similarity gate comparison. These are metadata credits, not part of the canonical song title.
- **Fix applied:** Added `re.sub(r'\s*\((?:feat\.?|ft\.?|featuring)\s+.*?\)\s*$', '', title, flags=re.IGNORECASE)` at the top of `search_wikipedia()` in all 3 metadata_resolver.py files, before `_title_norm` computation and search term construction.

### FAILED: Period in artist name breaks substring matching in `search_wikipedia_artist()`
- **What was tried:** `search_wikipedia_artist("Kirin J Callinan")` searches Wikipedia and scores candidates by checking if the artist name appears as a substring of the page title: `any(av in pt_lower for av in _artist_variants)`. A match gives +4, a miss gives -10.
- **Why it failed:** The stored artist name is `"Kirin J Callinan"` (no period) but the Wikipedia page title is `"Kirin J. Callinan"` (with period after J). The Python `in` operator for substring matching fails: `"kirin j callinan" in "kirin j. callinan"` → `False`, because `"j callinan"` ≠ `"j. callinan"`. The -10 penalty pushes the score far below the minimum threshold of 4, so the correct page is rejected. No Wikipedia artist page means no infobox image extraction, so `artist_image` stays None.
- **Lesson:** Periods are semantically insignificant in artist names (initials, abbreviations) but break substring matching. Both sides must be period-stripped before comparison, similar to how hyphens are already handled with variants.
- **Fix applied:** In `search_wikipedia_artist()` in all 3 metadata_resolver.py files, added period-stripping to the artist-in-title check: `_pt_no_dots = pt_lower.replace(".", "")` and `any(av in pt_lower or av.replace(".", "") in _pt_no_dots for av in _artist_variants)`. This handles both directions (name has period but page doesn't, or vice versa).

---

## Track: Groove Armada feat. Gram'ma Funk — I See You Baby

### FAILED: Incomplete MusicBrainz resolution never corrected — deferred re-resolve gate too narrow
- **What was tried:** The initial `search_musicbrainz()` call during import uses two strategies: Strategy 1 (`_search_single_release_group`) looks for singles/EPs, and Strategy 2 (recording fallback) searches recording endpoints. When Strategy 1 fails, Strategy 2 may return partial results: `mb_artist_id`, `mb_recording_id`, and `mb_release_id` are set, but `mb_release_group_id` and `album` are missing. Later, the deferred pipeline has an MB re-resolve section (line ~782) that re-runs `search_musicbrainz()` — but it's gated by `if not item.mb_recording_id`, which is False when the recording ID was already set.
- **Why it failed:** For Groove Armada – I See You Baby, the recording fallback partially succeeded: `mb_recording_id=b6972bd4-...`, `mb_release_id=58ac1539-...`, `mb_artist_id=35723b60-...` were all set, but `mb_release_group_id=None` and `album=""`. Since `mb_recording_id` was already set, the deferred re-resolve section was skipped entirely. Without `mb_release_group_id` and `album`, no album entity was created → no album art was resolved → no poster upgrade from CAA. Additionally, `mb_artist_id` on the video_item was never propagated to the artist entity record, leaving `artists.mb_artist_id = None` even though the video_item had the correct value.
- **Lesson:** The deferred MB re-resolve gate must cover partial resolution failures, not just total failures. Items with `mb_recording_id` set but `mb_release_group_id` missing represent an intermediate failure state that needs correction. Also, `mb_artist_id` should be propagated from video_item to artist entity when the entity lacks it.
- **Fix applied:** Added a "1c. Complete incomplete MB resolution" block in all 3 deferred.py files, positioned BEFORE the artwork pipeline. This block: (1) detects partial resolution (`item.mb_recording_id` is set but `item.mb_release_group_id` is None), (2) re-runs `search_musicbrainz()` with full artist validation, (3) updates `mb_release_group_id` and `album` on the video_item, (4) creates an album entity via `get_or_create_album()` if album data was resolved, (5) propagates `mb_artist_id` from video_item to artist entity when the entity lacks it. Running before the artwork pipeline ensures the corrected album data is available for CAA cover art lookup and poster upgrade.

### FAILED: Album entity created with single's MusicBrainz IDs instead of parent album's
- **What was tried:** The import pipeline calls `resolve_album(artist, album, mb_release_id=video_item.mb_release_id)`, passing the *single's* `mb_release_id` (e.g. `58ac1539-…`). `get_or_create_album()` then creates the album entity with this single release ID. Later, `_re_resolve_sources()` performs a `musicbrainzngs` lookup from the album entity's wrong `mb_release_id`, gets the single's release-group (`088b00d4-…`), and sets it as `album_entity.mb_release_group_id`. Source reconstruction then creates a `source_type=album` source pointing to the single's RG. The 1c completion block didn't correct existing album entities because it only fires when `not item.mb_release_group_id`, which is False for already-processed items.
- **Why it failed:** For Groove Armada — I See You Baby, the album entity (id=120 "Vertigo") had `mb_release_id=58ac1539` (single release) and `mb_release_group_id=088b00d4` (single RG), year=2004 instead of the correct album release ID `eb7e98b6`, album RG `e514bda8`, year=1999. Source id=277 had `source_type=album` pointing to the single's RG. This meant CAA looked up single cover art for the "album" source instead of the actual album's cover art.
- **Lesson:** (1) `get_or_create_album()` must accept and persist `mb_release_group_id` — it previously ignored this field entirely. (2) The 1c block must pass the *album's* RG and release ID (from `mb_album_release_group_id` / `mb_album_release_id` in the MB search result), not the single's. (3) A separate correction block ("1d") must run even when `mb_release_group_id` is already set, to fix album entities where the RG equals the single's RG instead of the album's. (4) Source dedup must update `source_type` when an existing source has the wrong type.
- **Fix applied:** (a) Updated `get_or_create_album()` in all 3 resolver.py to handle `mb_release_group_id` in both existing-entity update and new-entity creation paths. (b) Updated 1c block in all 3 deferred.py to pass `mb_album_release_group_id` and `mb_album_release_id` to album entity, and force-correct existing entities with wrong IDs. (c) Added "1d" block in all 3 deferred.py that detects when `album_entity.mb_release_group_id == item.mb_release_group_id` (entity has single's RG), re-searches MB, and corrects the entity with the album's RG/release ID. (d) Updated source dedup in all 3 deferred.py to update `source_type` when it differs.

---

## Track: Anna of the North — The Dreamer

### FAILED: `_step_resolve_entities()` album fallback using `mb_release_id` instead of `_find_parent_album()` (Attempt 1)
- **What was tried:** When `resolve_metadata_unified()` returned `album=None` (despite the scraper tester finding `album="Lovers"` for the same song), `_step_resolve_entities()` fell to a fallback that looked up `metadata["mb_release_id"]` via `musicbrainzngs.get_release_by_id()` and checked if the release-group's primary-type was "album".
- **Why it failed:** For singles, `mb_release_id` points to the single's release (e.g. "The Dreamer"), whose release-group type is "single" — the fallback ALWAYS rejects it. The fallback could never succeed for any track where `mb_release_id` is a single release. Meanwhile, `resolve_metadata_unified()` uses `_find_parent_album(mb_recording_id)` which browses releases containing the recording to find Album-typed release groups — a fundamentally different and correct approach.
- **Root cause:** `_step_resolve_entities()` used a different album-finding code path than `resolve_metadata_unified()`. When the unified pipeline's album resolution failed non-deterministically (MusicBrainz API timing, AI identity clearing album, etc.), the entity resolution's independent fallback used the wrong approach (`get_release_by_id` on the single's release ID) instead of the same `_find_parent_album()` function.
- **Lesson:** Entity resolution fallbacks must use the SAME album-finding functions as the unified metadata pipeline. Duplicating album-finding logic with a different approach creates asymmetric failures that only manifest when the primary path fails.
- **Fix applied:** Replaced the `mb_release_id` lookup fallback in `_step_resolve_entities()` across all 3 `stages.py` files with: (1) `_find_parent_album(mb_recording_id)` — identical to `resolve_metadata_unified()`'s approach, and (2) `_find_album_by_artist_browse(mb_artist_id, title)` as a secondary fallback. Both functions propagate `album`, `mb_album_release_id`, and `mb_album_release_group_id` into metadata so downstream source collection and artwork fetching work correctly.

### ANTI-PATTERN: Entity resolution using different album-finding logic than the unified metadata pipeline
When `_step_resolve_entities()` has a fallback for album resolution, it must call the same functions (`_find_parent_album`, `_find_album_by_artist_browse`) that `resolve_metadata_unified()` uses internally. Using a bespoke `get_release_by_id()` lookup on the single's `mb_release_id` is fundamentally wrong because singles have type "single", not "album". The scraper tester has no separate entity resolution step — it relies entirely on `resolve_metadata_unified()` — so any divergence in the import pipeline's entity resolution creates results that differ from the scraper tester.

### FAILED: Remix suffix in title prevents Wikipedia single page lookup
- **What was tried:** `search_wikipedia("I See You Baby (Fatboy Slim Remix)", "Groove Armada")` constructs search queries containing the full title including the `(Fatboy Slim Remix)` suffix. Wikipedia's page is titled simply "I See You Baby".
- **Why it failed:** The remix parenthetical pollutes search queries and poisons the SequenceMatcher similarity gate — `"i see you baby (fatboy slim remix)"` vs `"I See You Baby"` gives a low ratio. The correct page is never matched or is rejected by the similarity threshold. This is the same class of problem as the `(feat. ...)` suffix bug already fixed, but for remix/version/edit parentheticals.
- **Lesson:** Remix and version suffixes — `(Fatboy Slim Remix)`, `(Acoustic Version)`, `(Radio Edit)`, `(Remastered)` — are not part of the canonical song title and must be stripped before Wikipedia search, just like featuring credits.
- **Fix applied:** Added `re.sub(r'\s*\([^)]*(?:remix|version|mix|edit|remaster(?:ed)?)\)\s*$', '', title, flags=re.IGNORECASE)` at the top of `search_wikipedia()` in all 3 metadata_resolver.py files, immediately after the existing feat-suffix strip.

### FAILED: MB single/album Source records never created — _re_resolve_sources runs before 1c/1d fills IDs
- **What was tried:** `_re_resolve_sources()` creates MusicBrainz Source records (single and album) from `item.mb_release_group_id` and `item.album_entity.mb_release_group_id`. It runs at the start of the deferred task, triggered by `_identity_changed`. The 1c/1d blocks that fill in missing `mb_release_group_id` values run later in the same deferred task.
- **Why it failed:** For Groove Armada, on the third import: `_re_resolve_sources` ran at 11:34:01 when `mb_release_group_id=None`, so no MB single or album Source was created. Then 1c ran at 11:34:29 and filled `mb_release_group_id=088b00d4`, and 1d corrected the album entity to `e514bda8`. But no code existed to create Source records after these IDs were filled. The Sources table had MB artist source only — no MB single (088b00d4) or MB album (e514bda8) sources.
- **Lesson:** Source creation must happen AFTER all ID-filling steps complete. Since `_re_resolve_sources` runs before 1c/1d, a separate backfill step is needed after 1c/1d to ensure MB single/album Source records exist.
- **Fix applied:** Added a "1e" block in all 3 deferred.py files, placed after 1d and artist-ID propagation, before the artwork pipeline call. This block checks for missing MB single source (`item.mb_release_group_id`) and MB album source (`album_entity.mb_release_group_id`) and creates them with dedup checking, ensuring both Source records exist regardless of whether `_re_resolve_sources` ran with complete data.

### FAILED: Video poster lookup has no release-group fallback when parent album exists
- **What was tried:** In the `_has_parent_album` branch of the poster upgrade section, `_fetch_front_cover(item.mb_release_id)` fetches the single's cover by its specific release ID. If CAA has no cover for that exact release, no poster is set — there's no fallback to the release-group endpoint.
- **Why it failed:** For Groove Armada — I See You Baby, `_fetch_front_cover("58ac1539")` returns None because that specific release has no CAA art. But `_fetch_front_cover_by_release_group("088b00d4")` returns the single's cover from a different pressing. The `not _has_parent_album` branch already had the release-group fallback, but it was missing from the `_has_parent_album` branch. Note: `pipeline_url/deferred.py` already had this fallback, but `pipeline/deferred.py` and `pipeline_lib/deferred.py` did not.
- **Lesson:** All three pipelines must stay in sync. Poster fallback logic must always include the release-group endpoint as a fallback when the specific release has no CAA cover.
- **Fix applied:** Added `_fetch_front_cover_by_release_group(item.mb_release_group_id)` fallback after `_fetch_front_cover(item.mb_release_id)` in all 3 deferred.py files' `_has_parent_album` branch.

### FAILED: Stale artist artwork preserved by overwrite=False validation
- **What was tried:** `download_and_validate()` with `overwrite=False` checks if the file already exists on disk. If it does, it runs `validate_file()` which opens the image with PIL to verify it's a valid image file. If valid, the download is skipped and the existing file is returned.
- **Why it failed:** For Groove Armada, the artist poster on disk was 324×307 pixels (15KB JPEG, ~1:1 aspect ratio) — a completely different image from the correct Wikipedia source (1280px wide, ~1.66:1 aspect ratio). Because `validate_file()` only checks PIL validity (can the file be opened as an image?), the stale/wrong image passed validation and the correct higher-resolution image was never downloaded.
- **Lesson:** PIL validity alone is insufficient for detecting stale artwork. When the existing file's dimensions are significantly below the requested `max_width`, it's likely a leftover from a previous run with different source data. A dimension heuristic catches this without requiring URL tracking.
- **Fix applied:** Added a stale-image heuristic in `download_and_validate()` in `artwork_service.py`: when `overwrite=False`, after validating the existing file, check if `max_width >= 600` and the file width is below 50% of `max_width`. If so, treat the file as stale — delete it and re-download from the current URL. This ensures undersized images from previous runs are replaced with the correct artwork.

### FAILED: 1e source backfill only checked existence, not source_type
- **What was tried:** The 1e block in deferred.py checked if a MB release-group source already existed (by `source_video_id`). If found, it was skipped. If not found, it was created with the correct `source_type`.
- **Why it failed:** For Groove Armada, `_re_resolve_sources` ran BEFORE 1c/1d filled the correct IDs. At that time, `album_entity.mb_release_group_id` was `088b00d4` (the single's RG, not the album's). So it created source 278 with `088b00d4` as `source_type="album"`. Later, 1c filled `item.mb_release_group_id=088b00d4`, and 1d corrected the album entity to `e514bda8`. When 1e ran, it found `088b00d4` already existed (as album) and skipped it. Then it created `e514bda8` as a second album source. Result: two album sources, no single source.
- **Lesson:** Source backfill must upsert (find-or-correct), not just find-or-create. When a source exists with the wrong `source_type`, it must be corrected. Additionally, enforce a hard limit of one MB release-group source per category.
- **Fix applied:** Rewrote the 1e block to: (1) correct `source_type` when a source exists with wrong type, (2) enforce max-one MB source per category by deleting any extras not matching the expected single/album RGs.

### FAILED: MediaAsset artwork records only deleted pending, not valid
- **What was tried:** Section 2b of the deferred task creates MediaAsset records for artist_thumb and album_thumb. Before adding the new record, it deleted existing ones matching `status == "pending"`.
- **Why it failed:** On re-import, the existing MediaAsset (from the first import) had `status="valid"` with stale dimensions (324×307). The delete filter only targeted `"pending"` records, leaving the old valid record untouched. The new record was added alongside it, creating duplicates — or if the commit failed (DB locking), only the stale record remained.
- **Lesson:** When replacing artwork records, delete ALL existing records for the same video+asset_type, not just pending ones. Re-imports should always reflect the freshly-downloaded file dimensions.
- **Fix applied:** Changed the MediaAsset delete filter to remove all matching records regardless of status before adding the fresh record.

### FAILED: Accessing item.album_entity after modifying album_entity_id without expiring relationship
- **What was tried:** Section 1c of `_deferred_entity_artwork` sets `item.album_entity_id = _alb_ent.id` and calls `db.flush()`. Subsequent sections (1d, 1e, poster/section 3) then access `item.album_entity` to read the album entity's `mb_release_group_id`.
- **Why it failed:** SQLAlchemy caches relationship attributes separately from foreign-key columns. After `item.album_entity_id` is changed via the FK and flushed, `item.album_entity` still returns the **old** (or `None`) entity unless the relationship is explicitly expired or refreshed. This caused: (1) Section 1e saw `_album_rg = None`, skipping MB album source creation entirely; (2) Section 3 (poster) computed `_has_parent_album = False` (because `item.album_entity` was `None`), falling to the `elif not _has_parent_album` branch which used `art_result["album_image_url"]` (the album cover) and mislabeled it "single_cover".
- **Lesson:** After modifying a SQLAlchemy foreign-key column (`item.album_entity_id`), always call `db.expire(item, ["album_entity"])` before any subsequent code reads the relationship. `db.flush()` alone does NOT refresh cached relationships.
- **Fix applied:** Added `db.expire(item, ["album_entity"])` between sections 1c and 1d in all 3 pipeline deferred.py files.

---

## Track: Fatboy Slim — Ya Mama (Push The Tempo)

### FAILED: `_confirm_single_via_artist` returning first match instead of best match (Attempt 1)
- **What was tried:** `_confirm_single_via_artist(mb_artist_id, title)` browsed the artist's single release groups and returned the FIRST release group whose title similarity exceeded 0.6.
- **Why it failed:** Fatboy Slim has multiple singles. "Ya Mama" (title_sim=0.55 against "Ya Mama (Push The Tempo)") appeared before the correct "Song for Shelter / Ya Mama" (sim=0.65). The first match was accepted despite a better match existing later in the list. Additionally, base-match boost (0.90) was too aggressive — "Ya Mama" received a 0.90 boost just for matching the base title, pushing it past the gate.
- **Lesson:** Single confirmation must evaluate ALL candidates and pick the best, not short-circuit on the first passing match. The base-match boost should be moderate (0.70), not near-perfect (0.90).
- **Fix applied:** Changed to evaluate all release groups and return the one with the highest title similarity. Lowered base-match boost from 0.90 to 0.70. Applied to all 3 copies of `metadata_resolver.py`.

### FAILED: AI Source Resolution dropping parenthetical subtitle, then Step B using AI title for MB search (Attempt 2)
- **What was tried:** AI Source Resolution (gpt-5-mini) resolved "Ya Mama (Push The Tempo)" → "Ya Mama" (dropped parenthetical). Step B in `unified_metadata.py` used the AI title for MusicBrainz search: `search_musicbrainz("Fatboy Slim", "Ya Mama")`. Without the parenthetical, MB returned the wrong single `d3eb803e` ("Ya Mama" standalone, different release-group from the correct double A-side `677213e8`).
- **Why it failed:** The AI normalised away subtitle info that MusicBrainz needs for disambiguation. Step B unconditionally preferred the AI title over the parsed title.
- **Lesson:** When the AI title is a substring of the parsed title, the parsed title contains additional disambiguation information (parentheticals, subtitles) that MusicBrainz needs. The parsed title should be preferred in these cases.
- **Fix applied:** Added substring check in Step B: if `ai_title.lower() in parsed_title.lower()` and they differ, use `parsed_title` for the MB search. Applied to both `app/scraper/unified_metadata.py` and `app/pipeline_lib/services/unified_metadata.py`.

### FAILED: Step 3a unconditionally deleting scraper poster when parent album exists (Attempt 3)
- **What was tried:** `deferred.py` Step 3a checked `_has_parent_album` and, when the CoverArtArchive returned 404 for the single's release, deleted the existing scraper poster (Wikipedia single art `YaMamaFatboySlim.jpg`) on the assumption it was album art mis-labeled as poster.
- **Why it failed:** The Wikipedia scraper correctly found the single's cover art from the single's Wikipedia page. This is NOT album art — it's legitimate single cover art. But Step 3a assumed all scraper posters are album art when a parent album exists and CAA has no single cover, so it deleted the correct poster leaving the video with no art.
- **Lesson:** Before deleting a scraper poster, compare its `source_url` against the album entity's `cover_image`. If they differ, the poster is from a different source (e.g. Wikipedia single page) and should be kept.
- **Fix applied:** Added `source_url` comparison: only delete scraper poster if its URL matches the album entity's cover_image. If they differ, log "Keeping scraper poster: source differs from album cover" and preserve it.

### FAILED: `description_generated` flag only set in `_deferred_ai_enrichment` (Attempt 4)
- **What was tried:** The `description_generated` processing state flag was only set inside `_deferred_ai_enrichment()`, which generates a plot via AI when the scraper didn't provide one.
- **Why it failed:** When the scraper already produced a plot (via Wikipedia + AI Final Review), `_deferred_ai_enrichment` is skipped entirely. The `description_generated` flag was never set, so the frontend's track history tile showed "No description generated" even though a valid plot existed in the DB.
- **Lesson:** Processing state flags must be set by the stage that actually produces the data, not just by one possible producer. When the scraper's AI Final Review generates the plot, the initial import stage must set the flag.
- **Fix applied:** Added `flags["description_generated"] = "import"` in `mutation_plan.py` when the scraper produces a plot with `ai_enriched` flag.

### FAILED: Scraper test constructing CoverArtArchive URLs without validating art exists (Attempt 5)
- **What was tried:** `scraper_test.py` constructed CAA URLs by pattern (`https://coverartarchive.org/release-group/{id}/front`) from MB IDs and listed them as artwork candidates without checking if the CAA actually has art for that release-group.
- **Why it failed:** For the correct single (release-group `677213e8`), CoverArtArchive returns 404 — no art exists. But the constructed URL was still added as an artwork candidate with source `musicbrainz_coverart` and given highest poster priority. The scraper tester displayed this dead link as the found poster art.
- **Lesson:** CoverArtArchive URLs should never be constructed by pattern and assumed valid. The existing `_fetch_front_cover_by_release_group()` and `_fetch_front_cover()` functions properly validate by querying the CAA API. Always use these validated functions.
- **Fix applied:** Replaced pattern-constructed URLs with calls to `_fetch_front_cover_by_release_group()` / `_fetch_front_cover()`. Candidates are only added when CAA returns a valid direct image URL. The `coverartarchive` source URL is also only included if valid art was found.

### ANTI-PATTERN: Constructing external API URLs by pattern without validation
Building URLs from IDs and adding them as valid resources without querying the API creates phantom references. Any URL that depends on external data availability must be validated before being treated as a live resource.

---

## Track: Paramore — Decode

### FAILED: Parsed title preference override not stripping artist-name prefix (Attempt 1)
- **What was tried:** Fix 2 (Ya Mama) added logic to prefer `parsed_title` over the AI title when the AI title is a substring of parsed_title. For "Paramore: Decode", the AI correctly identified title as "Decode". The substring check `"decode" in "paramore: decode"` evaluated to True, so the MB search used "Paramore: Decode" instead of "Decode".
- **Why it failed:** The YouTube title "Paramore: Decode [OFFICIAL VIDEO]" was parsed with the artist name embedded in the title (colon-separated). The parsed title "Paramore: Decode" contains the AI title "Decode" as a substring, but the extra content is just the artist name prefix — NOT useful disambiguation info like a parenthetical subtitle. MusicBrainz search for "Paramore: Decode" found no matching recording.
- **Lesson:** Before preferring parsed_title over AI title, strip any artist-name prefix. If the remaining content after stripping equals the AI title, the parsed title adds no disambiguation value — keep the AI title. Only prefer parsed_title when it contains genuinely additional info (parentheticals, subtitles, featured artists) beyond the artist name.
- **Fix applied:** Added artist-prefix stripping before the substring comparison. After removing the artist name and separator characters (`:`, `-`, `—`, `–`), if the cleaned remainder equals the AI title, keep AI title and log "parsed title only differs by artist prefix". If the cleaned remainder still contains extra info beyond the AI title, use the cleaned parsed title. Applied to both `app/scraper/unified_metadata.py` and `app/pipeline_lib/services/unified_metadata.py`.

### ANTI-PATTERN: Substring check without semantic analysis of the "extra" content
A simple `ai_title in parsed_title` check cannot distinguish between useful disambiguation (parenthetical subtitles, featured artists) and noise (artist name prefix, platform tags). The check must analyse WHAT makes the parsed title longer — only genuinely additional metadata justifies overriding the AI's cleaner title.

### FAILED: Scraper test poster priority short-circuited by `not any(c.applied)` guard (Attempt 2)
- **What was tried:** `scraper_test.py` poster priority block had `if _poster_cands and not any(c.applied for c in _poster_cands):` — it only evaluated priority when NO poster candidate was already marked applied.
- **Why it failed:** The Wikipedia scraper sets `applied=True` on its poster candidate during the scraper stage. This meant CoverArtArchive candidates (which have higher poster priority in the import pipeline's deferred upgrade) were never evaluated against the Wikipedia candidate. The scraper tester showed Wikipedia as the chosen poster when the real import pipeline would choose CoverArtArchive — a divergence between the tester and the actual import behavior.
- **Lesson:** The scraper tester must mirror the import pipeline's poster priority logic. The import pipeline's deferred poster upgrade unconditionally evaluates CoverArtArchive against the scraper poster and replaces it when CAA has higher priority. The tester's `not any(c.applied)` guard prevented this same evaluation, making the tester unreliable as a source of truth.
- **Fix applied:** Removed the `not any(c.applied)` guard from the poster priority block. Now always evaluates priority: sorts all poster candidates by `_poster_priority` order (`musicbrainz_coverart` > `wikipedia` > `yt-dlp`), and if the best candidate isn't already applied, demotes the current applied candidate and promotes the best one.

### ANTI-PATTERN: Priority evaluation gated by "already has a selection"
When multiple sources compete for the same output slot (poster, album art), the priority sort must always run — even when an earlier pipeline stage has already selected a candidate. Gating priority evaluation on "nothing selected yet" prevents higher-priority sources from overriding lower-priority ones.

### FAILED: Frontend PROV map missing `wikipedia_artist` and `wikipedia_album` source types (Bug)
- **What was tried:** `ScraperTesterPage.tsx` PROV map mapped source strings to display labels/colors for artwork candidate badges. The scraper tester rendered each candidate's source using `PROV[source]` with a fallback to `PROV["none"]` which displayed "Not Found".
- **Why it failed:** `unified_metadata.py` emits artwork candidates with sources `"wikipedia_artist"` and `"wikipedia_album"` (for artist and album artwork scraped from Wikipedia). These two source strings were missing from the PROV map. The fallback `PROV["none"]` displayed "NOT FOUND" as the source label, making it appear that the artwork had no identified source when it actually came from Wikipedia.
- **Lesson:** When adding new artwork candidate source types in the backend scraper, the frontend display map must be updated simultaneously. The fallback "Not Found" label is indistinguishable from a real error, masking the actual source provenance.
- **Fix applied:** Added `"wikipedia_artist"` → "Artist (Wikipedia)" and `"wikipedia_album"` → "Album (Wikipedia)" to the PROV map in `ScraperTesterPage.tsx`, both using the emerald color scheme matching the existing `"wikipedia"` entry.

### ANTI-PATTERN: Frontend display maps with silent fallback for unknown source types
When a display map falls back to a generic "Not Found" or "Unknown" label for unrecognized keys, new source types added in the backend silently degrade to misleading labels. Either the fallback should clearly indicate "unrecognized source: {key}" or the map should be validated against all possible backend values.

### FAILED: `search_album_wikipedia()` album name match weight too low — `(album)` suffix bonus dominates (Bug)
- **What was tried:** `search_album_wikipedia()` in `artist_album_scraper.py` scored Wikipedia search candidates with `+4` for album name appearing in page title, `+3` for `(album)` or `(ep)` disambiguation suffix, and `+2` for artist name in page title. No penalty when the album name was absent.
- **Why it failed:** For Paramore's "Brand New Eyes" album, the Wikipedia page "Paramore (album)" (the self-titled album) scored 8 (+3 for `(album)` suffix, +2 for artist name in title, +2 for snippet keywords, +1 for artist in snippet) while "Brand New Eyes" scored only 7 (+4 for album name match, +2 for snippet keywords, +1 for artist in snippet). The `(album)` suffix bonus (+3) combined with artist-in-title (+2) allowed a page that didn't contain the searched album name at all to outscore the exact match. Any artist with a self-titled album would exhibit this bug.
- **Lesson:** Album name presence in the page title must be the dominant scoring signal — it should outweigh all combinable bonuses from disambiguation tags and artist presence. Additionally, pages whose titles don't contain the album name should receive an explicit penalty, not just miss a bonus.
- **Fix applied:** Changed album name match weight from `+4` to `+6`. Added `-2` penalty when the album name is absent from the page title. After fix: "Brand New Eyes" scores 9 (+6 album match, +2 snippet, +1 artist snippet) vs "Paramore (album)" scores 4 (+3 album suffix, +2 artist in title, +1 artist snippet, -2 absent album name). Applied to all 3 copies of `artist_album_scraper.py` (`app/scraper/`, `app/pipeline_lib/services/`, `app/services/`).

### ANTI-PATTERN: Disambiguation suffix bonus without album name validation
Wikipedia disambiguation tags like `(album)`, `(ep)`, `(song)` indicate page type but NOT content relevance. Awarding +3 for `(album)` without requiring the album name to also appear in the title allows any album page to outscore actual matches. Disambiguation bonuses should only apply as a tiebreaker between pages that already match the searched album name.

---

## Track: Paramore — Decode (poster resolution fix)

### FAILED: `resolve_album()` receiving single's `mb_release_id` instead of album's (Attempt 1)
- **What was tried:** All 11 callsites across 7 files passed `metadata.get("mb_release_id")` (the video's single release ID) to `resolve_album()`. This is the single/EP release the track appears on, not the album.
- **Why it failed:** `resolve_album()` uses `mb_release_id` to look up the album on MusicBrainz via `get_release_by_id()`. Passing the single's release ID returns the single's release metadata (e.g. "Decode" single release a5e83c24) instead of the album's (e.g. "Twilight: Original Motion Picture Soundtrack" release). The album entity was then created with the single's release ID and cover art, making the album poster identical to the single poster.
- **Lesson:** `metadata["mb_release_id"]` is the VIDEO's release (typically a single). The ALBUM's release ID is a different field: `metadata["mb_album_release_id"]`. `resolve_album()` must receive the album's release ID to correctly identify the album entity. These two fields serve fundamentally different purposes and must never be confused.
- **Fix applied:** Changed all 11 callsites in 7 files from `metadata.get("mb_release_id")` to `metadata.get("mb_album_release_id")`:
  - `app/pipeline_url/stages.py`, `app/pipeline_lib/stages.py`, `app/pipeline/stages.py`: `resolve_album()` call in entity resolution step
  - `app/tasks.py`: `resolve_album()` calls in old pipeline path
  - `app/ai/metadata_service.py`, `app/pipeline_url/ai/metadata_service.py`, `app/pipeline_lib/ai/metadata_service.py`: Removed wrong `mb_release_id=video.mb_release_id` parameter

### ANTI-PATTERN: Using the video's single `mb_release_id` for album operations
`metadata["mb_release_id"]` points to the single/EP release — it is the VIDEO's primary MusicBrainz release. For album operations (`resolve_album()`, album entity creation, album cover art lookup), the correct field is `metadata["mb_album_release_id"]`. Passing the wrong ID produces an entity with the single's metadata masquerading as an album.

---

## Track: Peter Bence — The Awesome Piano (false positive Wikipedia album art)

### FAILED: `search_album_wikipedia()` penalty for missing album name too weak (Attempt 1)
- **What was tried:** `search_album_wikipedia()` in `artist_album_scraper.py` applied a `-2` penalty when the searched album name did not appear in the Wikipedia page title. Combined with bonuses from `(album)` disambiguation (+3), artist-in-title (+2), and snippet keywords (+2), a completely unrelated album page could still outscore the correct "no result" outcome.
- **Why it failed:** For Peter Bence's "The Awesome Piano", Wikipedia search returned "September Morn (album)" by Neil Diamond. The page title has `(album)` suffix → +3, snippet contained music keywords → +2. Missing album name penalty was only -2, leaving a net positive score that passed the minimum threshold. The wrong album page was accepted, and its infobox image (Neil Diamond's "September Morn" album cover) was used as artwork for Peter Bence.
- **Lesson:** When the album name is entirely absent from the page title, the page is almost certainly about a different album. A -2 penalty is insufficient to overcome the combined bonuses from disambiguation tags and snippet keywords. The penalty must be severe enough to make it mathematically impossible for non-matching pages to pass.
- **Fix applied:** Changed the "album name not in title" penalty from `-2` to `-10` in all 3 copies of `artist_album_scraper.py` (`app/scraper/`, `app/services/`, `app/pipeline_lib/services/`). After fix: "September Morn (album)" scores -3 (+3 album suffix, +2 keywords, -10 no name match) → rejected.

### ANTI-PATTERN: Weak penalties for negative signals in scoring functions
When a scoring function penalises a critical negative signal (like "the searched name doesn't appear in the result title") by only -2 while awarding +3/+2 for incidental signals (disambiguation tags, snippet keywords), the penalty is mathematically inadequate. Negative signals that indicate "this is the wrong entity" must have penalties large enough to guarantee rejection regardless of what bonuses accumulate.

---

## Track: Bastille & Hans Zimmer — Pompeii MMXXIII (AI Final Review JSON parse failure)

### FAILED: AI Final Review silently discarding corrections when gpt-5 response is truncated (Attempt 1)
- **What was tried:** `final_review.py` parsed the AI response using `json.loads(raw_text)`. When the gpt-5 model returned a truncated JSON response (e.g. cut off mid-string or mid-object due to token limits), `json.loads` raised `JSONDecodeError`. The exception handler logged a warning and returned `None`, causing `resolve_metadata_unified()` to skip all AI corrections.
- **Why it failed:** For Bastille & Hans Zimmer — Pompeii MMXXIII, the AI Final Review identified 3 corrections (artist→"Bastille & Hans Zimmer", genres→orchestral, plot→MMXXIII-specific). But the gpt-5 response was truncated at the token limit, producing invalid JSON. All 3 corrections were silently discarded. The MusicBrainz-supplied artist "Bastille" (without Hans Zimmer) became the final artist. The pipeline showed "AI Final Review applied 0 correction(s)" with no error — a silent data loss.
- **Lesson:** LLM responses are often truncated by token limits, producing valid-looking but incomplete JSON (e.g. `{"corrections": [{"field": "artist", "value": "Bastille &`). A single `json.loads` call with an exception fallback to `None` is insufficient. The parser must attempt repair of truncated JSON before giving up.
- **Fix applied:** Added `_try_parse_json(text)` in `final_review.py` — a 3-stage parser: (1) direct `json.loads`, (2) regex extraction of a JSON block from surrounding text, (3) truncation repair that closes unclosed strings, arrays, and objects by scanning bracket/brace depth and appending missing terminators. Also updated `source_resolution.py` to use the same robust parser. After fix, the Bastille test shows "AI Final Review applied 3 correction(s)".

### ANTI-PATTERN: Silent failure on JSON parse errors in AI response handlers
AI model responses are probabilistically truncated. Handling `JSONDecodeError` by returning `None` (skip all corrections) creates silent data loss. The handler must: (1) attempt repair of common truncation patterns, (2) log the raw response for debugging when repair fails, (3) distinguish between "no corrections needed" and "corrections exist but parsing failed". Never conflate parse failure with empty results.

---

## Track: Bastille & Hans Zimmer — Pompeii MMXXIII (wrong Wikipedia album art)

### FAILED: `album_scraper_wiki` performing independent Wikipedia search instead of using pre-resolved URL (Attempt 1)
- **What was tried:** In the scraper test pipeline, the `album_scraper_wiki` artwork candidate is produced by calling `get_album_artwork_wikipedia(album, artist)`, which internally calls `search_album_wikipedia(album, artist)` to find the Wikipedia album page, then scrapes the infobox image. This runs independently of the earlier pipeline step that already resolved the correct Wikipedia album page via AI source resolution and cross-fallback.
- **Why it failed:** For Bastille's "Bad Blood" album, the pipeline's AI source resolution and cross-fallback had already correctly found the Wikipedia page `Bad_Blood_(Bastille_album)` with the correct album cover (`Bastille_-_Bad_Blood_(Album).png`). This URL was stored in `metadata["_source_urls"]["wikipedia_album"]`. However, `get_album_artwork_wikipedia()` ignored this pre-resolved URL and performed its own `search_album_wikipedia("Bad Blood", "Bastille")` search. Wikipedia's search API returned the disambiguation page and then the EP page `Bad_Blood_(EP)` (a different album by a different artist), whose infobox image (`Bad_Blood_(EP).jpg`) was applied as the `album_scraper_wiki` artwork candidate. Since `album_scraper_wiki` had higher priority in the candidate list, the wrong EP cover was marked `*** APPLIED ***`, overriding the correct `wikipedia_album` candidate.
- **Lesson:** When the pipeline has already resolved a Wikipedia album URL through validated search and cross-fallback, downstream artwork scrapers must use that pre-resolved URL instead of performing their own independent (and potentially wrong) search. Independent re-searching risks finding different disambiguation results than the validated pipeline.
- **Fix applied:** Added optional `wiki_url` keyword parameter to `scrape_album_artwork()` and `get_album_artwork_wikipedia()` in all 3 copies (`app/scraper/`, `app/services/`, `app/pipeline_lib/services/artist_album_scraper.py`). When `wiki_url` is provided, it's used directly, skipping `search_album_wikipedia()`. Updated `scraper_test.py` to pass `wiki_url=metadata["_source_urls"]["wikipedia_album"]` (the pre-resolved URL from the pipeline). Callers without a pre-resolved URL (e.g. `artwork_manager.py`) continue to use the fallback search.

### ANTI-PATTERN: Redundant independent searches when a validated result already exists
When an upstream pipeline stage has already resolved and validated a resource URL (e.g. Wikipedia album page), downstream stages must reuse the validated URL rather than performing their own independent search. Independent re-searching creates a divergence risk — the new search may find a different result (different disambiguation, different ranking) than the validated one, undoing the upstream stage's careful resolution work.

---

## Track: Natasha Bedingfield — Unwritten (as featured in Anyone But You)

### FAILED: `_album_is_title_duplicate` discarding genuine parent album when names match (Attempt 1)
- **What was tried:** `_album_is_title_duplicate()` in `unified_metadata.py` normalizes and compares album vs title strings. When the album name matches the song title, the album is discarded with the log `"MusicBrainz: album 'Unwritten' matches title — discarded"`.
- **Why it failed:** MusicBrainz found the single "Unwritten" (release group `2520aff3-e605-3f86-ab4b-13005a24e972`) AND the parent album "Unwritten" (release group `9b29b689-01a9-3da4-aa2b-b13ee0e100ac`). These are two DIFFERENT release groups — the single and the album are distinct entities that happen to share the same name. The duplicate check only compared strings, never checking whether MB had found genuinely different release groups.
- **Lesson:** When a song and its parent album share the same name (common for debut albums: "Unwritten", "19", "Thriller", etc.), string comparison alone will always produce a false positive. The duplicate check must also compare `mb_album_release_group_id` vs `mb_release_group_id` — if they differ, the album is a genuine parent album, not a title echo.
- **Fix applied:** Added `_has_distinct_rg` check: when `_album_is_title_duplicate` returns True but `mb_album_release_group_id != mb_release_group_id`, accept the album as a genuine parent album with log `"matches title but has distinct release group — accepted"`. Applied to all 3 copies of `unified_metadata.py`.

### FAILED: `detect_article_mismatch()` rejecting song pages that mention films via "premiered in" (Attempt 1)
- **What was tried:** `detect_article_mismatch()` in `metadata_resolver.py` checks the first 500 characters of the Wikipedia page's plot for `_non_music_phrases` including `"premiered in"`. If any phrase matches, the article is rejected as a TV/film page.
- **Why it failed:** The Wikipedia page `Unwritten_(song)` is a legitimate song article. Its plot mentions that the song was featured in the 2023 film "Anyone But You" which "premiered in" that year. The phrase `"premiered in"` triggered the non-music detection, rejecting the correct song page with `"non-music article detected (plot mentions TV/film content)"`. The same page was found again via search fallback and rejected again for the same reason.
- **Lesson:** `"premiered in"` is too broad — song Wikipedia pages commonly describe film/TV placements in their opening paragraphs. Unlike `"television series"` or `"tv series"` (which strongly indicate a TV page), `"premiered in"` frequently appears on music pages that describe the song's media appearances. The remaining phrases (`"television series"`, `"tv series"`, `"reality series"`, `"television show"`, `"reality show"`, `"television program"`, `"premiered on"`, `"seasons and"`, `"was renewed"`, `"streaming service"`) are sufficient to catch actual TV/film articles.
- **Fix applied:** Removed `"premiered in"` from `_non_music_phrases` in all 3 copies of `metadata_resolver.py`. Note: `"premiered on"` is retained — it's more specific to TV shows ("premiered on CBS", "premiered on Netflix").

### FAILED: `extract_album_wiki_url_from_single` not wired into `unified_metadata.py` (Attempt 1)
- **What was tried:** `extract_album_wiki_url_from_single()` exists in `metadata_resolver.py` and is called in `stages.py` (the pipeline path), but was never called in `unified_metadata.py` (the scraper tester path). The function scrapes a Wikipedia single/song page's infobox and follows the "from the album" link to discover the album's Wikipedia page.
- **Why it failed:** When both MB and Wikipedia failed to resolve an album through their primary paths (MB discarded due to title match, Wikipedia rejected due to "premiered in"), there was no fallback to extract the album from the single's Wikipedia infobox — because the function was only wired into the pipeline, not the unified metadata resolver used by the scraper tester.
- **Lesson:** When a fallback function exists in the codebase, it must be wired into ALL code paths that need it. The scraper tester uses `resolve_metadata_unified()` in `unified_metadata.py`, which is separate from the pipeline's `stages.py`. Both paths need the same fallback logic.
- **Fix applied:** Added a "Wikipedia album-link fallback" section in `unified_metadata.py` (all 3 copies) after the Wikipedia resolution block. When `metadata["album"]` is still None and a Wikipedia source URL exists, calls `extract_album_wiki_url_from_single()` to follow the infobox album link. If found, scrapes the album page and sets `metadata["album"]` from the album page title (with the same `_album_is_title_duplicate` + `_has_distinct_rg` guard).

### ANTI-PATTERN: Overly broad non-music phrase matching in Wikipedia article validation
Phrases like `"premiered in"` appear naturally on music Wikipedia pages that describe a song's use in films, commercials, or TV shows. Non-music phrase lists must be specific to TV/film PAGE TYPES, not phrases that merely mention media appearances. A song page saying "the song premiered in Anyone But You" is categorically different from a TV page saying "the series premiered in 2019".

### ANTI-PATTERN: Fallback functions wired only into one code path
When a function provides critical fallback behavior (like following Wikipedia infobox links), it must be wired into every code path that could benefit from it — not just the pipeline stages. The scraper tester's `unified_metadata.py` and the pipeline's `stages.py` are independent code paths that both need the same fallback logic.

---

## Track: Motion City Soundtrack — L.G. FUAD

### FAILED: MusicBrainz single search accepting B-side tracks without penalty (Attempt 1)
- **What was tried:** `_search_single_release_group()` in `metadata_resolver.py` scored release-group candidates by title similarity and artist match, but did not check the target track's position within the release.
- **Why it failed:** MusicBrainz found the single "Broken Heart" which contains "L.G. FUAD" as track 5 (B-side). The single search accepted it because the title "Broken Heart" scored well enough and the artist matched. The MB resolution returned `release_group_id` for "Broken Heart" instead of the correct "L.G. FUAD" single, giving the wrong album/single metadata.
- **Lesson:** When searching MusicBrainz for a *single* release, a track appearing at position ≥ 3 on the release is almost certainly a B-side or bonus track, not the A-side. These should be penalized or rejected when looking for a *specific single*.
- **Fix applied:** Added B-side detection in `_search_single_release_group()` across all 3 copies of `metadata_resolver.py`. When the target track appears at position ≥ 3 in the release's tracklist, the candidate receives a -30 scoring penalty, effectively eliminating B-side matches.

### FAILED: Parsed title with literal enclosing quotes degrading MusicBrainz search (Attempt 1)
- **What was tried:** `extract_artist_title()` sometimes returns titles wrapped in literal quote characters (e.g. `"L.G. FUAD"` with actual `"` chars). These were passed directly to MusicBrainz search functions.
- **Why it failed:** The literal quote characters in the search query degraded the MusicBrainz API's fuzzy matching, causing it to miss the correct single release and fall through to inferior candidates.
- **Lesson:** Parsed titles must be stripped of enclosing quote characters before being used as search terms.
- **Fix applied:** Added quote-stripping (`title.strip('"\u2018\u2019\u201c\u201d')`) in `resolve_metadata_unified()` in both copies of `unified_metadata.py`, applied before passing the title to any downstream search function.

---

## Track: Mason — Exceeder

### FAILED: Artwork manager ignoring pipeline-discovered Wikipedia artist URL (Attempt 1)
- **What was tried:** The unified pipeline (`resolve_metadata_unified`) correctly found the Wikipedia artist page via infobox cross-link: `Mason_(DJ)`. The artist image was scraped and stored in `metadata["_artwork_candidates"]` with `art_type="artist"`. However, `tasks.py`'s artwork pipeline called `process_artist_album_artwork()` which does its own independent Wikipedia name-search via `get_artist_artwork()` → `search_artist_wikipedia()`.
- **Why it failed:** The artwork manager's independent search for "Mason" matched `Mason (band)` (score=9) instead of `Mason_(DJ)`. The `Mason (band)` Wikipedia page had no artist image, so the result was "No artist image found for: Mason". The correct image from the pipeline's `_artwork_candidates` was never used because `tasks.py` never read `_artwork_candidates`.
- **Why the scraper test worked:** The scraper test (`scraper_test.py`, line ~609) reads `_artwork_candidates` from the unified pipeline metadata and includes them in the final artwork candidate list. So the pipeline-found artist image from `Mason_(DJ)` was correctly reported as available.
- **Lesson:** When the unified pipeline has already discovered a correct Wikipedia page via cross-links and scraped the image, that result must be passed through to the artwork pipeline. Independent re-searching by name can match the wrong Wikipedia disambiguation (especially for common names like "Mason", "Berlin", "America"). The pipeline's result should be used as a fallback when the artwork manager's own search fails.
- **Fix applied:** In `tasks.py`, after `resolve_metadata_unified`, extract the artist image URL from `metadata["_artwork_candidates"]` (where `art_type == "artist"`). After `process_artist_album_artwork`, if `art_result["artist_image_url"]` is None, call `ensure_artist_artwork` directly with the pipeline-found URL. This mirrors the existing album cross-link override pattern.

### FAILED: AI Final Review JSON parser too fragile for LLM output variations (Attempt 1)
- **What was tried:** `_try_parse_json()` in `final_review.py` had 3 fallback strategies: (1) direct `json.loads`, (2) regex extract outermost `{…}`, (3) truncation repair for unclosed brackets.
- **Why it failed:** The AI (OpenAI, temperature=0.3) returned a response of 1,894 characters that started with valid JSON (`'{\n  "proposed": {\n    "artist": "Mason"...'`) but all 3 strategies failed to parse it. The failure was silent — no debug logging indicated which strategy failed or where the parse error occurred. The result: `AI final review: failed — JSON parse failure`, causing the production pipeline to miss title corrections (e.g. "Exceeder" → "Exceeder (Original Mix)") and plot enrichment that the scraper test received on a separate AI call.
- **Lesson:** LLM JSON output can contain control characters, trailing commas, or other non-standard JSON that `json.loads` rejects. The parser must sanitize the input before parsing and log detailed errors at each fallback stage for diagnostics.
- **Fix applied:** Added a sanitization step to `_try_parse_json()` before the regex-extract fallback: (1) strip control characters (`\x00-\x08`, `\x0b`, `\x0c`, `\x0e-\x1f`) and (2) remove trailing commas before `}` / `]`. Added `logger.debug` at each parse failure with the exception details. Enhanced the warning log in `_parse_final_review_response` to include the last 200 chars of long responses. Applied to all 3 copies of `final_review.py`.

### ANTI-PATTERN: Artwork pipeline re-searching for data the scraper already found
When the scraper pipeline has already resolved a Wikipedia page via cross-links (which are high-confidence — they follow explicit infobox relationships), that result should be preserved and used by downstream pipelines. Re-searching by artist name alone introduces ambiguity for common or disambiguation-heavy names. The pattern of "pipeline finds correct result → downstream component ignores it and searches independently → gets wrong result" is a data-loss bug.

### ANTI-PATTERN: Silent JSON parse failures with no diagnostic output
When an AI response fails to parse as JSON, the error log must include: (1) which parse strategy failed, (2) the exact `JSONDecodeError` position/message, and (3) enough of the response content (both start and end) to diagnose the issue. A generic "failed to parse" message with only the first 300 chars is insufficient when the error may be in the middle or end of the response.

---

## Track: Chappell Roan — Pink Pony Club

### FAILED: CoverArtArchive release-group endpoint returning wrong release's cover art (Attempt 1)
- **What was tried:** `fetch_caa_artwork()` in `artwork_selection.py` used only `_fetch_front_cover_by_release_group(mb_release_group_id)` for singles. This was based on the assumption that the release-group endpoint returns a canonical representative cover for the single.
- **Why it failed:** The "Pink Pony Club" release-group (`c7085645`) has 11 releases. CAA's release-group endpoint redirected to release `32a5d34e` — the "music video" release — whose cover art is a video screenshot (Chappell Roan in a cowboy hat), not the actual single artwork. The correctly matched release `f6e54331` (the 4" vinyl) has its own distinct cover art on CAA, but was never tried because the code went straight to the release-group endpoint.
- **Lesson:** The CAA release-group endpoint picks whichever release it considers "canonical" which can be any release in the group — including music video releases with video screenshots as cover art. For singles, the specific matched release (`mb_release_id`) should be tried first, with the release-group as fallback only when the specific release has no cover.
- **Fix applied:** Changed `fetch_caa_artwork()` to try `_fetch_front_cover(mb_release_id)` first for singles, falling back to `_fetch_front_cover_by_release_group(mb_release_group_id)` only when the specific release has no CAA cover.

### ANTI-PATTERN: Trusting release-group endpoints to return the "best" cover art
CoverArtArchive's `/release-group/{id}` endpoint returns cover art from whichever release CAA considers canonical, not necessarily the one that was matched by the scraper. When a release-group contains releases of different formats (audio, music video, vinyl), the "canonical" cover can be a video screenshot or an alternate pressing's art. Always prefer the specific matched release's cover art and use the release-group as a fallback.

### FAILED: Wikipedia-only scrape crashes with UnboundLocalError on scrape_wikipedia_page (Attempt 1)
- **What was tried:** Running "Scrape Metadata" in Wikipedia-only mode (`scrape_wikipedia=True, scrape_musicbrainz=False`) for any track. The production pipeline in `scrape_metadata_task()` calls `scrape_wikipedia_page(wiki_url)` at line ~2498 to scrape the found Wikipedia page.
- **Why it failed:** A later block in the same function (line ~2671) had a local import: `from app.scraper.metadata_resolver import scrape_wikipedia_page`. In Python, when a name is assigned anywhere in a function body (including via `from X import Y`), that name becomes a local variable for the **entire** function at compile time. So when line 2498 tries to call `scrape_wikipedia_page()`, Python sees it as an unbound local variable (assigned later but not yet) and raises `UnboundLocalError: local variable 'scrape_wikipedia_page' referenced before assignment`. Every Wikipedia-only scrape attempt crashed silently with this error.
- **Why the scraper tester worked:** The scraper tester calls `resolve_metadata_unified()` which handles Wikipedia scraping internally in a separate module scope, never hitting the same function-level scoping conflict.
- **Lesson:** Never re-import a name inside a function body that is already imported at module level. If a different module's version is needed locally, always use an alias (`import X as _X`) to avoid shadowing the global name across the entire function scope.
- **Fix applied:** Changed the local import at line ~2671 to use an alias: `scrape_wikipedia_page as _scrape_wiki_page`. Also removed two other redundant local imports (`_find_parent_album`, `get_settings`) in the same file that shadowed their global imports.

### ANTI-PATTERN: Local imports shadowing global imports in long functions
Python determines variable scope at compile time, not runtime. A `from X import Y` inside any branch of a function makes `Y` a local variable for the *entire* function, even before the import line executes. In long functions (1000+ lines), a local import deep in the function can silently break earlier references to the same name. Always use `import Y as _alias` for local imports that share names with globals, or remove redundant local imports entirely.

### FAILED: Apply scrape results deletes shared entity artwork (artist_thumb/album_thumb) (Attempt 1)
- **What was tried:** `apply_ai_fields()` in `metadata_service.py` promoted pending `artist_thumb`/`album_thumb` MediaAssets to valid. It deleted the old valid asset's file via `os.remove()`, then moved the pending file to a canonical per-video path via `os.replace()`.
- **Why it failed:** Both old valid and pending `artist_thumb` assets pointed to the **same shared entity file** (e.g. `_artists/Staind/poster.jpg`). Step 1 deleted the file, then step 2 tried to move it but `os.path.isfile()` returned False (just deleted). Result: the valid asset points to a non-existent canonical path, artist art disappears from the UI. The undo path had the same bug — deleting pending `artist_thumb` files would also destroy entity artwork.
- **Lesson:** Per-video MediaAssets for entity artwork (artist_thumb, album_thumb) point to **shared** files in `_artists/` and `_albums/` directories. These files must never be deleted or moved by per-video operations. Always use copy (not move) when creating per-video artwork from entity sources, and guard deletes against shared file paths.
- **Fix applied:** (1) In apply: skip `os.remove()` when old and pending point to the same file (`normpath` comparison). Use `shutil.copy2()` instead of `os.replace()` so entity artwork is preserved. (2) In undo: only delete pending files that are inside the video's own folder (per-video files), not entity artwork in `_artists/`/`_albums/`.

### ANTI-PATTERN: Using os.remove/os.replace on shared entity artwork files
Entity artwork files (`_artists/Name/poster.jpg`, `_albums/Artist/Album/poster.jpg`) are shared across all videos by that artist/album. Per-video operations (apply, undo, re-scrape) must never delete or move these files. Use `shutil.copy2()` to create per-video copies, and guard file deletion with a check that the file is inside the video's own folder path.

---

## Track: The Chats — Pub Feed (Session 28)

### FAILED: MusicBrainz entity resolver search_artist accepting wrong artist at similarity boundary (Attempt 1)
- **What was tried:** `search_artist()` in the entity resolver's MusicBrainz provider validated search results with `SequenceMatcher(None, name.lower(), result_name.lower()).ratio()` and threshold `if _sim < 0.7: continue`.
- **Why it failed (two compounding issues):**
  1. **MusicBrainz search API returns wrong artist for "The Chats":** The top 5 results are The Beatles (score=100), The E Street Band, The Rolling Stones, The Beach Boys, The Smashing Pumpkins. "The Chats" does not appear at all — these are all large/famous bands that dominate MusicBrainz's relevance scoring for variations of "The ____".
  2. **Similarity threshold too permissive:** `SequenceMatcher("the chats", "the beatles")` = exactly **0.70**. The threshold `< 0.7` (strict less-than) allows 0.70 to pass. The Beatles was accepted as a valid match for The Chats.
  3. **get_artist MBID lookup had lower confidence (0.95) than search score (1.0):** When `mb_artist_id` was provided, `get_artist(mbid)` correctly returned The Chats with confidence=0.95. But `search_artist("The Chats")` also ran and returned The Beatles with confidence=1.0 (ext:score=100/100). `_pick_best("canonical_name")` selected The Beatles because 1.0 > 0.95, overriding the authoritative MBID lookup.
- **Result:** Entity 210 (The Beatles, mb_id=b10bbbfc) was assigned to video 122 instead of creating a new "The Chats" entity. Artist artwork showed Beatles poster.
- **Lesson:** (a) MusicBrainz search results must have a higher similarity threshold to prevent near-miss false positives from dominating bands. (b) MBID direct lookup is the most authoritative data source — its confidence must be at least equal to the maximum possible search score so it can never be overridden by fuzzy name search.
- **Fix applied:**
  1. Raised search similarity threshold from `< 0.7` to `< 0.75` in all 3 pipeline copies of `providers/musicbrainz.py`. The Beatles (sim=0.70) is now rejected. Verified legitimate matches still pass: "Flo Rida"/"FloRida" (0.93), "Jay-Z"/"Jay Z" (0.80), "P!nk"/"Pink" (0.75).
  2. Changed `get_artist(mbid)` confidence from 0.95 to 1.0 in all 3 copies. Direct MBID lookup is authoritative and cannot be overridden by fuzzy search results.

### ANTI-PATTERN: MBID direct lookup with lower confidence than fuzzy search
When `get_artist(mbid)` returns the definitive answer for a known MBID, its confidence must be maximal. Setting it below the search score ceiling (1.0) allows unrelated but high-scoring search results to override the authoritative lookup via `_pick_best`. Direct lookups by ID are always more trustworthy than fuzzy name searches.

---

## Track: Tom MacDonald — I'm Corny (Session 28)

### FAILED: `_extract_high_res_image` accepting SVG images from Wikipedia infoboxes (Attempt 1)
- **What was tried:** `_extract_high_res_image(infobox)` used `infobox.find("img")` to get the first `<img>` tag in the Wikipedia infobox and returned its `src` URL as the artist image.
- **Why it failed:** Tom MacDonald's Wikipedia infobox image is `Tom_MacDonald_sig.svg` — his autograph/signature rendered as an SVG. `infobox.find("img")` takes the first image unconditionally, so this SVG was returned as the artist artwork. SVG files are logos, signatures, or wordmarks — not raster photographs suitable for display as artist art. Both the `wikipedia_artist` and `artist_scraper` artwork candidates returned the same SVG URL.
- **Lesson:** Wikipedia infoboxes can contain SVG images (signatures, logos, wordmarks, coat-of-arms) that are not suitable as artist/album artwork. The extraction function must skip SVG images and prefer the next raster image (JPEG, PNG, WebP).
- **Fix applied:** Changed `_extract_high_res_image` from `infobox.find("img")` to iterating `infobox.find_all("img")`, skipping any `src` containing `.svg` (case-insensitive). Returns the first non-SVG image, or `None` if all images are SVGs. Applied to all 3 copies of `artist_album_scraper.py`.

### FAILED: MusicBrainz URL relations returning SVG image URLs without filtering (Attempt 1)
- **What was tried:** `search_artist_musicbrainz()` iterated URL relations with `type=="image"` and passed the target URL through `_resolve_commons_url()`, accepting the result unconditionally as the artist image.
- **Why it failed:** Tom MacDonald's MusicBrainz URL relations include an image link pointing to the same `Tom_MacDonald_sig.svg` on Wikimedia Commons. `_resolve_commons_url()` correctly resolved the file URL but had no SVG filtering. The SVG was accepted as `result["image_url"]` — the same signature file from a second source.
- **Lesson:** MusicBrainz URL relations of type `"image"` can point to any Wikimedia Commons file, including SVGs. After resolving the URL, the result must be checked for SVG file extensions before acceptance.
- **Fix applied:** After `_resolve_commons_url(rel["target"])` returns, check if the URL ends with `.svg` (case-insensitive). If so, `continue` to the next relation instead of accepting it. Applied to both the direct-lookup and name-search paths in all 3 copies of `artist_album_scraper.py` (6 code locations total).

### ANTI-PATTERN: Accepting any image format from external sources without validation
Wikipedia infoboxes and MusicBrainz URL relations can contain SVG files (vector graphics used for logos, signatures, wordmarks, coats of arms). These are never suitable as artist/album artwork — they are not photographs or album covers. Any image URL obtained from external sources must be validated for raster format (JPEG, PNG, WebP) before use as artwork.

### FAILED: MusicBrainz Strategy 2 picking wrong recording on third-party compilation instead of artist's own album (Adam Cohen — "We Go Home")
- **What was tried:** `search_musicbrainz()` Strategy 2 iterated filtered recordings and picked the one whose best release had the highest `_RELEASE_TYPE_PRIORITY`. All 4 recordings for "We Go Home" had `primary-type=Album` → priority=1. The first one iterated (on "The Sound of September (Maxi 2014-10)", a German compilation sampler) won by iteration order.
- **Why it failed:** Two issues:  
  1. `_RELEASE_TYPE_PRIORITY` only checked `primary-type`, ignoring `secondary-type-list`. Compilations with primary-type "Album" and secondary-type "Compilation" received the same priority as the artist's own album.  
  2. When multiple recordings tied on priority, no tiebreaker existed — the first one won by iteration order, which was the compilation, not the artist's own album "We Go Home".
- **Lesson:** `secondary-type-list` containing "Compilation" must be penalized. When priorities tie, prefer the recording whose release title matches the search title (title-track heuristic — the artist's own album named after the song is more relevant than a random compilation).
- **Fix applied:** (1) Added +3 priority penalty for releases with "Compilation" in `secondary-type-list`, in both `_pick_best_release()._sort_key` and the outer recording selection loop. (2) Added tiebreaker in the recording loop: when priorities are equal, prefer the recording whose release title matches the search title.

### FAILED: Wikipedia cross-link extracting generic "Album" article URL from album page infobox (Adam Cohen — "We Go Home")
- **What was tried:** `extract_wiki_infobox_links()` searched infobox-header elements for text containing "album", then extracted the first `<a>` tag's href. On the We_Go_Home album page, the infobox header contains `[[Studio album]] by [[Adam Cohen]]` — the code matched "album" in the text and extracted the wikilink to the generic Wikipedia article `https://en.wikipedia.org/wiki/Album`.
- **Why it failed:** The function was designed for single/song pages where the infobox says "from the album [[Album Name]]". When called on an album page, the type label "Album" (linking to the generic article) is incorrectly interpreted as a link to a specific album.
- **Lesson:** After extracting album URLs from infobox, validate that the URL doesn't point to a generic music-concept article (Album, EP, Single, Studio_album, etc.).
- **Fix applied:** Added a `_GENERIC` frozenset of known generic music-concept Wikipedia page names. After album URL extraction, check if the extracted page name is in this set and reject it if so.

### ANTI-PATTERN: Treating infobox type labels as content links
Wikipedia album page infoboxes use wikilinks in their type descriptors (e.g. `[[Studio album]] by [[Artist]]`). These links point to generic concept articles, not to specific albums. Any infobox link extraction must distinguish between type labels and content references.

---

## Track: Alien Ant Farm — Smooth Criminal

### FAILED: Exact title equality in `extract_single_wiki_url_from_album()` tracklist matching (Attempt 1)
- **What was tried:** `extract_single_wiki_url_from_album()` in `metadata_resolver.py` scraped `<table class="tracklist">` tables and compared each cell's normalized text against the search track title using exact equality: `if cell_norm != _track_norm and cell_lower != _track_lower: continue`.
- **Why it failed:** Wikipedia tracklist cells frequently contain parenthetical annotations after the title. On the ANThology album page, track 12 renders as `"Smooth Criminal" (new version; originally recorded for Greatest Hits, titled "Slick Thief".)`. After normalization, the cell text becomes `"smooth criminal new version originally recorded forgreatest hits titled slick thief"`, which does not equal `"smooth criminal"`. The exact match fails and the row is skipped — even though it contains a valid link (`/wiki/Smooth_Criminal#Alien_Ant_Farm_version`). This same pattern affects any Wikipedia tracklist with version notes, remix info, or featured artists in the cell text.
- **Lesson:** Wikipedia tracklist cells are not clean titles — they commonly include parenthetical annotations. Title matching must use prefix matching (with word boundary) rather than exact equality. Additionally, the `<a>` tag text within the cell is typically the clean title without annotations, providing a reliable secondary match path.
- **Fix applied:** (1) Changed title comparison from exact equality to prefix matching: `cell_norm.startswith(_track_norm + " ")` or exact equality. The `+ " "` word boundary prevents false partial matches. (2) Added link-text fallback: when the cell-text prefix match fails, check the `<a>` tag's text directly (which is typically the clean title). Applied in both `app/scraper/metadata_resolver.py` and `app/services/metadata_resolver.py`.

### FAILED: Cross-fallback unconditionally merging metadata from cover song's original artist page (Attempt 2)
- **What was tried:** After fixing the tracklist matching, `extract_single_wiki_url_from_album()` now correctly finds the link `/wiki/Smooth_Criminal#Alien_Ant_Farm_version`. The cross-fallback in `unified_metadata.py` then calls `scrape_wikipedia_page(_xref_single_url)` and unconditionally merges `year`, `genres`, `image_url`, and `plot` from the scraped page. No `expected_artist` was passed to `scrape_wikipedia_page`.
- **Why it failed:** The URL `Smooth_Criminal#Alien_Ant_Farm_version` is a section anchor on Michael Jackson's "Smooth Criminal" page. HTTP clients strip the `#fragment` before sending, so the scraper fetches MJ's full page. The infobox contains MJ's year (1988), genres (synth-funk, pop, R&B), and single cover art. The cross-fallback merges all of these into AAF's metadata — contaminating it with the original artist's data. The AI-provided URL path had a cover-song guard (comparing infobox artist to expected artist) but the cross-fallback path did not.
- **Lesson:** Any code path that scrapes a Wikipedia page discovered via tracklist links must check for cover songs before merging metadata. Cover song signals: (1) URL contains a `#fragment` that references the covering artist's name (e.g. `#Alien_Ant_Farm_version`), (2) the scraped page's infobox artist doesn't match the expected artist. When a cover is detected, the Wikipedia URL should still be stored for reference (linking), but ALL metadata must be skipped — plot text is also unsafe because it describes the original artist's version (e.g. MJ's biography, not AAF's cover).
- **Fix applied:** Added two-layer cover-song detection in the cross-fallback block of `unified_metadata.py`: (1) Fragment detection — when the URL has `#fragment` containing the resolved artist name, flag as cover. (2) Infobox artist mismatch — after scraping, compare infobox artist vs resolved artist. When cover detected: skip all metadata (plot, year, genres, image_url), store the URL as `wikipedia_cover_ref` for reference linking, log `wikipedia:mb_xref_cover` as source. Also now passes `expected_artist` to `scrape_wikipedia_page`.

### ANTI-PATTERN: Unconditional metadata merge from Wikipedia pages discovered via tracklist links
When following internal Wikipedia links (tracklist → song page, infobox → album page), the target page may belong to a different artist than the one being processed. This is common with cover songs, where the tracklist links to the original artist's page (possibly via a section anchor like `#Cover_version`). Any metadata merge from a linked page must verify that the page's infobox artist matches the expected artist before merging ANY fields. Even plot/description text is unsafe for cover songs — the article describes the original version (e.g. MJ's single history, not AAF's cover). The only safe action is storing the URL for reference linking.

---

## Track: Ann Lee — 2 Times

### FAILED: Provenance-only guard for CoverArtArchive poster upgrade skip (Job 163)
- **What was tried:** All four poster upgrade paths (tasks.py scrape path, pipeline/deferred.py, pipeline_lib/deferred.py, pipeline_url/deferred.py) checked whether an existing poster had provenance `artwork_pipeline` and, if so, skipped the CoverArtArchive poster upgrade entirely. The assumption was that any `artwork_pipeline` poster was already correct CAA art.
- **Why it failed:** During the original import (job 143), `fetch_caa_artwork()` correctly tried the release-group endpoint first, but that endpoint failed transiently (CAA outage or timeout). The code fell back to the specific release endpoint, which returned art from release `24d1e1f3` — the "2 Times (UK remix)" pressing with different cover art than the canonical single. This wrong-release art was saved with provenance `artwork_pipeline`. When job 163 (AI Auto scrape) ran later, the guard saw `artwork_pipeline` and skipped the upgrade — even though `fetch_caa_artwork()` now returns the correct canonical release-group art (from release `8db9ae05`).
- **Lesson:** Provenance alone is insufficient to determine whether a poster is correct. Two CAA posters can both have `artwork_pipeline` provenance but come from completely different releases with different artwork. The guard must compare the *source URL* of the existing poster against the URL that `fetch_caa_artwork()` currently returns. Only skip when they match.
- **Fix applied:** Changed all four poster upgrade paths to always call `fetch_caa_artwork()`, then compare the returned URL against `_existing_poster.source_url`. Only skip when provenance is `artwork_pipeline` AND the source URL matches. This allows re-upgrades when the canonical CAA art differs from what was previously fetched (e.g., after a transient failure caused the wrong release's art to be saved).

### ANTI-PATTERN: Using provenance as a proxy for content correctness
A media asset's provenance ("artwork_pipeline", "scraper", "thumb_fallback") only records *how* the asset was obtained, not *what* it contains. Two assets with identical provenance can have completely different content if they were fetched from different source URLs. Any guard that decides whether to replace an existing asset must compare the actual source URL or content hash — not just the provenance label.

---

## Track: Anna of the North — The Dreamer

### FAILED: `search_album_wikipedia` without similarity gate (substring false positive)
- **What happened:** Searching Wikipedia for album "Lovers" by Anna of the North returned "The Modern Lovers (album)" — a completely unrelated album. The function scored candidates using substring containment (`album_lower in pt_lower`), so "lovers" inside "the modern lovers" earned +6 points plus "(album)" bonus +3, passing the threshold.
- **Root cause:** `search_album_wikipedia` in `artist_album_scraper.py` lacked the SequenceMatcher similarity gate that the pipeline version (`search_wikipedia_album` in `metadata_resolver.py`) already had. The pipeline version iterates candidates and requires a 0.7 ratio on the cleaned title; the scraper version just picked the top-scoring candidate.
- **Fix applied:** Added the same similarity gate to `search_album_wikipedia`: iterate sorted candidates, strip parenthetical suffixes, compute `SequenceMatcher.ratio()`, reject below 0.7. "The Modern Lovers" scores 0.52 similarity against "Lovers" — rejected. "Lovers (Anna of the North album)" scores 1.0 — accepted.

### FAILED: Wikipedia album search only runs before album name is known
- **What happened:** The `wikipedia_album` source URL was missing from scraper tester results, and `album_scraper_wiki` artwork came from the wrong Wikipedia page. The cross-fallback Wikipedia album search (Step 2 in `_scrape_with_ai_links`) and the post-cross-fallback album search both ran when `metadata["album"]` was still `None` — the album name "Lovers" was only discovered later via the AI identity fallback and Album RG name-match fallback.
- **Root cause:** Timing: MB resolved the recording but `_find_parent_album` didn't find the album. The album name was set by AI identity (line ~1258) and confirmed by Album RG fallback (line ~1285), both of which run after the Wikipedia album discovery block. No second attempt was made after the album became known.
- **Fix applied:** Added a late-stage Wikipedia album search after the Album RG fallback block. It checks `metadata.get("album")` and `"wikipedia_album" not in metadata["_source_urls"]` — if album is now known but no Wikipedia album page was found yet, it calls `search_wikipedia_album` and scrapes the album page for artwork.

### ANTI-PATTERN: Assuming all data is available at the first opportunity
When a pipeline has multiple stages that each contribute metadata (MB search → AI identity → Album RG fallback), discovery logic that depends on late-resolved fields must either run after all stages complete, or be re-attempted after each stage that could fill the gap. A single early check that finds nothing will silently leave the field empty forever.

---

## Track: Anna of the North — The Dreamer (false positive Wikipedia single source)

### FAILED: `_step_collect_source_links` re-searching Wikipedia without validation (Attempt 1)
- **What was tried:** `_step_collect_source_links` in `stages.py` had a fallback `search_wikipedia(title, artist)` call when `_pipeline_urls.get("wikipedia")` was None. The result was persisted directly as a `wikipedia_single` source without calling `scrape_wikipedia_page()` + `detect_article_mismatch()` to validate the page.
- **Why it failed:** For Anna of the North — The Dreamer, `resolve_metadata_unified()` correctly searched Wikipedia, found `https://en.wikipedia.org/wiki/The_Dreamer`, scraped it, and detected an article mismatch ("unrelated page with no identifiable artist/title"). It discarded the URL — `metadata["_source_urls"]["wikipedia"]` was never set. But `_step_collect_source_links` then re-searched Wikipedia, got the same URL back, and persisted it as `source_type: "single"` without any validation. The scraper tester read `_source_urls` directly (already validated), so it never showed the rejected URL — creating a divergence between scraper tester and import pipeline.
- **Lesson:** When the upstream pipeline has already searched and rejected a Wikipedia URL, downstream stages must not re-search and persist the same rejected result without validation. Either honor the upstream rejection signal, or validate the re-searched result with the same `detect_article_mismatch` check. See also: ANTI-PATTERN "Redundant independent searches when a validated result already exists" (Bastille — Pompeii MMXXIII).
- **Fix applied:** (1) Added `metadata["_wiki_single_rejected"] = True` flag in `resolve_metadata_unified()` (both `scraper/` and `services/` copies) when `detect_article_mismatch` fires. (2) In `_step_collect_source_links`, the `# ── Single ──` fallback now checks `metadata.get("_wiki_single_rejected")` — if True, skips the re-search entirely. If the flag is absent (no prior attempt was made), the fresh `search_wikipedia()` result is validated with `scrape_wikipedia_page()` + `detect_article_mismatch()` before persisting, ensuring parity with `resolve_metadata_unified()` and the scraper tester.

---

## Track: Myles Smith — Nice To Meet You

### FAILED: `_album_is_title_duplicate` unconditionally blocking AI Final Review self-titled album corrections (Attempt 1)
- **What was tried:** In `unified_metadata.py`, the AI Final Review correction loop had an unconditional `_album_is_title_duplicate(proposed, title)` check (line ~938). When the AI proposed `album="Nice To Meet You"` to replace the wrong compilation `"Die Ultimative Chartshow - Hits 2025"`, the check returned True (album == title) and discarded the correction with `"album matches title — discarded"`.
- **Why it failed:** The guard was intended to catch AI hallucinations where the AI invents a self-titled album. But it also blocked *legitimate* corrections where the AI replaces a wrong album (compilation, unrelated release) with the correct self-titled single. The AI review correctly identified that MusicBrainz had resolved to a German compilation instead of the actual single, and proposed "Nice To Meet You" — which happens to equal the song title.
- **Lesson:** The title-duplicate check must consider *what the AI is replacing*, not just *what it's proposing*. When the current album is a different, non-title value (e.g. a compilation), the AI is correcting a misidentification, not hallucinating. Only block when the current album is already empty or title-like (true hallucination scenario).
- **Fix applied:** Added a conditional exemption: when `_album_is_title_duplicate(proposed, title)` fires, check whether the current `metadata["album"]` is a different, non-title value (`_current_album and not _album_is_title_duplicate(_current_album, title)`). If so, allow the correction with log `"allowing self-titled album (replacing different album)"`. Applied to both `services/unified_metadata.py` and `scraper/unified_metadata.py`.

### ANTI-PATTERN: Unconditional title-duplicate blocking without context awareness
The `_album_is_title_duplicate` guard was designed for one failure mode (AI inventing a self-titled album from nothing) but applied unconditionally to ALL album corrections. When the AI is *correcting* a wrong album to the correct self-titled single, this guard actively prevents the fix. Context-aware guards must check the *transition* (wrong → correct), not just the *destination* (matches title).

### FAILED: `extract_album_wiki_url_from_single` only matching "album" in infobox, missing EP references (Attempt 1)
- **What was tried:** `extract_album_wiki_url_from_single()` in `metadata_resolver.py` extracted album URLs from Wikipedia single infoboxes using two methods. Method 1 checked `"from" in text and "album" in text`. Method 2 checked `label in ("album", "from the album", "from")`. Neither matched "EP".
- **Why it failed:** The Wikipedia page for "Nice to Meet You (Myles Smith song)" has infobox text `"from the EP A Minute..."`. Because the code only matched "album" and not "EP", the infobox link to the EP's Wikipedia page was never followed. This prevented album resolution via the Wikipedia cross-link fallback. The companion function `_extract_infobox_album()` (which extracts the album *name* as text) already handled EPs (`"album" in text or " ep " in text or text.endswith(" ep")`), but the URL-extraction function was never updated with the same EP awareness.
- **Lesson:** When two related functions handle the same infobox data (one for text, one for URLs), they must have symmetric matching logic. Adding EP support to one function without the other creates an inconsistency where album names are extracted but album URLs are not.
- **Fix applied:** Updated both Method 1 and Method 2 in `extract_album_wiki_url_from_single()`: Method 1 now checks `"from" in text and ("album" in text or "ep " in text or text.endswith(" ep") or " ep" in text)`. Method 2 now includes `"ep"` and `"from the ep"` in the label set. Applied to both `services/metadata_resolver.py` and `scraper/metadata_resolver.py`.

### ANTI-PATTERN: Asymmetric matching between related extraction functions
Functions that extract different aspects of the same data (text vs URL from the same infobox) must use the same keyword matching. When `_extract_infobox_album()` handles EPs but `extract_album_wiki_url_from_single()` does not, EP-released singles silently lose their album cross-link while still having the album name extracted — a partial failure that's hard to diagnose.

### FAILED: `_find_parent_album` accepting Various Artists compilations not tagged as compilations (Attempt 1)
- **What was tried:** `_find_parent_album()` in `metadata_resolver.py` browses MusicBrainz releases for a recording ID and selects release groups with `primary-type: "Album"` that have no excluded secondary types (compilation, DJ-mix, soundtrack, etc. in `_EXCLUDED_SECONDARY_TYPES`).
- **Why it failed:** MusicBrainz release group `70cc702d-578e-4160-9b2c-f7dc43a93aa0` ("Die Ultimative Chartshow - Hits 2025") is a Various Artists compilation, but its `secondary-type-list` is **empty** — it has no "compilation" tag despite being credited to "Various Artists" (MB ID `89ad4ac3-39f7-470e-963a-56509c546377`). Because `_find_parent_album` only checked secondary types and never checked the artist credit, this VA compilation passed the filter and was selected as the parent album for Myles Smith's "Nice To Meet You". This then set `mb_album_release_group_id`, which triggered the AI Final Review album-override guard, blocking the AI from correcting the album name.
- **Lesson:** MusicBrainz secondary-type tagging is inconsistent. Some compilations are not tagged with the "compilation" secondary type. Always check the artist credit as a secondary guard — if a release is credited to "Various Artists", it's a compilation regardless of secondary types.
- **Fix applied:** Added `'artist-credits'` to the `browse_releases` includes list, and added a check in `_extract_album()` that skips releases where the first artist-credit's `artist.id` matches the "Various Artists" MB ID (`89ad4ac3-39f7-470e-963a-56509c546377`). Applied to both `services/metadata_resolver.py` and `scraper/metadata_resolver.py`.

### IMPROVEMENT: Wikipedia single cover image as poster fallback
- **What was added:** When CoverArtArchive doesn't produce a poster (section 3 of deferred.py), a new section 3a now falls back to the Wikipedia single cover image (`metadata["image_url"]`) before the final video-thumbnail fallback (section 3b). This ensures videos whose singles aren't in CoverArtArchive still get proper artwork from Wikipedia.
- **Applied to:** `pipeline/deferred.py`, `pipeline_lib/deferred.py`, `pipeline_url/deferred.py`.
