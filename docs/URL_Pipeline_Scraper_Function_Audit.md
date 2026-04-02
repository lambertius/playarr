# URL Pipeline — Exhaustive Scraper/Metadata Function Call Audit

Generated: 2026-03-26

## Legend

| Column | Meaning |
|--------|---------|
| **Source Module** | The `pipeline_url` file making the import/call |
| **Imported Function** | The function name |
| **Import Path** | Where it's imported from |
| **Origin** | `shared` = from `app.scraper.*` / `app.services.*` / `app.metadata.*`; `local` = from `pipeline_url`'s own copy |
| **Line** | Import or call-site line number |
| **Call Context** | Where/how the function is invoked and approximate arguments |

---

## File 1: `pipeline_url/stages.py` (Main pipeline orchestrator — ~1400 lines)

### Top-level imports (lines 1–25)
No scraper/metadata imports at module level. All scraper/metadata imports are **deferred** (inside functions).

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `extract_metadata_from_ytdlp` | `app.pipeline_url.services.downloader` | **local** | L97 | L128 | `(download_data["info_dict"])` — Stage B5, extracts metadata from yt-dlp info dict |
| 2 | `resolve_metadata_unified` | `app.scraper.unified_metadata` | **shared** | L419 | L423–436 | `(artist, title, db, source_url, platform_title, channel_name, platform_description, platform_tags, upload_date, filename, duration_seconds, ytdlp_metadata, skip_wikipedia, skip_musicbrainz, skip_ai)` — Stage B6, main metadata resolution |
| 3 | `generate_ai_summary` | `app.pipeline_url.services.ai_summary` | **local** | L450 | L451 | `(metadata["plot"], source_url=canonical)` — Post-metadata AI summary |
| 4 | `detect_version_type` | `app.pipeline_url.matching.version_detector` | **local** | L481 | L496–514 | `(filename, source_title, uploader, description, parsed_artist, parsed_title, ..., existing_library_items, hint_cover, hint_live, hint_alternate, ...)` — Stage B7 |
| 5 | `parse_multi_artist` | `app.scraper.source_validation` | **shared** | L538 | L538, L546 | `(artist)` — Stage B7b duplicate check, splits multi-artist strings |
| 6 | `_init_musicbrainz` | `app.scraper.metadata_resolver` | **shared** | L651 | L652 | `()` — Before MusicBrainz API call to look up album from mb_release_id |
| 7 | `sanitize_album` | `app.scraper.source_validation` | **shared** | L676 | L677 | `(album_title, title=title)` — Sanitize album name before entity resolution |
| 8 | `resolve_artist` | `app.pipeline_url.metadata.resolver` | **local** | L614 | L622–626 | `(artist, mb_artist_id=..., skip_musicbrainz=..., skip_wikipedia=...)` — Stage B12 entity resolution |
| 9 | `resolve_album` | `app.pipeline_url.metadata.resolver` | **local** | L614 | L683–688 | `(artist, album_title, mb_release_id=..., skip_musicbrainz=..., skip_wikipedia=...)` — Stage B12 |
| 10 | `resolve_track` | `app.pipeline_url.metadata.resolver` | **local** | L614 | L698–703 | `(artist, title, mb_recording_id=..., skip_musicbrainz=..., skip_wikipedia=...)` — Stage B12 |
| 11 | `search_imdb_music_video` | `app.scraper.metadata_resolver` | **shared** | L791 | L792 | `(artist, title)` — Stage B13, IMDB source link collection |
| 12 | `search_wikipedia_artist` | `app.scraper.metadata_resolver` | **shared** | L843 | L844 | `(metadata.get("primary_artist") or artist)` — Stage B13 |
| 13 | `extract_artist_wiki_url_from_page` | `app.scraper.metadata_resolver` | **shared** | L846 | L848 | `(wiki_url)` — Fallback to extract artist wiki from single/album infobox |
| 14 | `search_wikipedia_album` | `app.scraper.metadata_resolver` | **shared** | L869 | L872 | `(artist, album_name)` — Stage B13, album wiki source |
| 15 | `extract_album_wiki_url_from_single` | `app.scraper.metadata_resolver` | **shared** | L870 | L874 | `(single_wiki)` — Extract album wiki from single page infobox |
| 16 | `search_wikipedia` | `app.scraper.metadata_resolver` | **shared** | L891 | L892 | `(title, primary_artist)` — Stage B13, single wiki source |
| 17 | `extract_single_wiki_url_from_album` | `app.scraper.metadata_resolver` | **shared** | L905 | L907 | `(_album_url, title)` — Extract single wiki from album tracklist |
| 18 | `download_image` | `app.scraper.metadata_resolver` | **shared** | L929 | L939 | `(image_url, poster_path)` — Stage B11, download poster image |
| 19 | `get_best_thumbnail_url` | `app.pipeline_url.services.downloader` | **local** | L932 | L933 | `(info_dict)` — Stage B11, YouTube thumbnail fallback |
| 20 | `build_folder_name` | `app.pipeline_url.services.file_organizer` | **local** | L930 | L935 | `(artist, title, resolution_label)` — Stage B11, poster filename |
| 21 | `extract_artist_title` | `app.scraper.metadata_resolver` | **shared** | L1357 | L1360 | `(raw_title)` — Parse artist/title from yt-dlp raw title |
| 22 | `clean_title` | `app.scraper.metadata_resolver` | **shared** | L1357 | L1363, L1369, L1371 | `(ytdlp_meta["artist"])`, `(ytdlp_meta["track"])`, `(raw_title)` — Clean parsed strings |
| 23 | `extract_quality_signature` | `app.pipeline_url.services.media_analyzer` | **local** | L291 | L295 | `(file_path)` — Stage B4 |
| 24 | `measure_loudness` | `app.pipeline_url.services.media_analyzer` | **local** | L291 | L300 | `(file_path)` — Stage B4 |
| 25 | `organize_file` | `app.pipeline_url.services.file_organizer` | **local** | L310 | L312 | `(downloaded_file, artist, title, resolution_label, existing_folder, version_type, alternate_version_label)` — Stage B8 |
| 26 | `normalize_video` | `app.pipeline_url.services.normalizer` | **local** | L338 | L341 | `(file_path)` — Stage B9 |
| 27 | `write_nfo_file` | `app.pipeline_url.services.file_organizer` | **local** | L358 | L361–373 | `(folder, artist, title, album, year, genres, plot, source_url, resolution_label, ...)` — Stage B10 |
| 28 | `get_available_formats` | `app.pipeline_url.services.downloader` | **local** | L399, L1055 | L399, L1055 | `(url)` — Stage B5 (yt-dlp metadata), quality format check |
| 29 | `download_video` | `app.pipeline_url.services.downloader` | **local** | L97 | L1161 | `(url, temp_dir, format_spec, progress_callback, cancel_check, container)` — Stage B3 |
| 30 | `identify_provider` | `app.pipeline_url.services.url_utils` | **local** | L1027 | L1029 | `(url)` — Stage B1 |
| 31 | `canonicalize_url` | `app.pipeline_url.services.url_utils` | **local** | L1027 | L1030 | `(provider, video_id)` — Stage B1 |
| 32 | `compare_quality` | `app.pipeline_url.services.media_analyzer` | **local** | L1057 | L1058 | `(current_sig, formats)` — Quality upgrade check |
| 33 | `decide_retry` | `app.services.retry_policy` | **shared** | L1092 | L1108 | `(current_attempt - 1, last_error)` — Download retry logic |
| 34 | `should_auto_retry` | `app.services.retry_policy` | **shared** | L1092 | L1175 | `(last_error)` — Check if error is retryable |
| 35 | `telemetry_store` | `app.services.telemetry` | **shared** | L1093 | Multiple | Download telemetry tracking |

