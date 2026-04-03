"""
⚠️  LEGACY — Superseded by pipeline_url/ and pipeline_lib/. Do not modify.

Mutation plan schema and builder.

A mutation plan is a plain dict describing ALL database writes needed for an import.
It is built from workspace artifacts during Stage B and consumed deterministically
by the apply executor in Stage C.  The plan is also written to workspace as
mutation_plan.json for debugging, auditing, and retry.
"""
from typing import Any, Dict, List, Optional


def empty_plan(job_id: int, import_type: str = "library", mode: str = "simple") -> dict:
    """Return a skeleton mutation plan."""
    return {
        "job_id": job_id,
        "import_type": import_type,      # "library" | "url"
        "mode": mode,                     # "simple" | "advanced"

        # VideoItem fields
        "video": {
            "action": "create",           # "create" | "update"
            "existing_id": None,          # int for updates
        },

        # QualitySignature fields (dict matching model columns)
        "quality_signature": None,

        # Source records to upsert: list of dicts with provider, source_video_id, etc.
        "sources": [],

        # Genre names (strings)
        "genres": [],

        # Entity resolution payloads for get_or_create_* calls
        "entities": {
            "artist": None,      # {"name": str, "resolved": dict}
            "album": None,       # {"title": str, "resolved": dict}
            "track": None,       # {"title": str, "resolved": dict}
            "canonical_track": None,  # kwargs for get_or_create_canonical_track
        },

        # Media asset records: list of {"asset_type", "file_path", "source_url", "provenance"}
        "media_assets": [],

        # Metadata snapshot reason string (e.g. "library_import", "url_import")
        "snapshot_reason": None,

        # Processing state flags: step_name → method
        "processing_flags": {},

        # Normalization result (optional)
        "normalization": None,

        # Deferred tasks to dispatch after apply
        "deferred_tasks": [],

        # Extra context for the apply (version, review, etc.)
        "version_type": "normal",
        "alternate_version_label": "",
        "original_artist": None,
        "original_title": None,
        "review_status": "none",
        "review_reason": None,
    }


