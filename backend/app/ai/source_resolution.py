"""
AI Source Resolution — Determine canonical identity and external source links
BEFORE scrapers run.

This is the new first AI stage in the metadata pipeline:

  1. Parse local/source identity hints
  2. **AI Source Resolution** ← THIS MODULE
  3. Scraper fetch using resolved source links/IDs
  4. Scraper validation
  5. AI final review and correction
  6. Save metadata

The AI receives all available context (source URL, platform title, channel,
description, filename, folder, duration, parsed artist/title, fingerprint)
and returns structured JSON with:
  - identity (artist, title, album, version_type)
  - external source links (Wikipedia URL, MusicBrainz IDs, IMDB URL)
  - confidence scores

Scrapers then use these AI-provided links/IDs *first*, falling back to
blind search only when AI didn't provide them or they're invalid.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SourceResolutionIdentity:
    """AI-resolved canonical identity."""
    artist: str = ""
    title: str = ""
    album: Optional[str] = None
    version_type: str = "normal"  # normal|cover|live|alternate|uncensored
    alternate_version_label: Optional[str] = None


@dataclass
class SourceResolutionSources:
    """AI-resolved external source links/IDs."""
    wikipedia_url: Optional[str] = None
    musicbrainz_artist_id: Optional[str] = None
    musicbrainz_recording_id: Optional[str] = None
    musicbrainz_release_id: Optional[str] = None
    imdb_url: Optional[str] = None
    youtube_url: str = ""


@dataclass
class SourceResolutionConfidence:
    """Confidence scores for the resolution."""
    identity: float = 0.0
    sources: float = 0.0


@dataclass
class SourceResolutionResult:
    """Complete AI source resolution result."""
    identity: SourceResolutionIdentity = field(default_factory=SourceResolutionIdentity)
    sources: SourceResolutionSources = field(default_factory=SourceResolutionSources)
    confidence: SourceResolutionConfidence = field(default_factory=SourceResolutionConfidence)
    notes: List[str] = field(default_factory=list)
    raw_response: str = ""
    prompt_used: str = ""
    tokens_used: int = 0
    model_name: str = ""
    error: str = ""  # Non-empty when the AI call failed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity": {
                "artist": self.identity.artist,
                "title": self.identity.title,
                "album": self.identity.album,
                "version_type": self.identity.version_type,
                "alternate_version_label": self.identity.alternate_version_label,
            },
            "sources": {
                "wikipedia_url": self.sources.wikipedia_url,
                "musicbrainz_artist_id": self.sources.musicbrainz_artist_id,
                "musicbrainz_recording_id": self.sources.musicbrainz_recording_id,
                "musicbrainz_release_id": self.sources.musicbrainz_release_id,
                "imdb_url": self.sources.imdb_url,
                "youtube_url": self.sources.youtube_url,
            },
            "confidence": {
                "identity": self.confidence.identity,
                "sources": self.confidence.sources,
            },
            "notes": self.notes,
            "prompt_used": self.prompt_used,
            "raw_response": self.raw_response,
            "model_name": self.model_name,
            "error": self.error or None,
        }


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

SOURCE_RESOLUTION_SYSTEM_PROMPT = (
    "You are an expert music metadata resolver. "
    "Given context about a music video, determine the canonical identity "
    "(artist, title, album, version type) and provide the correct external "
    "source links (Wikipedia URL, MusicBrainz IDs, IMDB URL). "
    "Respond in valid JSON only — no markdown, no code fences, no extra text."
)

SOURCE_RESOLUTION_PROMPT = """You are a **Music Video Source Resolver**. Your task is to determine the canonical identity of a music video and find the correct external source links BEFORE any web scraping occurs.

## CONTEXT

**Source video URL:** {source_url}
**Platform title:** {platform_title}
**Channel / Uploader:** {channel_name}
**Platform description:**
```
{platform_description}
```
**Filename:** {filename}
**Folder name:** {folder_name}
**Duration:** {duration}
**Current parsed artist:** {parsed_artist}
**Current parsed title:** {parsed_title}
{fingerprint_section}

## YOUR TASK

### Step 1 — Identity Resolution
Determine the canonical identity of this music video:
- Who is the actual performing artist? (Use proper capitalization, official name)
- What is the actual song title? (Clean, no suffixes like "Official Video")
- What album is it from? This should be the **single / release name** if it was released as a named single that differs from the song title. Use null if the song was not part of a named release, or if the release name is just the song title.
- What type of version is this? (normal, cover, live, alternate, uncensored)
- If alternate, what is the version label? (e.g. "Acoustic", "Remix", "Director's Cut")