---

## File 2: `pipeline_url/deferred.py` (Deferred enrichment — ~1550 lines)

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `export_video` | `app.metadata.exporters.kodi` | **shared** | L303 | L316–325 | `(db, video_id, artist, title, album, year, genres, plot, source_url, folder_path, resolution_label)` — Kodi NFO export |
| 2 | `export_artist` | `app.metadata.exporters.kodi` | **shared** | L303 | L328 | `(db, item.artist_entity)` — Kodi artist export |
| 3 | `export_album` | `app.metadata.exporters.kodi` | **shared** | L303 | L332 | `(db, item.album_entity)` — Kodi album export |
| 4 | `download_entity_assets` | `app.pipeline_url.metadata.assets` | **local** | L368 | L382 | `(entity_type, entity_id, candidates)` — Download resolver asset candidates |
| 5 | `AssetCandidate` | `app.metadata.providers.base` | **shared** | L369 | L378 | Used to construct candidates from workspace artifact |
| 6 | `parse_multi_artist` | `app.scraper.source_validation` | **shared** | L423 | L427 | `(item.artist or "")` — Extract primary artist for artwork pipeline |
| 7 | `search_musicbrainz` | `app.scraper.metadata_resolver` | **shared** | L441 | L447 | `(item.artist, item.title)` — Step 1c: Complete incomplete MB resolution |
| 8 | `_normalize_for_compare` | `app.scraper.metadata_resolver` | **shared** | L442 | L449–452 | Normalize strings for artist name validation |
| 9 | `_tokens_overlap` | `app.scraper.metadata_resolver` | **shared** | L443 | L453–454 | Token overlap check for fuzzy artist matching |
| 10 | `parse_multi_artist` (as `_pma_c`) | `app.scraper.source_validation` | **shared** | L446 | L448 | `(item.artist or "")` — Parse for MB re-search |
| 11 | `get_or_create_album` (as `_goca_c`) | `app.pipeline_url.metadata.resolver` | **local** | L462 | L468 | `(db, item.artist_entity, _mb_c["album"], resolved=...)` — Create album entity from MB completion |
| 12 | `search_musicbrainz` (as `_mb_1d`) | `app.scraper.metadata_resolver` | **shared** | L503 | L504 | `(item.artist, item.title)` — Step 1d: Correct album entity single IDs |
| 13 | `search_musicbrainz` (as `_mb_1dp`) | `app.scraper.metadata_resolver` | **shared** | L535 | L536 | `(item.artist, item.title)` — Step 1d': Fill missing album RG ID |
| 14 | `process_artist_album_artwork` | `app.pipeline_url.services.artwork_manager` | **local** | L639 | L640–649 | `(artist, album, mb_artist_id, mb_release_id, mb_album_release_id, mb_album_release_group_id, log_callback, overwrite, wiki_album_url)` — Full entity artwork pipeline |
| 15 | `validate_file` | `app.pipeline_url.services.artwork_service` | **local** | L652 | L664 | `(art_path)` — Validate downloaded artwork files |
| 16 | `CachedAsset` (as `_CA2c`) | `app.metadata.models` | **shared** | L694 | L698–713 | DB query for cached assets as fallback |
| 17 | `fetch_caa_artwork` | `app.scraper.artwork_selection` | **shared** | L759 | L760–763 | `(mb_release_id=..., mb_release_group_id=..., mb_album_release_group_id=...)` — Step 3: Poster CAA upgrade |
| 18 | `download_image` | `app.scraper.metadata_resolver` | **shared** | L771 | L783 | `(_video_poster_url, poster_dst)` — Download CAA poster image |
| 19 | `guarded_copy` | `app.pipeline_url.services.artwork_service` | **local** | L772 | L786 | `(poster_dst, thumb_dst)` — Safe artwork file copy |
| 20 | `validate_file` | `app.pipeline_url.services.artwork_service` | **local** | L772 | L787 | `(poster_dst)` — Validate poster |
| 21 | `build_folder_name` | `app.pipeline_url.services.file_organizer` | **local** | L862 | L863 | `(...)` — For poster fallback naming |
| 22 | `generate_preview` | `app.pipeline_url.services.preview_generator` | **local** | L254 | L264 | `(file_path, video_id=video_id)` — Deferred preview generation |
| 23 | `analyze_scenes` | `app.pipeline_url.ai.scene_analysis` | **local** | L273 | L275 | `(db, video_id)` — Scene detection + thumbnail scoring |
| 24 | `enrich_video_metadata` | `app.pipeline_url.ai.metadata_service` | **local** | L1451 | L1453 | `(db, video_id, auto_apply=True)` — AI metadata enrichment |
| 25 | `resolve_video` | `app.pipeline_url.matching.resolver` | **local** | L1491 | L1492 | `(db, video_id)` — Matching resolution |
| 26 | `search_wikipedia_artist` | `app.scraper.metadata_resolver` | **shared** | L994 | L995 | `(primary_artist)` — Re-resolve sources after AI correction |
| 27 | `search_wikipedia` | `app.scraper.metadata_resolver` | **shared** | L1000 | L1001 | `(title, primary_artist)` — Re-resolve single wiki |
| 28 | `search_wikipedia_album` | `app.scraper.metadata_resolver` | **shared** | L1007 | L1008 | `(primary_artist, album)` — Re-resolve album wiki |
| 29 | `extract_wiki_infobox_links` | `app.scraper.metadata_resolver` | **shared** | L1016 | L1023, L1052 | `(_wiki_single_url)`, `(_wiki_album_url)` — Wikipedia cross-link confirmation |
| 30 | `extract_single_wiki_url_from_album` | `app.scraper.metadata_resolver` | **shared** | L1100 | L1101 | `(_wiki_album_url, title)` — Album tracklist single fallback |
| 31 | `search_imdb_music_video` | `app.scraper.metadata_resolver` | **shared** | L1150 | L1151 | `(primary_artist, title)` — Re-resolve IMDB |
| 32 | `search_musicbrainz` | `app.scraper.metadata_resolver` | **shared** | L1173 | L1178 | `(artist, title)` — Re-resolve MB after AI correction |
| 33 | `_normalize_for_compare` | `app.scraper.metadata_resolver` | **shared** | L1174 | L1183–1185 | Normalize for MB artist match validation |
| 34 | `_tokens_overlap` | `app.scraper.metadata_resolver` | **shared** | L1174 | L1188–1189 | Token overlap for fuzzy MB match |
| 35 | `parse_multi_artist` (as `_pma_mb`) | `app.scraper.source_validation` | **shared** | L1176 | L1180 | `(artist)` — Parse for MB re-search |
| 36 | `build_folder_name` | `app.pipeline_url.services.file_organizer` | **local** | L1306 | L1311 | `(item.artist, item.title, resolution, ...)` — Re-organize file after AI correction |
| 37 | `sanitize_filename` | `app.pipeline_url.services.file_organizer` | **local** | L1306 | — | Module imported but only `build_folder_name` used directly |

