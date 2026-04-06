"""
AI Final Review — Post-scraping verification and correction.

This is the second AI stage in the metadata pipeline (after source resolution):

  1. AI Source Resolution (find correct links/IDs)
  2. Scraper fetch (using resolved links)
  3. Validation
  4. **AI Final Review** ← THIS MODULE
  5. Save metadata

The AI reviews the scraped results against the source video context and:
  - Detects discrepancies between scraped data and source identity
  - Corrects plot/description to be video-specific
  - Confirms or corrects artist/title/album
  - Confirms or rejects artwork choices
  - Resolves conflicts between scrapers and source hints

This is DIFFERENT from Source Resolution:
  - Source Resolution: Find correct external links and identity (pre-scrape)
  - Final Review: Verify and repair scraped metadata (post-scrape)
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FinalReviewResult:
    """Result of AI final review of scraped metadata."""
    # Corrected fields (None = no change from scraped)
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    plot: Optional[str] = None
    genres: Optional[List[str]] = None

    # Artwork decision
    artwork_approved: bool = True
    artwork_rejection_reason: Optional[str] = None

    # Per-field confidence
    field_scores: Dict[str, float] = field(default_factory=dict)
    overall_confidence: float = 0.0

    # Changes applied
    changes: List[str] = field(default_factory=list)
    scraper_overrides: List[str] = field(default_factory=list)

    # Proposed removals — fields the AI recommends setting to null because
    # the scraped value is incorrect and no correct replacement was found.
    # Dict mapping field name → reason for removal.
    proposed_removals: Dict[str, str] = field(default_factory=dict)

    # Debugging
    raw_response: str = ""
    prompt_used: str = ""
    tokens_used: int = 0
    model_name: str = ""
    error: str = ""  # Non-empty when the AI call failed

    def has_corrections(self) -> bool:
        """Check if the review produced any corrections."""
        return bool(self.changes)


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

FINAL_REVIEW_SYSTEM_PROMPT = (
    "You are a music video metadata quality reviewer. "
    "Compare scraped metadata against the source video context and fix errors. "
    "Prefer missing data over incorrect data. "
    "Respond in valid JSON only — no markdown, no code fences, no extra text."
)

FINAL_REVIEW_PROMPT = """You are a **Music Video Metadata Reviewer**. Your task is to review scraped metadata and correct any errors by comparing against the original source video context.

## SOURCE VIDEO CONTEXT (ground truth)

**Source URL:** {source_url}
**Platform title:** {platform_title}
**Channel / Uploader:** {channel_name}
**Platform description:**
```
{platform_description}
```
**Filename:** {filename}
**Duration:** {duration}

## SCRAPED METADATA (to review)

- **Artist:** {scraped_artist}
- **Title:** {scraped_title}
- **Album:** {scraped_album}
- **Year:** {scraped_year}
- **Genres:** {scraped_genres}
- **Description:** {scraped_plot}

## AI SOURCE RESOLUTION (pre-scrape identity)

- **Resolved Artist:** {resolved_artist}
- **Resolved Title:** {resolved_title}
- **Resolved Album:** {resolved_album}
- **Version Type:** {version_type}

## SCRAPER SOURCES USED

{scraper_sources}

## YOUR TASK

### Step 1 — Verify Identity
Compare the scraped artist/title against the source video and the AI-resolved identity. If they conflict, determine which is correct.

**IMPORTANT — Album field for music videos:**
The "album" field must be the name of the **single / release** that the music video was released under, NOT the parent studio album. Music videos are typically released as singles. If the scraped album matches or is similar to the song title (indicating it is already the single name), do NOT change it to the parent album. Only change the album if the scraped value is clearly wrong (e.g. a completely unrelated album name).

**CRITICAL — Album sentinel values:**
If the album is "[not set]", null, empty, "Unknown", "N/A", "None", or any similar placeholder, you MUST return null for the album. Do NOT return "Unknown" or any placeholder string as an album name. If no album was found by the scrapers, leave it as null.

**Formatting rules:**
- **Featuring credits:** Always use the format "Artist1 feat. Artist2" (never "ft.", "featuring", or "&" for featured artists)
- **Title suffixes:** Preserve meaningful version indicators like "(Remix)", "(Live)", "(Acoustic)", "(Long Version)", "(Radio Edit)", "(feat. X)". Strip noise suffixes like "(Official Video)", "[HD]", "(Music Video)", "(Lyric Video)".
- **Artist name style:** ALWAYS preserve the artist's exact official stylization (e.g. "CHVRCHES" not "Chvrches", "deadmau5" not "Deadmau5", "Florence + the Machine" not "Florence and the Machine", "SLACKCiRCUS" not "Slackcircus"). When uncertain, prefer the MusicBrainz canonical form. Never apply case normalization to artist names.

