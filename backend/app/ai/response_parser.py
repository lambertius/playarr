"""
AI Response Parser — Strict JSON parsing with retry and schema validation.

Provides:
- parse_enrichment_response(): Parse and validate AI JSON response
- extract_ai_metadata(): Convert parsed response → AIMetadataResponse
- strip_markdown_fences(): Clean up LLM output quirks
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Required top-level keys in the AI response
# ---------------------------------------------------------------------------
REQUIRED_KEYS = {"identity", "proposed", "confidence"}
PROPOSED_FIELDS = {"artist", "title", "album", "year", "genres", "plot"}
CONFIDENCE_FIELDS = {"artist", "title", "album", "year", "genres", "plot"}

# Repair prompt sent on first JSON failure
REPAIR_PROMPT = (
    "Your previous response was not valid JSON. "
    "Please respond with ONLY valid JSON matching the schema requested. "
    "No markdown fences, no commentary before or after the JSON object. "
    "Here was your broken response (fix it):\n\n{broken}"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_enrichment_response(
    content: str,
    *,
    retry_fn: Optional[Callable[[str], str]] = None,
) -> Tuple[Dict[str, Any], bool]:
    """
    Parse and validate an AI enrichment JSON response.

    Args:
        content: Raw string content from the AI provider.
        retry_fn: Optional callable(repair_prompt) -> str that makes a second
                  API call to request a fixed response.  If provided, a single
                  retry is attempted on parse failure.

    Returns:
        (parsed_dict, was_retry) — the validated dict and whether a retry
        was needed to obtain it.

    Raises:
        ValueError: If the response cannot be parsed even after retry.
    """
    # Attempt 1: parse directly
    cleaned = strip_markdown_fences(content)
    try:
        parsed = json.loads(cleaned)
        _validate_schema(parsed)
        return parsed, False
    except (json.JSONDecodeError, ValueError) as first_err:
        logger.warning(f"First JSON parse failed: {first_err}")

    # Attempt 2: try extracting JSON object from surrounding text
    extracted = _extract_json_object(content)
    if extracted:
        try:
            parsed = json.loads(extracted)
            _validate_schema(parsed)
            return parsed, False
        except (json.JSONDecodeError, ValueError):
            pass

    # Attempt 3: retry via provider (if callback given)
    if retry_fn:
        logger.info("Attempting JSON repair via provider retry")
        repair_prompt = REPAIR_PROMPT.format(broken=content[:2000])
        try:
            retry_content = retry_fn(repair_prompt)
            retry_cleaned = strip_markdown_fences(retry_content)
            parsed = json.loads(retry_cleaned)
            _validate_schema(parsed)
            return parsed, True
        except (json.JSONDecodeError, ValueError) as retry_err:
            logger.error(f"Retry parse also failed: {retry_err}")

    raise ValueError(
        f"AI response is not valid JSON after all attempts. "
        f"First 300 chars: {content[:300]}"
    )


def extract_ai_metadata(
    parsed: Dict[str, Any],
    tokens: int = 0,
    model_name: str = "",
    raw_response: str = "",
) -> Dict[str, Any]:
    """
    Convert a parsed AI response dict into a flat metadata dict suitable
    for building an AIMetadataResponse.

    Returns a dict with keys:
        artist, title, album, year, plot, genres,
        director, studio, tags,
        field_scores, overall_confidence,
        identity, mismatch_info, change_summary,
        raw_response, tokens_used, model_name
    """
    proposed = parsed.get("proposed", {})
    confidence = parsed.get("confidence", {})
    identity = parsed.get("identity", {})
    mismatch = parsed.get("mismatch", {})

    # ── Extract proposed fields ──
    year_val = proposed.get("year")
    if isinstance(year_val, str):
        try:
            year_val = int(year_val)
        except (ValueError, TypeError):
            year_val = None

    genres = proposed.get("genres")
    if isinstance(genres, str):
        genres = [g.strip() for g in genres.split(",") if g.strip()]

    tags = proposed.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    # ── Build field scores ──
    scores: Dict[str, float] = {}
    for field in ("artist", "title", "album", "year", "genres", "plot",
                  "director", "studio", "tags"):
        raw_score = confidence.get(field) 
        if raw_score is not None:
            try:
                scores[field] = min(1.0, max(0.0, float(raw_score)))
            except (ValueError, TypeError):
                scores[field] = 0.0

    # Overall confidence = average of core fields
    core_fields = ["artist", "title", "album", "year", "genres", "plot"]
    core_scores = [scores.get(f, 0.0) for f in core_fields]
    overall = sum(core_scores) / max(len(core_scores), 1)

    return {
        # Proposed values
        "artist": proposed.get("artist"),
        "title": proposed.get("title"),
        "album": proposed.get("album"),
        "year": year_val,
        "plot": proposed.get("plot"),
        "genres": genres,
        "director": proposed.get("director"),
        "studio": proposed.get("studio"),
        "tags": tags,
        # Confidence
        "field_scores": scores,
        "overall_confidence": overall,
        # Identity verification
        "identity": identity,
        # Mismatch detection from AI
        "mismatch_info": mismatch,
        # Summary
        "change_summary": parsed.get("change_summary", ""),
        # Debug/provenance
        "raw_response": raw_response,
        "tokens_used": tokens,
        "model_name": model_name,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_markdown_fences(content: str) -> str:
    """Remove markdown code fences that LLMs sometimes wrap JSON in."""
    text = content.strip()

    # Remove ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop first fence line
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Drop trailing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    return text


def _extract_json_object(content: str) -> Optional[str]:
    """Try to extract a JSON object {...} from surrounding text."""
    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        candidate = match.group()
        # Quick validation: must contain "proposed"
        if "proposed" in candidate:
            return candidate
    return None


def _validate_schema(parsed: Any) -> None:
    """
    Validate that parsed JSON has the expected structure.

    Raises ValueError if required keys are missing.
    """
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")

    missing = REQUIRED_KEYS - set(parsed.keys())
    if missing:
        raise ValueError(f"Missing required keys: {missing}")

    proposed = parsed.get("proposed", {})
    if not isinstance(proposed, dict):
        raise ValueError("'proposed' must be an object")

    # Check that at least artist and title are present
    if not proposed.get("artist") and not proposed.get("title"):
        raise ValueError("'proposed' must contain at least 'artist' or 'title'")

    confidence = parsed.get("confidence", {})
    if not isinstance(confidence, dict):
        raise ValueError("'confidence' must be an object")


# ---------------------------------------------------------------------------
# Featuring credit normalization (P5)
# ---------------------------------------------------------------------------

def normalize_featuring(artist: str) -> str:
    """Standardize featuring credit format to 'feat.' """
    if not artist:
        return artist
    artist = re.sub(r'\bfeaturing\b', 'feat.', artist, flags=re.IGNORECASE)
    artist = re.sub(r'\bft\.?\b', 'feat.', artist, flags=re.IGNORECASE)
    return artist.strip()