---

## File 3: `pipeline_url/services/artwork_manager.py` (Artwork Manager — ~460 lines)

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `get_artist_artwork` | `app.scraper.artist_album_scraper` | **shared** | L324 | L354 | `(artist, mb_artist_id=...)` — Default artist artwork fetcher |
| 2 | `get_album_artwork` | `app.scraper.artist_album_scraper` | **shared** | L324 | L398 | `(album, artist, wiki_url=wiki_album_url)` or `(album, artist)` — Default album artwork |
| 3 | `get_artist_artwork_wikipedia` | `app.scraper.artist_album_scraper` | **shared** | L325 | L342 | Via `_get_artist` when `source=="wikipedia"` |
| 4 | `get_artist_artwork_musicbrainz` | `app.scraper.artist_album_scraper` | **shared** | L326 | L344 | Via `_get_artist` when `source=="musicbrainz"` |
| 5 | `get_album_artwork_wikipedia` | `app.scraper.artist_album_scraper` | **shared** | L326 | L343 | Via `_get_album` when `source=="wikipedia"` |
| 6 | `get_album_artwork_musicbrainz` | `app.scraper.artist_album_scraper` | **shared** | L326 | L345 | Via `_get_album` when `source=="musicbrainz"` |
| 7 | `_fetch_front_cover_by_release_group` | `app.metadata.providers.coverartarchive` | **shared** | L410 | L411 | `(mb_album_release_group_id)` — CAA fallback for album cover |
| 8 | `_fetch_front_cover` | `app.metadata.providers.coverartarchive` | **shared** | L415 | L416 | `(mb_album_release_id)` — CAA fallback by release ID |
| 9 | `download_and_validate` | `app.pipeline_url.services.artwork_service` | **local** | L115 | L122 | `(url, dest_path, max_width, max_height, overwrite)` — Validate-and-save images |

