"""Typed, side-effect-free context and shared metadata stage for every import path."""
from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.import_policy import ImportPolicy


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

    metadata = resolve_metadata_unified(
        artist=artist,
        title=title,
        db=db,
        source_url=source_url or context.source,
        platform_title=platform_title,
        channel_name=channel_name,
        platform_description=platform_description,
        platform_tags=platform_tags or [],
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