### Step 2 — Verify and Improve Description
The scraped description likely comes from Wikipedia and should be preserved and improved — NOT replaced with a short summary.

**CRITICAL — Platform description is NOT the plot:**
The "Platform description" shown above in the SOURCE VIDEO CONTEXT is the YouTube/platform video description. It is provided ONLY for identity verification. You must NEVER use the platform description as the plot text. The platform description typically contains promotional links, social media URLs, lyrics, production credits, and other non-encyclopedic content that is completely unsuitable as a Kodi plot.

**The scraped description IS the plot source:**
The "Description" field under SCRAPED METADATA contains the Wikipedia-sourced description. This is the authoritative source for the plot field.

**CRITICAL — Missing description ("None" or empty):**
If the scraped description is "None", empty, or missing, you MUST generate a new plot from scratch using the Source URL and all available context (artist, title, album, your own knowledge of the song). Write a factual, informative Kodi-suitable description that mentions the artist, song title, and describes the music video's visual style, themes, or narrative if you can infer them. If you cannot describe the video's visuals, focus on the song's musical style, genre, and significance. Do NOT leave the plot as "None" or empty — always generate something.

**CRITICAL — Validate the description matches the music video:**
Before accepting any scraped description, verify it is genuinely about the music video / song at the Source URL. Reject the description if:
- It defines a generic concept, slang term, or non-music topic that happens to share the song's name (e.g. a Wikipedia article about "thirst trap" as a social media concept, not the song "Thirst Trap")
- It describes a completely different song, film, TV show, or unrelated subject
- It is a generic artist biography with no mention of this specific song
- It does not mention the artist, the song, or anything related to music
If the description fails this validation, discard it entirely and write a new plot from scratch using the Source URL and your knowledge, as described above.

Check for additional problems:
- Description from a DIFFERENT song
- Studio version description applied to a live performance
- Original song description applied to a cover
- Raw YouTube description text (contains URLs, social media links, lyrics, or production credits) — this should NEVER be used as the plot

If the scraped description is accurate for this video, **keep the full text** but refine it for use as a Kodi plot:
- Clean up any Wikipedia-style formatting artefacts (citation markers, formatting tags)
- Ensure it reads well as a standalone description
- Keep it detailed and informative (up to {max_plot_length} chars)
- Do NOT compress a good multi-paragraph Wikipedia description into a single sentence
- Do NOT include any URLs, social media links, or song lyrics in the plot
- If the description is already good, return it unchanged or with only minor polish

Only rewrite the description from scratch if it is clearly wrong (about a different song, wrong version, etc.). Even in that case, NEVER use the YouTube platform description as a replacement.

### Step 3 — Verify Artwork
If artwork was fetched, confirm it matches the expected song/album. Flag if:
- The artwork appears to be from a different song/album
- The artwork quality is poor or wrong

### Step 4 — Confidence Assessment
Rate your confidence for each field (0.0–1.0).

### Step 5 — Recommend Removals
If any scraped field contains a value that is **incorrect** and you could not find a correct replacement, add it to `proposed_removals`. This tells the system to set the field to null rather than keeping wrong data. Use this when:
- The scraped album is actually the parent studio album but no single/release name was found
- A scraped year is clearly wrong but the correct year is unknown
- Genres are clearly wrong for this artist/song

Do NOT use `proposed_removals` for artist or title — those are required fields. Only use it when the current value is verifiably wrong. An empty `proposed_removals` object means all scraped values are acceptable or were corrected in `proposed`.