---

## File 4: `pipeline_url/db_apply.py` (DB apply — ~430 lines)

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `get_or_create_artist` | `app.pipeline_url.metadata.resolver` | **local** | L45 | L201 | `(db, artist_name, resolved=...)` — Entity upsert |
| 2 | `get_or_create_album` | `app.pipeline_url.metadata.resolver` | **local** | L45 | L211 | `(db, artist_entity, album_title, resolved=...)` |
| 3 | `get_or_create_track` | `app.pipeline_url.metadata.resolver` | **local** | L45 | L221 | `(db, artist_entity, album_entity, track_title, resolved=...)` |
| 4 | `get_or_create_canonical_track` | `app.pipeline_url.services.canonical_track` | **local** | L49 | L231 | `(db, **ct_params)` |
| 5 | `link_video_to_canonical_track` | `app.pipeline_url.services.canonical_track` | **local** | L49 | L255 | `(db, video_item, canonical_track)` |
| 6 | `save_revision` | `app.metadata.revisions` | **shared** | L48 | L205, L215 | `(db, "artist"/"album", entity_id, "auto_import", "resolver")` |
| 7 | `parse_multi_artist` | `app.scraper.source_validation` | **shared** | L68 | L69, L74 | `(v.get("artist", ""))` — TOCTOU duplicate check |
| 8 | `capitalize_genre` | `app.scraper.metadata_resolver` | **shared** | L363 | L364 | `(genre_name)` — Genre normalization |