def build_plan_from_workspace(ws) -> dict:
    """Assemble a mutation plan by reading all workspace artifacts.

    Args:
        ws: ImportWorkspace instance with completed Stage B artifacts.

    Returns:
        A complete mutation plan dict ready for apply.
    """
    input_data = ws.read_artifact("input") or {}
    job_id = ws.job_id
    import_type = input_data.get("import_type", "library")
    opts = input_data.get("options") or {}
    mode = input_data.get("mode") or opts.get("mode", "simple")

    plan = empty_plan(job_id, import_type, mode)

    # ── Identity + file organization ─────────────────────────────────
    identity = ws.read_artifact("parsed_identity") or {}
    organized = ws.read_artifact("organized") or {}
    ffprobe = ws.read_artifact("ffprobe") or {}
    loudness = ws.read_artifact("loudness") or {}
    metadata = ws.read_artifact("scraper_results") or {}
    version = ws.read_artifact("version_detection") or {}
    source_links = ws.read_artifact("source_links") or {}
    entity_res = ws.read_artifact("entity_resolution") or {}
    artwork = ws.read_artifact("artwork_results") or {}
    artwork_source = ws.read_artifact("artwork_source") or {}
    youtube_match = ws.read_artifact("youtube_match") or {}
    normalized = ws.read_artifact("normalized") or {}

    # Determine final artist/title (metadata may override identity)
    final_artist = metadata.get("artist") or identity.get("artist") or "Unknown Artist"
    final_title = metadata.get("title") or identity.get("title") or "Unknown Title"

    # ── Video fields ─────────────────────────────────────────────────
    existing_id = input_data.get("existing_video_id")
    plan["video"]["action"] = "update" if existing_id else "create"
    plan["video"]["existing_id"] = existing_id
    plan["video"]["artist"] = final_artist
    plan["video"]["title"] = final_title
    plan["video"]["album"] = metadata.get("album") or identity.get("album") or ""
    plan["video"]["year"] = metadata.get("year") or identity.get("year")
    plan["video"]["plot"] = metadata.get("plot") or identity.get("plot") or ""
    plan["video"]["folder_path"] = organized.get("new_folder")
    plan["video"]["file_path"] = organized.get("new_file")
    plan["video"]["file_size_bytes"] = organized.get("file_size_bytes")
    plan["video"]["resolution_label"] = organized.get("resolution_label")
    plan["video"]["song_rating"] = 3
    plan["video"]["video_rating"] = 3
    plan["video"]["mb_artist_id"] = metadata.get("mb_artist_id")
    plan["video"]["mb_recording_id"] = metadata.get("mb_recording_id")
    plan["video"]["mb_release_id"] = metadata.get("mb_release_id")
    plan["video"]["processing_state"] = {}

    # ── Version detection ────────────────────────────────────────────
    plan["version_type"] = version.get("version_type", "normal")
    plan["alternate_version_label"] = version.get("alternate_version_label", "")
    plan["original_artist"] = version.get("original_artist")
    plan["original_title"] = version.get("original_title")
    plan["review_status"] = version.get("review_status", "none")
    plan["review_reason"] = version.get("review_reason")

    # ── Quality signature ────────────────────────────────────────────
    if ffprobe:
        sig = dict(ffprobe)
        sig["loudness_lufs"] = loudness.get("lufs") or sig.get("loudness_lufs")
        if normalized.get("after_lufs") is not None:
            sig["loudness_lufs"] = normalized["after_lufs"]
        plan["quality_signature"] = sig

    # ── Normalization ────────────────────────────────────────────────
    if normalized:
        plan["normalization"] = normalized

    # ── Compatibility / normalization failure → review ────────────────
    if ws.get_stage_status("normalize_audio") == "failed" and plan["review_status"] == "none":
        plan["review_status"] = "needs_human_review"
        plan["review_reason"] = "Audio normalization failed (possible codec incompatibility)"

    # ── AI failure → review ──────────────────────────────────────────
    _ai_failures = ws.read_artifact("ai_failures") or []
    if _ai_failures and plan["review_status"] == "none":
        plan["review_status"] = "needs_human_review"
        _codes = ", ".join(f.get("code", "unknown") for f in _ai_failures)
        plan["review_reason"] = f"AI enhancement failed ({_codes})"

    # ── Genres ────────────────────────────────────────────────────────
    plan["genres"] = metadata.get("genres") or identity.get("genres") or []

    # ── Sources ──────────────────────────────────────────────────────
    sources = []
    # NFO source URL
    nfo_source = identity.get("source_url")
    if nfo_source:
        sources.append({
            "provider": _guess_provider(nfo_source),
            "source_video_id": _extract_source_id(nfo_source),
            "original_url": nfo_source,
            "canonical_url": nfo_source,
            "source_type": "video",
            "provenance": "import",
        })

    # YouTube match (library import)
    if youtube_match.get("url"):
        sources.append({
            "provider": "youtube",
            "source_video_id": youtube_match.get("video_id", ""),
            "original_url": youtube_match["url"],
            "canonical_url": youtube_match.get("canonical_url") or youtube_match["url"],
            "source_type": "video",
            "provenance": "matched",
            "channel_name": youtube_match.get("channel"),
            "platform_title": youtube_match.get("title"),
        })

    # URL import primary source
    if import_type == "url" and input_data.get("canonical_url"):
        sources.append({
            "provider": input_data.get("provider", "youtube"),
            "source_video_id": input_data.get("provider_video_id", ""),
            "original_url": input_data.get("url", ""),
            "canonical_url": input_data["canonical_url"],
            "source_type": "video",
            "provenance": "import",
            "channel_name": input_data.get("channel_name"),
            "platform_title": input_data.get("platform_title"),
            "platform_description": input_data.get("platform_description"),
            "platform_tags": input_data.get("platform_tags"),
            "upload_date": input_data.get("upload_date"),
        })

    # Source links from Stage B collection (IMDB, Wikipedia, MusicBrainz)
    for key, link in source_links.items():
        if link and isinstance(link, dict) and link.get("url"):
            sources.append({
                "provider": link.get("provider", "other"),
                "source_video_id": link.get("id", ""),
                "original_url": link["url"],
                "canonical_url": link["url"],
                "source_type": link.get("source_type", "video"),
                "provenance": link.get("provenance", "scraped"),
            })

    plan["sources"] = sources

    # ── Entity resolution ────────────────────────────────────────────
    if entity_res.get("artist"):
        plan["entities"]["artist"] = entity_res["artist"]
    if entity_res.get("album"):
        plan["entities"]["album"] = entity_res["album"]
    if entity_res.get("track"):
        plan["entities"]["track"] = entity_res["track"]
    if entity_res.get("canonical_track"):
        plan["entities"]["canonical_track"] = entity_res["canonical_track"]

    # Enrich video fields from entity data
    _enrich_video_from_entities(plan, entity_res, metadata)

    # ── Media assets ─────────────────────────────────────────────────
    assets = []
    for asset_info in artwork.get("assets", []):
        assets.append(asset_info)
    for asset_info in artwork_source.get("assets", []):
        assets.append(asset_info)
    plan["media_assets"] = assets

    # ── Snapshot ─────────────────────────────────────────────────────
    plan["snapshot_reason"] = f"{import_type}_import"

    # ── Processing flags ─────────────────────────────────────────────
    flags = {}
    if ws.is_stage_complete("analyze_media"):
        flags["quality_analyzed"] = "import"
    if ws.is_stage_complete("resolve_metadata"):
        flags["metadata_scraped"] = "import"
        flags["metadata_resolved"] = "import"
    if ws.is_stage_complete("organize_file"):
        flags["file_organized"] = "import"
        flags["filename_checked"] = "import"
    if ws.is_stage_complete("normalize_audio"):
        flags["audio_normalized"] = "import"
    if ws.is_stage_complete("resolve_entities"):
        flags["track_identified"] = "fingerprint"
        flags["canonical_linked"] = "canonical"
    if ws.is_stage_complete("fetch_artwork") or ws.is_stage_complete("copy_artwork"):
        flags["artwork_fetched"] = f"{import_type}_import"
    if ws.is_stage_complete("nfo_write"):
        flags["nfo_exported"] = "import"
    if metadata.get("ai_source_resolution"):
        flags["ai_source_resolution"] = "import"
    if metadata.get("ai_final_review"):
        flags["ai_final_review"] = "import"
        if metadata.get("plot"):
            flags["metadata_ai_analyzed"] = "import"
            flags["ai_enriched"] = "import"
            flags["description_generated"] = "import"
    if input_data.get("import_type") == "url":
        flags["downloaded"] = "import"
    elif input_data.get("import_type") == "library":
        flags["downloaded"] = "library"
    plan["processing_flags"] = flags

    # ── Deferred tasks ───────────────────────────────────────────────
    deferred = ["preview", "matching"]
    if mode == "advanced":
        deferred.extend(["kodi_export", "entity_artwork", "orphan_cleanup"])
        if not metadata.get("ai_final_review") or not metadata.get("plot"):
            deferred.append("ai_enrichment")
        deferred.append("scene_analysis")
    plan["deferred_tasks"] = deferred

    return plan