## RESPONSE FORMAT
Respond in **valid JSON only**:
{{
  "proposed": {{
    "artist": "{scraped_artist}",
    "title": "{scraped_title}",
    "album": "{scraped_album}",
    "year": {scraped_year_json},
    "genres": [{scraped_genres_json}],
    "plot": "The full, improved description text (keep Wikipedia detail, clean up formatting)"
  }},
  "confidence": {{
    "artist": 0.95,
    "title": 0.95,
    "album": 0.8,
    "year": 0.9,
    "genres": 0.85,
    "plot": 0.9
  }},
  "artwork": {{
    "approved": true,
    "rejection_reason": null
  }},
  "changes": [
    "Brief description of each change made"
  ],
  "scraper_overrides": [
    "Fields where scraper output was overridden and why"
  ],
  "proposed_removals": {{
    "field_name": "Reason why this field's value is incorrect and should be removed (set to null)"
  }}
}}"""


def _build_final_review_prompt(
    *,
    source_url: str = "",
    platform_title: str = "",
    channel_name: str = "",
    platform_description: str = "",
    filename: str = "",
    duration_seconds: Optional[float] = None,
    scraped_artist: str = "",
    scraped_title: str = "",
    scraped_album: str = "",
    scraped_year: Optional[int] = None,
    scraped_genres: Optional[List[str]] = None,
    scraped_plot: str = "",
    resolved_artist: str = "",
    resolved_title: str = "",
    resolved_album: str = "",
    version_type: str = "normal",
    scraper_sources: str = "",
    max_plot_length: int = 2000,
) -> str:
    """Build the final review prompt."""
    duration = "Unknown"
    if duration_seconds is not None:
        mins, secs = divmod(int(duration_seconds), 60)
        duration = f"{mins}:{secs:02d} ({duration_seconds:.0f}s)"

    desc = platform_description or "Not available"
    if len(desc) > 2000:
        desc = desc[:2000] + "..."

    genres_str = ", ".join(scraped_genres or []) or "Unknown"
    genres_json = ", ".join(f'"{g}"' for g in (scraped_genres or []))
    year_json = str(scraped_year) if scraped_year else "null"

    return FINAL_REVIEW_PROMPT.format(
        source_url=source_url or "Not available",
        platform_title=platform_title or "Not available",
        channel_name=channel_name or "Not available",
        platform_description=desc,
        filename=filename or "Not available",
        duration=duration,
        scraped_artist=scraped_artist or "Unknown",
        scraped_title=scraped_title or "Unknown",
        scraped_album=scraped_album or "[not set]",
        scraped_year=scraped_year or "Unknown",
        scraped_genres=genres_str,
        scraped_plot=scraped_plot or "None",
        resolved_artist=resolved_artist or "Unknown",
        resolved_title=resolved_title or "Unknown",
        resolved_album=resolved_album or "[not set]",
        version_type=version_type,
        scraper_sources=scraper_sources or "No scraper source information available",
        max_plot_length=max_plot_length,
        scraped_year_json=year_json,
        scraped_genres_json=genres_json,
    )


def _parse_final_review_response(raw: str) -> FinalReviewResult:
    """Parse the AI's JSON response into a FinalReviewResult."""
    result = FinalReviewResult()
    result.raw_response = raw

    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        pass

    if data is None:
        import re
        # Sanitize control characters and trailing commas
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', text)
        text = re.sub(r',\s*([}\]])', r'\1', text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass

    # Repair bracket/brace mismatches (GPT-5 sometimes closes arrays
    # with } instead of ], or vice versa).
    if data is None:
        chars = list(text)
        _stack: list[str] = []
        _in_str = False
        _esc = False
        _repaired = False
        for _i, _ch in enumerate(chars):
            if _esc:
                _esc = False
                continue
            if _ch == '\\' and _in_str:
                _esc = True
                continue
            if _ch == '"':
                _in_str = not _in_str
                continue
            if _in_str:
                continue
            if _ch in ('{', '['):
                _stack.append(_ch)
            elif _ch in ('}', ']'):
                if _stack:
                    _opener = _stack[-1]
                    _expected = '}' if _opener == '{' else ']'
                    if _ch != _expected:
                        chars[_i] = _expected
                        _repaired = True
                    _stack.pop()
        if _repaired:
            try:
                data = json.loads(''.join(chars))
            except json.JSONDecodeError:
                pass

    if data is None:
        import re
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    if data is None:
        logger.warning(
            "Failed to parse AI final review response "
            f"(length={len(raw)}, first 300 chars: {raw[:300]!r})"
        )
        return result

    proposed = data.get("proposed", {})
    result.artist = proposed.get("artist")
    result.title = proposed.get("title")
    result.album = proposed.get("album")
    result.year = proposed.get("year")
    result.plot = proposed.get("plot")
    result.genres = proposed.get("genres")

    confidence = data.get("confidence", {})
    result.field_scores = confidence
    scores = [v for v in confidence.values() if isinstance(v, (int, float))]
    result.overall_confidence = sum(scores) / len(scores) if scores else 0.0

    artwork = data.get("artwork", {})
    result.artwork_approved = artwork.get("approved", True)
    result.artwork_rejection_reason = artwork.get("rejection_reason")

    result.changes = data.get("changes", [])
    result.scraper_overrides = data.get("scraper_overrides", [])

    # Parse proposed_removals — AI can recommend specific fields be
    # set to null when the scraped value is wrong and no replacement
    # was found.
    raw_removals = data.get("proposed_removals", {})
    if isinstance(raw_removals, dict):
        result.proposed_removals = {
            k: str(v) for k, v in raw_removals.items()
            if isinstance(k, str) and v
        }
    else:
        result.proposed_removals = {}

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_final_review(
    *,
    source_url: str = "",
    platform_title: str = "",
    channel_name: str = "",
    platform_description: str = "",
    filename: str = "",
    duration_seconds: Optional[float] = None,
    scraped_artist: str = "",
    scraped_title: str = "",
    scraped_album: str = "",
    scraped_year: Optional[int] = None,
    scraped_genres: Optional[List[str]] = None,
    scraped_plot: str = "",
    resolved_artist: str = "",
    resolved_title: str = "",
    resolved_album: str = "",
    version_type: str = "normal",
    scraper_sources: str = "",
    provider_name: Optional[str] = None,
    db: Optional[Session] = None,
) -> Optional[FinalReviewResult]:
    """
    Run AI final review on scraped metadata.

    This should be called AFTER scrapers complete. Returns None if AI is
    not configured or the call fails.
    """
    from app.ai.provider_factory import get_ai_provider
    from app.ai.model_router import get_model_router, TaskType

    router = get_model_router(provider_name)
    model_selection = router.select_model(task_type=TaskType.CORRECTION, mismatch_score=0.0)
    model_override = None if model_selection.model == "configured" else model_selection.model

    provider = get_ai_provider(provider_name, model=model_override)
    if not provider:
        logger.info("AI final review skipped — no AI provider configured")
        return None

    prompt = _build_final_review_prompt(
        source_url=source_url,
        platform_title=platform_title,
        channel_name=channel_name,
        platform_description=platform_description,
        filename=filename,
        duration_seconds=duration_seconds,
        scraped_artist=scraped_artist,
        scraped_title=scraped_title,
        scraped_album=scraped_album,
        scraped_year=scraped_year,
        scraped_genres=scraped_genres,
        scraped_plot=scraped_plot,
        resolved_artist=resolved_artist,
        resolved_title=resolved_title,
        resolved_album=resolved_album,
        version_type=version_type,
        scraper_sources=scraper_sources,
    )

    logger.info(
        f"AI Final Review — sending prompt ({len(prompt)} chars) "
        f"for: {scraped_artist} - {scraped_title} "
        f"(model={model_selection.model})"
    )
    logger.debug(f"AI Final Review prompt:\n{prompt}")

    try:
        import time as _time
        from app.ai.source_resolution import _call_provider_raw

        # Small delay to avoid rate-limiting when AI source resolution
        # just finished (especially in AI Only mode with no scraper gap).
        _time.sleep(2)

        try:
            raw_response = _call_provider_raw(provider, FINAL_REVIEW_SYSTEM_PROMPT, prompt)
        except Exception as e:
            logger.warning(f"AI final review failed on first attempt ({model_selection.model}): {e}")
            # Retry once with standard-tier model as fallback
            _time.sleep(3)
            fallback_selection = router.select_model(task_type=TaskType.ENRICHMENT, mismatch_score=0.0)
            fallback_override = None if fallback_selection.model == "configured" else fallback_selection.model
            fallback_provider = get_ai_provider(provider_name, model=fallback_override)
            if fallback_provider:
                logger.info(f"AI final review retrying with fallback model: {fallback_selection.model}")
                raw_response = _call_provider_raw(fallback_provider, FINAL_REVIEW_SYSTEM_PROMPT, prompt)
                model_selection = fallback_selection
            else:
                raise

        result = _parse_final_review_response(raw_response)
        result.prompt_used = prompt
        result.model_name = model_selection.model

        logger.info(
            f"AI Final Review complete: "
            f"confidence={result.overall_confidence:.2f}, "
            f"changes={len(result.changes)}, "
            f"overrides={len(result.scraper_overrides)}, "
            f"artwork_approved={result.artwork_approved}"
        )

        if result.changes:
            for change in result.changes:
                logger.info(f"  Final Review change: {change}")
        if result.scraper_overrides:
            for override in result.scraper_overrides:
                logger.info(f"  Scraper override: {override}")

        return result

    except Exception as e:
        logger.error(f"AI final review failed: {e}")
        err_result = FinalReviewResult()
        err_result.prompt_used = prompt
        err_result.model_name = model_selection.model
        err_result.raw_response = f"ERROR: {e}"
        err_result.error = str(e)
        return err_result