---

## File 5: `pipeline_url/metadata/resolver.py` (Entity resolver — ~500+ lines)

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `WikipediaProvider` | `app.pipeline_url.metadata.providers.wikipedia` | **local** | L39 | L55 | Instantiated as provider |
| 2 | `MusicBrainzProvider` | `app.pipeline_url.metadata.providers.musicbrainz` | **local** | L40 | L54 | Instantiated as provider |
| 3 | `CoverArtArchiveProvider` | `app.metadata.providers.coverartarchive` | **shared** | L41 | L56 | Instantiated as provider |
| 4 | `MetadataProvider` | `app.metadata.providers.base` | **shared** | L38 | — | Base class for providers |
| 5 | `ProviderResult` | `app.metadata.providers.base` | **shared** | L38 | — | Provider result type |
| 6 | `AssetCandidate` | `app.metadata.providers.base` | **shared** | L38 | — | Asset candidate type |
| 7 | `ArtistEntity`, `AlbumEntity`, `TrackEntity`, `Genre` | `app.metadata.models` | **shared** | L35–37 | Throughout | ORM models for entity create/merge |
| 8 | `_get_or_create_genre` (from `app.tasks`) | `app.tasks` | **shared** | ~L466 | ~L475 | `(genre_name)` — Genre creation in entity upsert |

---

## File 6: `pipeline_url/metadata/providers/wikipedia.py` (Wikipedia provider — ~400 lines)

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `_WIKI_USER_AGENT` | `app.scraper.metadata_resolver` | **shared** | L23 | Multiple | User agent constant for Wikipedia requests |
| 2 | `_wikipedia_search_api` | `app.scraper.metadata_resolver` | **shared** | L24 | ~L150 | Wikipedia search API function |
| 3 | `capitalize_genre` | `app.scraper.metadata_resolver` | **shared** | L25 | Multiple | Genre normalization |
| 4 | `_build_wikipedia_url` | `app.scraper.metadata_resolver` | **shared** | L177 | L178 | `(best['title'])` — Construct Wikipedia URL from page title |
| 5 | `_extract_infobox_artist` | `app.scraper.metadata_resolver` | **shared** | L375 | L379 | `(infobox)` — Extract artist from Wikipedia infobox |
| 6 | `_extract_infobox_title` | `app.scraper.metadata_resolver` | **shared** | L375 | L380 | `(infobox)` — Extract title from infobox |
| 7 | `detect_article_mismatch` | `app.scraper.metadata_resolver` | **shared** | L377 | L383 | `(scraped_data, artist, title)` — Validate Wikipedia article matches expected track |

---

## File 7: `pipeline_url/metadata/providers/musicbrainz.py` (MusicBrainz provider — ~400 lines)

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `_init_musicbrainz` | `app.scraper.metadata_resolver` | **shared** | L21 | Multiple | Initialize musicbrainzngs client |
| 2 | `capitalize_genre` | `app.scraper.metadata_resolver` | **shared** | L21 | L44 | Genre tag normalization |
| 3 | `_pick_best_release` | `app.scraper.metadata_resolver` | **shared** | L22 | Multiple | Select best MusicBrainz release from candidates |
| 4 | `_search_single_release_group` | `app.scraper.metadata_resolver` | **shared** | L23 | Multiple | Search for single release group in MB |
| 5 | `_resolve_commons_url` | `app.scraper.artist_album_scraper` | **shared** | L25 | Multiple | Resolve Wikimedia Commons URLs to direct image links |