# ── Helpers ──────────────────────────────────────────────────────────

def _guess_provider(url: str) -> str:
    """Guess SourceProvider from a URL string."""
    url_lower = (url or "").lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "vimeo.com" in url_lower:
        return "vimeo"
    if "wikipedia.org" in url_lower:
        return "wikipedia"
    if "imdb.com" in url_lower:
        return "imdb"
    if "musicbrainz.org" in url_lower:
        return "musicbrainz"
    return "other"


def _extract_source_id(url: str) -> str:
    """Best-effort extraction of an ID from a URL."""
    import re
    if not url:
        return ""
    # YouTube
    m = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
    if m:
        return m.group(1)
    # IMDB
    m = re.search(r"(tt\d+|nm\d+)", url)
    if m:
        return m.group(1)
    # Wikipedia page title
    m = re.search(r"wikipedia\.org/wiki/(.+?)(?:\?|#|$)", url)
    if m:
        return m.group(1)
    # MusicBrainz UUID
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", url)
    if m:
        return m.group(1)
    return url[:200]


def _enrich_video_from_entities(plan: dict, entity_res: dict, metadata: dict) -> None:
    """Fill video metadata gaps from entity resolution data."""
    resolved_track = (entity_res.get("track") or {}).get("resolved", {})
    resolved_album = (entity_res.get("album") or {}).get("resolved", {})
    resolved_artist = (entity_res.get("artist") or {}).get("resolved", {})

    v = plan["video"]

    # Year from track or album
    if not v.get("year"):
        v["year"] = resolved_track.get("year") or resolved_album.get("year")

    # Album from album resolution
    if not v.get("album") and resolved_album.get("title"):
        v["album"] = resolved_album["title"]

    # MB IDs from entity resolution
    if not v.get("mb_recording_id") and resolved_track.get("mb_recording_id"):
        v["mb_recording_id"] = resolved_track["mb_recording_id"]
    if not v.get("mb_release_id") and resolved_track.get("mb_release_id"):
        v["mb_release_id"] = resolved_track["mb_release_id"]
    if not v.get("mb_artist_id") and resolved_artist.get("mb_artist_id"):
        v["mb_artist_id"] = resolved_artist["mb_artist_id"]

    # Genres from entities
    if not plan.get("genres"):
        plan["genres"] = (
            resolved_track.get("genres")
            or resolved_album.get("genres")
            or resolved_artist.get("genres")
            or []
        )
