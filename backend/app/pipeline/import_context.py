"""Typed, side-effect-free context and shared metadata stage for every import path."""
from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.services.import_policy import ImportPolicy, validate_provider_url


ImportPathway = Literal[
    "url_add", "playlist_add", "disk_import", "rescan",
    "metadata_action", "trusted_sidecar", "scraper_test",
]


class ImportContext(BaseModel):
    """Immutable inputs shared by production runs and the scraper dry-run UI."""

    model_config = ConfigDict(frozen=True)

    pathway: ImportPathway
    source: str
    policy: ImportPolicy
    dry_run: bool = False
    artist_override: Optional[str] = None
    title_override: Optional[str] = None
    wikipedia_url: Optional[str] = None
    musicbrainz_url: Optional[str] = None
    correlation_id: Optional[str] = None
    schema_version: int = 1

    @field_validator("wikipedia_url")
    @classmethod
    def validate_wikipedia_url(cls, value: Optional[str]) -> Optional[str]:
        return validate_provider_url(value, "wikipedia")

    @field_validator("musicbrainz_url")
    @classmethod
    def validate_musicbrainz_url(cls, value: Optional[str]) -> Optional[str]:
        return validate_provider_url(value, "musicbrainz")


def selected_stages(context: ImportContext) -> tuple[str, ...]:
    """Return the auditable stage contract for a pathway and policy."""
    stages = ["identity"]
    if context.pathway in {"url_add", "playlist_add", "scraper_test"}:
        stages.append("provider_metadata")
    if context.pathway in {"disk_import", "rescan"} and context.policy.source_match:
        stages.append("source_match")
    if context.pathway == "trusted_sidecar":
        stages.append("trusted_sidecar")
    if context.policy.providers or not context.policy.skip_ai:
        stages.append("metadata_resolution")
    if context.policy.tmvdb_pull:
        stages.append("tmvdb_pull")
    if context.policy.scene_analysis:
        stages.append("scene_analysis")
    if context.policy.normalise_audio:
        stages.append("normalise_audio")
    if context.policy.review_mode != "none":
        stages.append("review")
    return tuple(stages)


_TRACE_RESULT_FIELDS = (
    "artist", "title", "album", "year", "genres", "plot", "image_url",
    "mb_artist_id", "mb_recording_id", "mb_release_id",
    "mb_release_group_id", "imdb_url",
)


def _result_snapshot(metadata: dict[str, Any], fields=_TRACE_RESULT_FIELDS) -> dict[str, Any]:
    """Keep diagnostic responses useful without copying internal state."""
    return {
        field: metadata.get(field)
        for field in fields
        if metadata.get(field) not in (None, "", [])
    }