---

## File 8: `pipeline_url/metadata/assets.py` (Asset cache manager — ~250 lines)

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `download_and_validate` | `app.pipeline_url.services.artwork_service` | **local** | L44 | L153 | `(candidate.url, dest_path, max_width, max_height, provider, overwrite)` — Validated download |
| 2 | `validate_file` | `app.pipeline_url.services.artwork_service` | **local** | L45 | L130 | `(existing.local_cache_path)` — Validate cached files |
| 3 | `validate_existing_cached_asset` | `app.pipeline_url.services.artwork_service` | **local** | L46 | — | Imported but usage is in the API |
| 4 | `invalidate_cached_asset` | `app.pipeline_url.services.artwork_service` | **local** | L47 | — | Imported for invalidation flow |
| 5 | `CachedAsset` | `app.metadata.models` | **shared** | L41 | Multiple | ORM model for asset cache records |
| 6 | `AssetCandidate` | `app.metadata.providers.base` | **shared** | L42 | — | Type for asset candidates |

---

## File 9: `pipeline_url/services/canonical_track.py` (~200 lines)

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `capitalize_genre` | `app.scraper.metadata_resolver` | **shared** | L33 | L34 | `(genre_name)` — Genre normalization |
| 2 | `make_comparison_key` | `app.pipeline_url.matching.normalization` | **local** | L26 | Multiple | Normalize artist+title for matching |

---

## File 10: `pipeline_url/ai/metadata_service.py` (~900+ lines)

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `capitalize_genre` | `app.scraper.metadata_resolver` | **shared** | L531, L663, L903 | L532, L664, L904 | Genre normalization in AI enrichment phases |
| 2 | `save_revision` | `app.metadata.revisions` | **shared** | L555, L686 | L556, L687 | `(db, "video", video_id, ...)` — Save metadata snapshots |

---

## File 11: `pipeline_url/services/artwork_service.py` (~800+ lines)

### Scraper/Metadata Function Calls

| # | Function | Import Path | Origin | Import Line | Call Line(s) | Arguments / Context |
|---|----------|-------------|--------|-------------|-------------|---------------------|
| 1 | `CachedAsset` | `app.metadata.models` | **shared** | L683, L760 | Multiple | ORM model for façade API |
| 2 | `AssetCandidate` | `app.metadata.providers.base` | **shared** | L761 | — | Type reference |

**Note:** `artwork_service.py` is a **local** module that does NOT delegate to `app.scraper.*` for downloads — it has its own `httpx.get` + PIL-based image processing pipeline.

---

## Summary: Local vs. Shared Function Inventory

### SHARED functions (imported from `app.scraper.*` / `app.services.*` / `app.metadata.*`)

