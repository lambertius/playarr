# Artwork Pipeline — Developer Contract

> **Single source of truth:** `backend/app/services/artwork_service.py`

This document describes the architecture and rules governing all artwork
(images, posters, thumbnails) in Playarr. **All contributors must read this
before touching code that downloads, copies, validates, or serves image files.**

---

## 1. Golden Rule

**Every artwork byte that enters Playarr — whether from a remote URL, a user
upload, or a file copy — passes through `artwork_service`.**

No other module may:

- Fetch images via `httpx` / `requests` / `urllib`
- Write image files via `PIL.Image.save()`
- Copy artwork files via `shutil.copy` / `shutil.copy2`

If you need to do any of those things, use the facade API below.

---

## 2. Public Facade API

| Function | Use Case |
|---|---|
| `fetch_and_store_entity_asset(url, entity_type, entity_id, kind)` | Download artwork for an artist/album/video entity into the asset cache |
| `fetch_and_store_video_asset(url, video_folder, folder_name, asset_type)` | Download artwork into a video's Kodi-layout folder |
| `validate_and_store_upload(file_bytes, dest_path)` | Validate and persist a user-uploaded image |
| `derive_export_asset_from_cache(cache_path, dest_path)` | Copy a validated cached asset to an export/Kodi path |
| `guarded_copy(src_path, dest_path)` | Copy any artwork file with source validation |
| `download_and_validate(url, dest_path, ...)` | Low-level: download + magic-byte + PIL verify + resize |
| `validate_file(path)` → `ValidationResult` | Validate an existing file on disk |
| `resize_and_convert(path, max_w, max_h)` → `ValidationResult` | Resize/convert in-place |

### Helper / Bookkeeping

| Function | Use Case |
|---|---|
| `invalidate_cached_asset(asset, reason)` | Mark a CachedAsset as invalid |
| `invalidate_media_asset(asset, reason)` | Mark a MediaAsset as invalid |
| `validate_existing_cached_asset(asset)` | Re-validate a CachedAsset on disk |
| `validate_existing_media_asset(asset)` | Re-validate a MediaAsset on disk |
| `delete_video_artwork(video_id, db)` | Delete all artwork for a video |
| `delete_entity_cached_assets(entity_type, entity_id, db)` | Delete all cached assets for an entity |
| `repair_cached_assets(db)` → `RepairReport` | Scan + repair all CachedAssets |
| `repair_media_assets(db)` → `RepairReport` | Scan + repair all MediaAssets |
| `check_media_asset_provenance(kwargs)` | Validate a dict has required provenance fields |

---

## 3. Validation Chain

Every image goes through this chain:

1. **Magic bytes** — first 16 bytes must match JPEG/PNG/WebP/GIF signatures
2. **PIL verify** — `Image.open(path).verify()` must succeed without exception
3. **Resize/convert** — image is resized to max dimensions and saved as JPEG
4. **Hash** — SHA-256 of the final file is computed for integrity tracking

If any step fails, the file is deleted and an error result is returned.

---

## 4. Provenance Fields

Every `MediaAsset` record **must** have these fields populated:

| Field | Description |
|---|---|
| `provenance` | Origin: `"import"`, `"rescan"`, `"redownload"`, `"wikipedia_scrape"`, `"artwork_pipeline"`, `"ai_scene_analysis"`, `"user_upload"` |
| `status` | `"valid"`, `"invalid"`, or `"missing"` |
| `width` / `height` | Image dimensions (pixels) |
| `file_size_bytes` | File size on disk |
| `file_hash` | SHA-256 of the file |
| `last_validated_at` | UTC timestamp of last validation |

The ORM `@validates("provenance")` hook on `MediaAsset` warns if provenance is
not set. The `@validates("status")` hook on `CachedAsset` warns on unrecognized
status values.

---

## 5. Runtime Enforcement

### ORM Validators
- `MediaAsset.provenance` — soft warning if not set
- `CachedAsset.status` — soft warning if not recognized

### Serving Guards
- `GET /api/assets/{id}` returns 404 if `status != "valid"`
- `GET /api/poster/{video_id}` filters by `status == "valid"`
- `GET /api/video-thumb/{video_id}` filters by `status == "valid"`

### Upload Guard
- `POST /api/upload-artwork/{video_id}` validates through
  `artwork_service.validate_and_store_upload()` — magic bytes, PIL verify,
  resize — before persisting.

---

## 6. Static Enforcement Tests

`tests/test_artwork_enforcement.py` contains compile-time scans that fail CI
if any bypass path is introduced:

| Test | What it catches |
|---|---|
| `test_no_httpx_outside_allowlist` | Raw `httpx.get()` calls outside `artwork_service.py` |
| `test_no_pil_save_outside_allowlist` | Direct `PIL.Image.save()` outside `artwork_service.py` |
| `test_no_shutil_copy_for_artwork` | `shutil.copy/copy2` for artwork filenames outside `artwork_service.py` |
| `test_provenance_warning_on_missing` | ORM validator fires on missing provenance |
| `test_deprecation_warning` | `download_image()` raises `DeprecationWarning` |
| `test_public_api_exists` | All facade functions are importable |

---

## 7. Deprecated Functions

| Function | Replacement |
|---|---|
| `metadata_resolver.download_image()` | `artwork_service.download_and_validate()` or facade functions |

`download_image()` emits a `DeprecationWarning` and delegates to
`artwork_service.download_and_validate()` internally. It will be removed in a
future version.

---

## 8. Adding New Artwork Paths

If you need to introduce a new way to acquire artwork:

1. **Do not** call `httpx`, `PIL.save()`, or `shutil.copy2` directly
2. Use the appropriate facade function from Section 2
3. Populate all provenance fields (Section 4) on the `MediaAsset` record
4. Run `pytest tests/test_artwork_enforcement.py` to verify compliance
5. If you need a new allowlist entry, add it to the test and document why

---

## 9. Architecture Diagram

```
User Upload ──→ validate_and_store_upload() ──→ disk
                                                 ↓
Remote URL  ──→ fetch_and_store_*()          ──→ download_and_validate()
                                                 ├─→ magic bytes check
                                                 ├─→ PIL verify
                                                 ├─→ resize_and_convert()
                                                 └─→ disk (validated)
                                                 ↓
Cache Copy  ──→ derive_export_asset_from_cache() ──→ validate_file() → shutil.copy2
                                                 ↓
Any Copy    ──→ guarded_copy()               ──→ validate_file() → shutil.copy2
                                                 ↓
                                            MediaAsset DB record
                                            (status, provenance, hash, dimensions)
```