def _build_metadata_trace(
    context: ImportContext,
    *,
    artist: str,
    title: str,
    source_url: str,
    platform_title: str,
    channel_name: str,
    platform_description: str,
    platform_tags: list[str],
    upload_date: str,
    filename: str,
    folder_name: str,
    duration_seconds: Optional[float],
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Describe the data crossing every metadata-stage boundary.

    These records are produced by the same function used by production jobs
    and the Scraper Tester.  The persistence layer applies redaction before
    storing them.
    """
    sources = metadata.get("scraper_sources_used") or []
    logs = [str(line) for line in metadata.get("pipeline_log") or []]
    failures = metadata.get("pipeline_failures") or []
    base_request = {
        "artist": artist,
        "title": title,
        "source_url": source_url or context.source,
        "platform_title": platform_title,
        "channel_name": channel_name,
        "platform_description": platform_description,
        "platform_tags": platform_tags,
        "upload_date": upload_date,
        "filename": filename,
        "folder_name": folder_name,
        "duration_seconds": duration_seconds,
    }
    events: list[dict[str, Any]] = [{
        "step": "identity",
        "provider": None,
        "status": "succeeded",
        "request": {"source": context.source, "pathway": context.pathway},
        "response": {"artist": artist, "title": title},
        "decisions": [],
    }]

    if context.policy.skip_ai:
        events.append({
            "step": "ai_source_resolution", "provider": "ai",
            "status": "skipped", "request": {}, "response": {},
            "decisions": ["AI is disabled by the effective import policy"],
        })
    else:
        ai_source = metadata.get("ai_source_resolution")
        ai_error = ai_source.get("error") if isinstance(ai_source, dict) else None
        events.append({
            "step": "ai_source_resolution", "provider": "ai",
            "status": "failed" if (not ai_source or ai_error) else "succeeded",
            "request": base_request,
            "response": ai_source or {},
            "decisions": [line for line in logs if line.startswith("ai_identity_")],
            "exception": ai_error,
        })

    provider_specs = (
        (
            "musicbrainz", context.policy.skip_musicbrainz,
            context.musicbrainz_url,
            ("album", "year", "genres", "mb_artist_id", "mb_recording_id",
             "mb_release_id", "mb_release_group_id"),
        ),
        (
            "wikipedia", context.policy.skip_wikipedia,
            context.wikipedia_url,
            ("album", "year", "genres", "plot", "image_url"),
        ),
    )
    for provider, skipped, direct_url, fields in provider_specs:
        provider_label = "MusicBrainz" if provider == "musicbrainz" else "Wikipedia"
        used = [source for source in sources if str(source).startswith(f"{provider}:")]
        failure_prefix = "MB_" if provider == "musicbrainz" else "WIKI_"
        provider_failures = [
            failure for failure in failures
            if str(failure.get("code", "")).upper().startswith(failure_prefix)
        ]
        if skipped:
            status = "skipped"
        elif used:
            status = "succeeded"
        else:
            status = "failed"
        request = {
            "target": "direct_url" if direct_url else "search",
            "url": direct_url,
            "query": None if direct_url else {"artist": artist, "title": title},
        } if not skipped else {}
        provider_results = metadata.get("_provider_results")
        response_fields = (
            (provider_results or {}).get(provider, {})
            if provider_results is not None
            else _result_snapshot(metadata, fields)
        )
        source_urls = metadata.get("_source_urls") or {}
        events.append({
            "step": f"{provider}_fetch", "provider": provider,
            "status": status, "request": request,
            "response": {
                "sources_used": used,
                "source_url": source_urls.get(provider),
                "fields": response_fields,
            } if not skipped else {},
            "decisions": [line for line in logs if provider_label in line],
            "exception": provider_failures or None,
        })

    imdb_sources = [source for source in sources if str(source).startswith("imdb:")]
    imdb_enabled = context.policy.ai_role == "proofread"
    events.append({
        "step": "imdb_lookup", "provider": "imdb",
        "status": "succeeded" if imdb_enabled else "skipped",
        "request": ({"query": {"artist": artist, "title": title}}
                    if imdb_enabled else {}),
        "response": ({
            "sources_used": imdb_sources,
            "source_url": (metadata.get("_source_urls") or {}).get("imdb"),
            "fields": ((metadata.get("_provider_results") or {}).get("imdb", {})
                       if metadata.get("_provider_results") is not None
                       else _result_snapshot(metadata, ("imdb_url",))),
        } if imdb_enabled else {}),
        "decisions": [line for line in logs if "IMDB" in line],
    })

    events.append({
        "step": "validation", "provider": None,
        "status": "failed" if failures else "succeeded",
        "request": {"candidate_fields": _result_snapshot(metadata)},
        "response": {"failures": failures},
        "decisions": [
            line for line in logs
            if any(word in line.lower() for word in ("rejected", "discarded", "cleared"))
        ],
        "exception": failures or None,
    })

    if context.policy.skip_ai:
        events.append({
            "step": "ai_final_review", "provider": "ai",
            "status": "skipped", "request": {}, "response": {},
            "decisions": ["AI final review is disabled by the effective import policy"],
        })
    else:
        ai_review = metadata.get("ai_final_review")
        review_error = ai_review.get("error") if isinstance(ai_review, dict) else None
        events.append({
            "step": "ai_final_review", "provider": "ai",
            "status": "failed" if (not ai_review or review_error) else "succeeded",
            "request": {
                "scraped_metadata": metadata.get("_pre_ai_snapshot") or {},
                "scraper_sources": sources,
            },
            "response": ai_review or {},
            "decisions": [line for line in logs if line.startswith("ai_review_")],
            "exception": review_error,
        })

    events.append({
        "step": "result", "provider": None,
        "status": "succeeded", "request": {},
        "response": _result_snapshot(metadata),
        "decisions": [],
    })
    return events


def run_metadata_stage(
    context: ImportContext,
    *,
    artist: str,
    title: str,
    db=None,
    source_url: str = "",
    platform_title: str = "",
    channel_name: str = "",
    platform_description: str = "",
    platform_tags: Optional[list[str]] = None,
    upload_date: str = "",
    duration_seconds: Optional[float] = None,
    ytdlp_metadata: Optional[dict[str, Any]] = None,
    filename: str = "",
    folder_name: str = "",
    log_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Execute the exact production metadata resolver without mutating files/DB."""
    from app.scraper.unified_metadata import resolve_metadata_unified

    effective_tags = platform_tags or []
    metadata = resolve_metadata_unified(
        artist=artist,
        title=title,
        db=db,
        source_url=source_url or context.source,
        platform_title=platform_title,
        channel_name=channel_name,
        platform_description=platform_description,
        platform_tags=effective_tags,
        upload_date=upload_date,
        duration_seconds=duration_seconds,
        ytdlp_metadata=ytdlp_metadata,
        filename=filename,
        folder_name=folder_name,
        skip_wikipedia=context.policy.skip_wikipedia,
        skip_musicbrainz=context.policy.skip_musicbrainz,
        skip_ai=context.policy.skip_ai,
        wikipedia_url=context.wikipedia_url if not context.policy.skip_wikipedia else None,
        musicbrainz_url=context.musicbrainz_url if not context.policy.skip_musicbrainz else None,
        log_callback=log_callback,
        _test_mode=context.dry_run,
    )
    metadata["import_context"] = context.model_dump(mode="json")
    metadata["selected_stages"] = list(selected_stages(context))
    metadata["structured_trace"] = _build_metadata_trace(
        context,
        artist=artist,
        title=title,
        source_url=source_url,
        platform_title=platform_title,
        channel_name=channel_name,
        platform_description=platform_description,
        platform_tags=effective_tags,
        upload_date=upload_date,
        filename=filename,
        folder_name=folder_name,
        duration_seconds=duration_seconds,
        metadata=metadata,
    )
    return metadata


def context_from_options(
    pathway: ImportPathway,
    source: str,
    options: dict[str, Any],
    *,
    dry_run: bool = False,
) -> ImportContext:
    policy_data = options.get("import_policy")
    if policy_data:
        policy = ImportPolicy.model_validate(policy_data)
    else:
        from app.services.import_policy import policy_from_pipeline_options
        policy = policy_from_pipeline_options(options, library=pathway == "disk_import")
    return ImportContext(
        pathway=pathway,
        source=source,
        policy=policy,
        dry_run=dry_run,
        artist_override=options.get("artist_override"),
        title_override=options.get("title_override"),
        wikipedia_url=options.get("wikipedia_url"),
        musicbrainz_url=options.get("musicbrainz_url"),
        correlation_id=options.get("correlation_id"),
    )