| Function | Module | Called from (pipeline_url files) |
|----------|--------|--------------------------------|
| `resolve_metadata_unified` | `app.scraper.unified_metadata` | stages.py |
| `extract_artist_title` | `app.scraper.metadata_resolver` | stages.py |
| `clean_title` | `app.scraper.metadata_resolver` | stages.py |
| `download_image` | `app.scraper.metadata_resolver` | stages.py, deferred.py |
| `capitalize_genre` | `app.scraper.metadata_resolver` | db_apply.py, canonical_track.py, ai/metadata_service.py, wikipedia.py, musicbrainz.py |
| `_init_musicbrainz` | `app.scraper.metadata_resolver` | stages.py, musicbrainz.py |
| `search_musicbrainz` | `app.scraper.metadata_resolver` | deferred.py (×4 call sites) |
| `_normalize_for_compare` | `app.scraper.metadata_resolver` | deferred.py (×2) |
| `_tokens_overlap` | `app.scraper.metadata_resolver` | deferred.py (×2) |
| `_pick_best_release` | `app.scraper.metadata_resolver` | musicbrainz.py |
| `_search_single_release_group` | `app.scraper.metadata_resolver` | musicbrainz.py |
| `_WIKI_USER_AGENT` | `app.scraper.metadata_resolver` | wikipedia.py |
| `_wikipedia_search_api` | `app.scraper.metadata_resolver` | wikipedia.py |
| `_build_wikipedia_url` | `app.scraper.metadata_resolver` | wikipedia.py |
| `_extract_infobox_artist` | `app.scraper.metadata_resolver` | wikipedia.py |
| `_extract_infobox_title` | `app.scraper.metadata_resolver` | wikipedia.py |
| `detect_article_mismatch` | `app.scraper.metadata_resolver` | wikipedia.py |
| `search_wikipedia` | `app.scraper.metadata_resolver` | stages.py, deferred.py |
| `search_wikipedia_artist` | `app.scraper.metadata_resolver` | stages.py, deferred.py |
| `search_wikipedia_album` | `app.scraper.metadata_resolver` | stages.py, deferred.py |
| `extract_artist_wiki_url_from_page` | `app.scraper.metadata_resolver` | stages.py |
| `extract_album_wiki_url_from_single` | `app.scraper.metadata_resolver` | stages.py |
| `extract_single_wiki_url_from_album` | `app.scraper.metadata_resolver` | stages.py, deferred.py |
| `extract_wiki_infobox_links` | `app.scraper.metadata_resolver` | deferred.py |
| `search_imdb_music_video` | `app.scraper.metadata_resolver` | stages.py, deferred.py |
| `parse_multi_artist` | `app.scraper.source_validation` | stages.py, deferred.py, db_apply.py |
| `sanitize_album` | `app.scraper.source_validation` | stages.py |
| `fetch_caa_artwork` | `app.scraper.artwork_selection` | deferred.py |
| `get_artist_artwork` | `app.scraper.artist_album_scraper` | artwork_manager.py |
| `get_album_artwork` | `app.scraper.artist_album_scraper` | artwork_manager.py |
| `get_artist_artwork_wikipedia` | `app.scraper.artist_album_scraper` | artwork_manager.py |
| `get_artist_artwork_musicbrainz` | `app.scraper.artist_album_scraper` | artwork_manager.py |
| `get_album_artwork_wikipedia` | `app.scraper.artist_album_scraper` | artwork_manager.py |
| `get_album_artwork_musicbrainz` | `app.scraper.artist_album_scraper` | artwork_manager.py |
| `_resolve_commons_url` | `app.scraper.artist_album_scraper` | musicbrainz.py |
| `_fetch_front_cover_by_release_group` | `app.metadata.providers.coverartarchive` | artwork_manager.py |
| `_fetch_front_cover` | `app.metadata.providers.coverartarchive` | artwork_manager.py |
| `CoverArtArchiveProvider` | `app.metadata.providers.coverartarchive` | resolver.py |
| `save_revision` | `app.metadata.revisions` | db_apply.py, ai/metadata_service.py |
| `decide_retry`, `should_auto_retry` | `app.services.retry_policy` | stages.py |
| `telemetry_store` | `app.services.telemetry` | stages.py |
| `export_video`, `export_artist`, `export_album` | `app.metadata.exporters.kodi` | deferred.py |

### LOCAL copies (pipeline_url's own modules — NOT from `app.scraper.*`)

