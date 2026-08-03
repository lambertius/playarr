"""Typed import policy shared by URL, library and scraper-test entry points."""
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict


MetadataMode = Literal[
    "existing_only",
    "wiki_only",
    "musicbrainz_only",
    "scrapers",
    "ai_proofread",
    "ai_only",
]


def validate_provider_url(value: str | None, provider: str) -> str | None:
    """Validate and normalize an explicit scraper target."""
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Direct source URLs must use http or https")
    if provider == "wikipedia":
        valid = (
            (host == "wikipedia.org" or host.endswith(".wikipedia.org"))
            and parsed.path.startswith("/wiki/")
        )
        label = "a wikipedia.org/wiki page"
    elif provider == "musicbrainz":
        valid = (
            (host == "musicbrainz.org" or host.endswith(".musicbrainz.org"))
            and bool(re.fullmatch(
                r"/(?:recording|release-group)/[0-9a-fA-F-]+/?", parsed.path,
            ))
        )
        label = "a musicbrainz.org recording or release-group page"
    else:
        raise ValueError(f"Unsupported metadata provider: {provider}")
    if not valid:
        raise ValueError(f"Direct {provider.title()} URL must target {label}")
    if provider == "musicbrainz":
        candidate = parsed._replace(path=parsed.path.lower()).geturl()
    return candidate


class ImportPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    metadata_mode: MetadataMode
    providers: tuple[Literal["wikipedia", "musicbrainz"], ...] = ()
    ai_role: Literal["disabled", "source_resolution", "proofread", "ai_only"] = "disabled"
    source_match: bool = True
    tmvdb_pull: bool = False
    normalise_audio: bool = False
    scene_analysis: bool = False
    locked_field_policy: Literal["preserve", "fail"] = "preserve"
    review_mode: Literal["basic", "advanced", "none"] = "basic"
    schema_version: int = 1

    @property
    def skip_wikipedia(self) -> bool:
        return "wikipedia" not in self.providers

    @property
    def skip_musicbrainz(self) -> bool:
        return "musicbrainz" not in self.providers

    @property
    def skip_ai(self) -> bool:
        return self.ai_role == "disabled"

    @classmethod
    def from_legacy(
        cls,
        *,
        scrape_wikipedia: bool = False,
        scrape_musicbrainz: bool = False,
        ai_auto: bool = False,
        ai_only: bool = False,
        source_match: bool = True,
        tmvdb_pull: bool = False,
        normalise_audio: bool = False,
        scene_analysis: bool = False,
        review_mode: Literal["basic", "advanced", "none"] = "basic",
    ) -> "ImportPolicy":
        if ai_only:
            return cls(
                metadata_mode="ai_only",
                providers=(),
                ai_role="ai_only",
                source_match=source_match,
                tmvdb_pull=tmvdb_pull,
                normalise_audio=normalise_audio,
                scene_analysis=scene_analysis,
                review_mode=review_mode,
            )
        if ai_auto:
            return cls(
                metadata_mode="ai_proofread",
                providers=("wikipedia", "musicbrainz"),
                ai_role="proofread",
                source_match=source_match,
                tmvdb_pull=tmvdb_pull,
                normalise_audio=normalise_audio,
                scene_analysis=scene_analysis,
                review_mode=review_mode,
            )
        providers = tuple(
            provider for provider, enabled in (
                ("wikipedia", scrape_wikipedia),
                ("musicbrainz", scrape_musicbrainz),
            ) if enabled
        )
        if providers == ("wikipedia",):
            mode: MetadataMode = "wiki_only"
        elif providers == ("musicbrainz",):
            mode = "musicbrainz_only"
        elif providers:
            mode = "scrapers"
        else:
            mode = "existing_only"
        return cls(
            metadata_mode=mode,
            providers=providers,
            source_match=source_match,
            tmvdb_pull=tmvdb_pull,
            normalise_audio=normalise_audio,
            scene_analysis=scene_analysis,
            review_mode=review_mode,
        )


def policy_from_request(request, **overrides) -> ImportPolicy:
    # A direct provider URL is an explicit instruction to use that provider.
    # Treat it as authoritative even if an older client forgot to also set the
    # corresponding checkbox.  AI-only remains authoritative in from_legacy().
    wikipedia_url = getattr(request, "wikipedia_url", None)
    musicbrainz_url = getattr(request, "musicbrainz_url", None)
    return ImportPolicy.from_legacy(
        scrape_wikipedia=bool(
            getattr(request, "scrape_wikipedia", False) or wikipedia_url
        ),
        scrape_musicbrainz=bool(
            getattr(request, "scrape_musicbrainz", False) or musicbrainz_url
        ),
        ai_auto=bool(getattr(request, "ai_auto", False)),
        ai_only=bool(getattr(request, "ai_only", False)),
        **overrides,
    )


def policy_from_pipeline_options(options: dict, *, library: bool = False) -> ImportPolicy:
    """Translate historic URL/library option names through one contract."""
    opts = options.get("options", options)
    # Both names are accepted at every boundary so a job can move between URL,
    # playlist, new-video and disk-import pathways without changing meaning.
    if "scrape_wikipedia" in opts:
        scrape_wikipedia = bool(opts["scrape_wikipedia"])
    else:
        scrape_wikipedia = bool(opts.get("scrape", True))

    # ai_auto_fallback is a historic transport name.  The UI and API schema
    # have always defined it as "AI only (no external scrapers)", not as AI
    # Auto.  Keep accepting it, but translate it to the unambiguous ai_only
    # policy role at this single boundary.
    ai_auto = bool(opts.get("ai_auto_analyse", False) or opts.get("ai_auto", False))
    ai_only = bool(opts.get("ai_only", False) or opts.get("ai_auto_fallback", False))
    return ImportPolicy.from_legacy(
        scrape_wikipedia=bool(scrape_wikipedia or opts.get("wikipedia_url")),
        scrape_musicbrainz=bool(
            opts.get("scrape_musicbrainz", True) or opts.get("musicbrainz_url")
        ),
        ai_auto=ai_auto,
        ai_only=ai_only,
        source_match=bool(opts.get("source_match", True)),
        tmvdb_pull=bool(opts.get("tmvdb_pull", False)),
        normalise_audio=bool(opts.get("normalize", False)),
        scene_analysis=bool(opts.get("scene_analysis", False)),
        review_mode=opts.get("review_mode", "basic"),
    )