Use ALL context clues:
- The platform title and description often contain credits
- The channel name is frequently the artist or their label
- The filename may contain artist-title patterns
- The video URL/platform can indicate the original uploader

**Formatting rules:**
- **Featuring credits:** Always use the format "Artist1 feat. Artist2" (never "ft.", "featuring", or "&" for featured artists)
- **Title suffixes:** Preserve meaningful version indicators like "(Remix)", "(Live)", "(Acoustic)", "(Long Version)", "(Radio Edit)", "(feat. X)". Strip noise suffixes like "(Official Video)", "[HD]", "(Music Video)", "(Lyric Video)".
- **Artist name style:** ALWAYS preserve the artist's exact official stylization (e.g. "CHVRCHES" not "Chvrches", "deadmau5" not "Deadmau5", "Florence + the Machine" not "Florence and the Machine", "SLACKCiRCUS" not "Slackcircus"). When uncertain, prefer the MusicBrainz canonical form. Never apply case normalization to artist names.

### Step 2 — Source Link Resolution
Based on the identified track, determine the most likely correct external source URLs/IDs:
- **Wikipedia URL**: The Wikipedia page for THIS specific song (not the artist's page, not a different song). Use the format "https://en.wikipedia.org/wiki/Page_Title". If the song doesn't have a Wikipedia page, return null.
- **MusicBrainz Artist ID**: UUID of the artist on MusicBrainz (null if unknown)
- **MusicBrainz Recording ID**: UUID of the recording on MusicBrainz (null if unknown)
- **MusicBrainz Release ID**: UUID of the release/album on MusicBrainz (null if unknown)
- **IMDB URL**: IMDB page for the music video if it exists (null otherwise)

IMPORTANT:
- Only provide URLs/IDs you are confident are correct
- A wrong Wikipedia page is WORSE than no Wikipedia page
- A wrong MusicBrainz ID is WORSE than no ID
- The YouTube/Vimeo source URL must always be included

### Step 3 — Confidence Assessment
Rate your confidence:
- **identity**: How confident are you in the artist/title/album identification (0.0–1.0)
- **sources**: How confident are you that the source links are correct (0.0–1.0)

## RESPONSE FORMAT
Respond in **valid JSON only** — no markdown fences, no commentary:
{{
  "identity": {{
    "artist": "Canonical Artist Name",
    "title": "Canonical Song Title",
    "album": "Album Name or null",
    "version_type": "normal",
    "alternate_version_label": null
  }},
  "sources": {{
    "wikipedia_url": "https://en.wikipedia.org/wiki/Song_Title_(song) or null",
    "musicbrainz_artist_id": "UUID or null",
    "musicbrainz_recording_id": "UUID or null",
    "musicbrainz_release_id": "UUID or null",
    "imdb_url": "https://www.imdb.com/title/ttXXXXXXXX/ or null",
    "youtube_url": "{source_url}"
  }},
  "confidence": {{
    "identity": 0.9,
    "sources": 0.7
  }},
  "notes": [
    "Brief notes about resolution decisions"
  ]
}}"""


def _build_source_resolution_prompt(
    *,
    source_url: str = "",
    platform_title: str = "",
    channel_name: str = "",
    platform_description: str = "",
    filename: str = "",
    folder_name: str = "",
    duration_seconds: Optional[float] = None,
    parsed_artist: str = "",
    parsed_title: str = "",
    fingerprint_artist: str = "",
    fingerprint_title: str = "",
    fingerprint_confidence: float = 0.0,
) -> str:
    """Build the source resolution prompt with all available context."""
    # Format duration
    duration = "Unknown"
    if duration_seconds is not None:
        mins, secs = divmod(int(duration_seconds), 60)
        duration = f"{mins}:{secs:02d} ({duration_seconds:.0f}s)"

    # Fingerprint section
    fingerprint_section = ""
    if fingerprint_artist or fingerprint_title:
        fingerprint_section = (
            f"\n**Audio fingerprint match:** {fingerprint_artist} — {fingerprint_title} "
            f"(confidence: {fingerprint_confidence:.0%})\n"
        )

    # Truncate description
    desc = platform_description or "Not available"
    if len(desc) > 2000:
        desc = desc[:2000] + "..."

    return SOURCE_RESOLUTION_PROMPT.format(
        source_url=source_url or "Not available",
        platform_title=platform_title or "Not available",
        channel_name=channel_name or "Not available",
        platform_description=desc,
        filename=filename or "Not available",
        folder_name=folder_name or "Not available",
        duration=duration,
        parsed_artist=parsed_artist or "Unknown",
        parsed_title=parsed_title or "Unknown",
        fingerprint_section=fingerprint_section,
    )


def _parse_source_resolution_response(raw: str) -> SourceResolutionResult:
    """Parse the AI's JSON response into a SourceResolutionResult."""
    result = SourceResolutionResult()
    result.raw_response = raw

    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from the response
        import re
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                logger.warning("Failed to parse AI source resolution response as JSON")
                result.notes.append("Failed to parse AI response")
                return result
        else:
            logger.warning("No JSON found in AI source resolution response")
            result.notes.append("No JSON found in AI response")
            return result

    # Parse identity
    identity = data.get("identity", {})
    result.identity.artist = identity.get("artist", "")
    result.identity.title = identity.get("title", "")
    result.identity.album = identity.get("album")
    result.identity.version_type = identity.get("version_type", "normal")
    result.identity.alternate_version_label = identity.get("alternate_version_label")

    # Parse sources
    sources = data.get("sources", {})
    result.sources.wikipedia_url = sources.get("wikipedia_url")
    result.sources.musicbrainz_artist_id = sources.get("musicbrainz_artist_id")
    result.sources.musicbrainz_recording_id = sources.get("musicbrainz_recording_id")
    result.sources.musicbrainz_release_id = sources.get("musicbrainz_release_id")
    result.sources.imdb_url = sources.get("imdb_url")
    result.sources.youtube_url = sources.get("youtube_url", "")

    # Parse confidence
    confidence = data.get("confidence", {})
    result.confidence.identity = float(confidence.get("identity", 0.0))
    result.confidence.sources = float(confidence.get("sources", 0.0))

    # Parse notes
    result.notes = data.get("notes", [])

    # Validate: strip null-like string values
    if result.sources.wikipedia_url and result.sources.wikipedia_url.lower() in ("null", "none", ""):
        result.sources.wikipedia_url = None
    if result.sources.musicbrainz_artist_id and result.sources.musicbrainz_artist_id.lower() in ("null", "none", ""):
        result.sources.musicbrainz_artist_id = None
    if result.sources.musicbrainz_recording_id and result.sources.musicbrainz_recording_id.lower() in ("null", "none", ""):
        result.sources.musicbrainz_recording_id = None
    if result.sources.musicbrainz_release_id and result.sources.musicbrainz_release_id.lower() in ("null", "none", ""):
        result.sources.musicbrainz_release_id = None
    if result.sources.imdb_url and result.sources.imdb_url.lower() in ("null", "none", ""):
        result.sources.imdb_url = None

    # Validate Wikipedia URL format
    if result.sources.wikipedia_url:
        if "wikipedia.org" not in result.sources.wikipedia_url:
            logger.warning(f"AI returned invalid Wikipedia URL: {result.sources.wikipedia_url}")
            result.sources.wikipedia_url = None

    # Validate IMDB URL format
    if result.sources.imdb_url:
        if "imdb.com" not in result.sources.imdb_url:
            logger.warning(f"AI returned invalid IMDB URL: {result.sources.imdb_url}")
            result.sources.imdb_url = None

    # Validate MusicBrainz UUID format (basic check)
    import re
    uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
    for field_name in ("musicbrainz_artist_id", "musicbrainz_recording_id", "musicbrainz_release_id"):
        val = getattr(result.sources, field_name)
        if val and not uuid_pattern.match(val):
            logger.warning(f"AI returned invalid MusicBrainz UUID for {field_name}: {val}")
            setattr(result.sources, field_name, None)

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def resolve_sources_with_ai(
    *,
    source_url: str = "",
    platform_title: str = "",
    channel_name: str = "",
    platform_description: str = "",
    filename: str = "",
    folder_name: str = "",
    duration_seconds: Optional[float] = None,
    parsed_artist: str = "",
    parsed_title: str = "",
    fingerprint_artist: str = "",
    fingerprint_title: str = "",
    fingerprint_confidence: float = 0.0,
    provider_name: Optional[str] = None,
    db: Optional[Session] = None,
) -> Optional[SourceResolutionResult]:
    """
    Run AI source resolution to determine canonical identity and external links.

    This should be called BEFORE scrapers run. Returns None if AI is not
    configured or the call fails.

    Args:
        source_url: YouTube/Vimeo URL
        platform_title: Video title from the platform
        channel_name: Channel / uploader name
        platform_description: Full video description from the platform
        filename: Local video filename
        folder_name: Folder name where the video is stored
        duration_seconds: Video duration
        parsed_artist: Currently parsed artist name
        parsed_title: Currently parsed title
        fingerprint_artist: Artist from audio fingerprint (if available)
        fingerprint_title: Title from audio fingerprint (if available)
        fingerprint_confidence: Fingerprint match confidence
        provider_name: Override AI provider (None = use settings)
        db: SQLAlchemy session for loading custom prompts and settings

    Returns:
        SourceResolutionResult or None if AI unavailable/failed.
    """
    from app.ai.provider_factory import get_ai_provider
    from app.ai.model_router import get_model_router, TaskType

    # Get AI provider
    router = get_model_router(provider_name)
    model_selection = router.select_model(task_type=TaskType.ENRICHMENT, mismatch_score=0.0)
    model_override = None if model_selection.model == "configured" else model_selection.model

    provider = get_ai_provider(provider_name, model=model_override)
    if not provider:
        logger.info("AI source resolution skipped — no AI provider configured")
        return None

    # Build prompt
    prompt = _build_source_resolution_prompt(
        source_url=source_url,
        platform_title=platform_title,
        channel_name=channel_name,
        platform_description=platform_description,
        filename=filename,
        folder_name=folder_name,
        duration_seconds=duration_seconds,
        parsed_artist=parsed_artist,
        parsed_title=parsed_title,
        fingerprint_artist=fingerprint_artist,
        fingerprint_title=fingerprint_title,
        fingerprint_confidence=fingerprint_confidence,
    )

    # Log the prompt (without secrets)
    logger.info(
        f"AI Source Resolution — sending prompt ({len(prompt)} chars) "
        f"for: {parsed_artist} - {parsed_title} "
        f"(model={model_selection.model})"
    )
    logger.debug(f"AI Source Resolution prompt:\n{prompt}")

    try:
        # Call the AI provider using the generate_plot interface
        # (we don't need the full enrich_metadata flow, just a text prompt → JSON response)
        # Use the provider's raw API to send our custom prompt
        raw_response = _call_provider_raw(provider, SOURCE_RESOLUTION_SYSTEM_PROMPT, prompt)

        result = _parse_source_resolution_response(raw_response)
        result.prompt_used = prompt
        result.model_name = model_selection.model

        # Ensure the source URL is always set
        if not result.sources.youtube_url:
            result.sources.youtube_url = source_url

        logger.info(
            f"AI Source Resolution complete: "
            f"identity={result.identity.artist} - {result.identity.title} "
            f"(confidence: identity={result.confidence.identity:.2f}, "
            f"sources={result.confidence.sources:.2f}), "
            f"wiki={'yes' if result.sources.wikipedia_url else 'no'}, "
            f"mb_rec={'yes' if result.sources.musicbrainz_recording_id else 'no'}, "
            f"imdb={'yes' if result.sources.imdb_url else 'no'}"
        )

        return result

    except Exception as e:
        logger.error(f"AI source resolution failed: {e}")
        err_result = SourceResolutionResult()
        err_result.prompt_used = prompt
        err_result.model_name = model_selection.model
        err_result.raw_response = f"ERROR: {e}"
        err_result.error = str(e)
        return err_result


# Models that are reasoning models — they reject temperature != 1 and top_p.
_REASONING_MODEL_PREFIXES = {"gpt-5", "gpt-5-mini", "gpt-5-nano", "o3-mini", "o4-mini", "o1", "o1-mini"}


def _is_reasoning_model(model_name: str) -> bool:
    """Check if a model is a reasoning model that rejects temperature/top_p."""
    return any(model_name.startswith(p) for p in _REASONING_MODEL_PREFIXES)


def _call_provider_raw(provider, system_prompt: str, user_prompt: str) -> str:
    """Call an AI provider with a raw system + user prompt and return the text response.

    Works with OpenAI, Gemini, Claude, and local providers by using their
    underlying HTTP client directly.
    """
    from app.ai.providers.openai_provider import OpenAIProvider
    from app.ai.providers.gemini_provider import GeminiProvider
    from app.ai.providers.claude_provider import ClaudeProvider
    from app.ai.providers.local_provider import LocalProvider

    if isinstance(provider, OpenAIProvider):
        import httpx
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        # GPT-5 / o-series reasoning models only support temperature=1 (default)
        if not _is_reasoning_model(provider.model):
            payload["temperature"] = 0.3
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload, headers=headers, timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    elif isinstance(provider, GeminiProvider):
        import httpx
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{provider.model}:generateContent?key={provider.api_key}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": 0.3},
        }
        resp = httpx.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    elif isinstance(provider, ClaudeProvider):
        import httpx
        headers = {
            "x-api-key": provider.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": provider.model,
            "max_tokens": 2000,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 0.3,
        }
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            json=payload, headers=headers, timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]

    elif isinstance(provider, LocalProvider):
        import httpx
        base_url = provider.base_url.rstrip("/")
        payload = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
        }
        resp = httpx.post(
            f"{base_url}/chat/completions",
            json=payload, timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    else:
        raise ValueError(f"Unsupported AI provider type: {type(provider).__name__}")