| Local Module | Key Functions | Has equivalent in `app.scraper.*`? | Status |
|-------------|---------------|-------------------------------------|--------|
| `pipeline_url/services/downloader.py` | `download_video`, `get_available_formats`, `extract_metadata_from_ytdlp`, `get_best_thumbnail_url`, `select_best_format`, `extract_playlist_entries` | Yes: `app.services.downloader` | **LOCAL COPY** — "AUTO-SEPARATED" from shared version |
| `pipeline_url/services/artwork_manager.py` | `process_artist_album_artwork`, `ensure_artist_artwork`, `ensure_album_artwork`, `download_and_save` | Yes: `app.services.artwork_manager` | **LOCAL COPY** — delegates to shared `app.scraper.artist_album_scraper` for artwork data |
| `pipeline_url/services/artwork_service.py` | `download_and_validate`, `validate_file`, `guarded_copy`, `normalize_artwork_url` | Yes: `app.services.artwork_service` | **LOCAL COPY** — owns httpx fetching + PIL processing |
| `pipeline_url/services/media_analyzer.py` | `extract_quality_signature`, `measure_loudness`, `compare_quality`, `probe_file` | Yes: `app.services.media_analyzer` | **LOCAL COPY** |
| `pipeline_url/services/file_organizer.py` | `organize_file`, `build_folder_name`, `write_nfo_file`, `sanitize_filename` | Yes: `app.services.file_organizer` | **LOCAL COPY** |
| `pipeline_url/services/normalizer.py` | `normalize_video` | Yes: `app.services.normalizer` | **LOCAL COPY** |
| `pipeline_url/services/preview_generator.py` | `generate_preview` | Yes: `app.services.preview_generator` | **LOCAL COPY** |
| `pipeline_url/services/url_utils.py` | `identify_provider`, `canonicalize_url`, `is_playlist_url` | Yes: `app.services.url_utils` | **LOCAL COPY** |
| `pipeline_url/services/ai_summary.py` | `generate_ai_summary` | Yes: `app.services.ai_summary` | **LOCAL COPY** |
| `pipeline_url/services/canonical_track.py` | `get_or_create_canonical_track`, `link_video_to_canonical_track`, `find_canonical_track` | Yes: `app.services.canonical_track` | **LOCAL COPY** |
| `pipeline_url/services/duplicate_detection.py` | `detect_duplicates` | Yes: `app.services.duplicate_detection` | **LOCAL COPY** |
| `pipeline_url/metadata/resolver.py` | `resolve_artist`, `resolve_album`, `resolve_track`, `get_or_create_artist`, `get_or_create_album`, `get_or_create_track` | Yes: `app.metadata.resolver` | **LOCAL COPY** |
| `pipeline_url/metadata/assets.py` | `download_asset`, `download_entity_assets` | Yes: `app.metadata.assets` | **LOCAL COPY** |
| `pipeline_url/metadata/providers/wikipedia.py` | `WikipediaProvider` class | Yes: `app.metadata.providers.wikipedia` | **LOCAL COPY** — imports heavily from shared `app.scraper.metadata_resolver` |
| `pipeline_url/metadata/providers/musicbrainz.py` | `MusicBrainzProvider` class | Yes: `app.metadata.providers.musicbrainz` | **LOCAL COPY** — imports from shared `app.scraper.metadata_resolver` + `artist_album_scraper` |
| `pipeline_url/ai/metadata_service.py` | `enrich_video_metadata` | Yes: `app.ai.metadata_service` (if it exists) | **LOCAL COPY** |
| `pipeline_url/ai/scene_analysis.py` | `analyze_scenes` | Yes: `app.ai.scene_analysis` (if it exists) | **LOCAL COPY** |

---

## Key Divergence Points

### 1. `artwork_manager.py` → Delegates to SHARED scraper
The local `process_artist_album_artwork` is a **LOCAL orchestrator** but it **delegates** the actual data-fetching to shared functions:
- `app.scraper.artist_album_scraper.get_artist_artwork()`
- `app.scraper.artist_album_scraper.get_album_artwork()`
- `app.metadata.providers.coverartarchive._fetch_front_cover*()` (fallback)

### 2. `stages.py` → Calls SHARED `resolve_metadata_unified` directly
The central metadata resolution (Stage B6) calls `app.scraper.unified_metadata.resolve_metadata_unified` — the **same shared function** the scraper tester uses.

### 3. Entity resolution uses LOCAL providers but SHARED building blocks
`pipeline_url/metadata/resolver.py` uses local `WikipediaProvider` + `MusicBrainzProvider` classes, but those classes internally import and use shared functions from `app.scraper.metadata_resolver`.

### 4. `deferred.py` → Heavy use of SHARED scraper functions
The deferred phase (`_deferred_entity_artwork`, `_re_resolve_sources`) imports 15+ functions directly from `app.scraper.metadata_resolver` and `app.scraper.source_validation`.

### 5. `fetch_caa_artwork` is SHARED
The poster upgrade in deferred.py (Step 3) uses `app.scraper.artwork_selection.fetch_caa_artwork` — the **exact same function** the scraper tester uses.

### 6. All services are "AUTO-SEPARATED" copies
Every file in `pipeline_url/services/` bears the header:
```
# AUTO-SEPARATED from services/<name>.py for pipeline_url pipeline
# This file is independent — changes here do NOT affect the other pipeline.
```
This means they started as copies of `app/services/*.py` and may have diverged over time. The same pattern applies to `pipeline_url/metadata/` and `pipeline_url/ai/` directories.
