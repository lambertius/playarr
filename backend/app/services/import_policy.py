"""Typed import policy shared by URL, library and scraper-test entry points."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


MetadataMode = Literal[
    "existing_only",
    "wiki_only",
    "musicbrainz_only",
    "scrapers",
    "ai_proofread",
    "ai_only",
]


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
    return ImportPolicy.from_legacy(
        scrape_wikipedia=bool(getattr(request, "scrape_wikipedia", False)),
        scrape_musicbrainz=bool(getattr(request, "scrape_musicbrainz", False)),
        ai_auto=bool(getattr(request, "ai_auto", False)),
        ai_only=bool(getattr(request, "ai_only", False)),
        **overrides,
    )


def policy_from_pipeline_options(options: dict, *, library: bool = False) -> ImportPolicy:
    """Translate historic URL/library option names through one contract."""
    opts = options.get("options", options)
    wikipedia_key = "scrape_wikipedia" if library else "scrape"
    return ImportPolicy.from_legacy(
        scrape_wikipedia=bool(opts.get(wikipedia_key, True)),
        scrape_musicbrainz=bool(opts.get("scrape_musicbrainz", True)),
        ai_auto=bool(opts.get("ai_auto_analyse", False) or opts.get("ai_auto_fallback", False)),
        ai_only=bool(opts.get("ai_only", False)),
        source_match=bool(opts.get("source_match", True)),
        tmvdb_pull=bool(opts.get("tmvdb_pull", False)),
        normalise_audio=bool(opts.get("normalize", False)),
        scene_analysis=bool(opts.get("scene_analysis", False)),
        review_mode=opts.get("review_mode", "basic"),
    )
