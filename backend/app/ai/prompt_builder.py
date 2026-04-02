"""
AI Prompt Builder — Constructs the enrichment prompt with full context.

Provides:
- SMART_ENRICHMENT_PROMPT: Comprehensive metadata validation template
- build_metadata_enrichment_prompt(): Provider-agnostic prompt builder
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Smart Music Video Metadata Validator — Prompt Template
# ---------------------------------------------------------------------------

SMART_ENRICHMENT_PROMPT = """You are a **Smart Music Video Metadata Validator**. Your task is to verify, correct, and enrich metadata for a music video in a Kodi media library.

## CONTEXT

**Current scraped metadata:**
- Artist: {artist}
- Title: {title}
- Album: {album}
- Year: {year}
- Genres: {genres}
- Description: {plot}

**File & source information:**
{file_context}

{platform_context}
{mismatch_section}

## YOUR TASK

### Step 1 — Identity Verification
Determine who actually performs this song. Use ALL available context — especially the **channel name** (often the artist or their label) and the **platform video description** (which typically contains credits, links, and context from the uploader). Cross-reference these with the artist name, song title, and filename. Watch for common scraping errors:
- Wrong artist attributed (cover versions, fan uploads, compilation channels)
- Garbled or truncated titles from automated scrapers
- Metadata from a DIFFERENT song pasted onto this video
- "Unknown Artist" or placeholder values that need resolution
- Meme remixes, mashups, or fan edits where the creator is not the original artist

### Step 2 — Metadata Correction
For every field below, provide the **correct** value based on your knowledge of this song and artist. Do NOT echo back bad scraped data — fix it:
- **artist**: Official artist/band name, properly capitalised (e.g. "The Beatles" not "Beatles, The")
- **title**: Correct song title, clean — no suffixes like "(Official Video)", "[HD]", "(Cover)", "(Piano Cover)", or "in the style of X". For covers, use the canonical original song title only — the version_type field already captures that it is a cover.
- **album**: The album this *performer's* version appeared on (null if truly unknown). For covers where the performing artist has no official album release, use null. Do NOT assign the original artist's album to a cover — e.g. a YouTube piano cover of a Magnetic Fields song should NOT get album "69 Love Songs".
- **year**: The release year of THIS specific video/version (integer or null). For covers, remixes, or re-recordings use the year the cover/remix was released — NOT the year of the original song. If an upload date is provided in the context, use its year.
- **genres**: Up to 5 relevant genres for this artist/song (list of strings)
- **plot**: A detailed, informative Kodi-style description, up to {max_plot_length} characters. Structure:
  "[Artist] performs '[Title]' from their [year] album [Album]. [Detailed description of the music video's visual style, themes, narrative, and production context.]"
  For remixes, mashups, or meme content, adapt the format: "[Creator] presents a [remix/mashup/edit] of [original content]. [Detailed description.]"
  Use ALL available context — especially the platform video description, channel info, and upload date — to write a rich, multi-sentence description.
  Include production credits (director, label) if available in the platform description.
  Do NOT compress into a single sentence — aim for 2-4 sentences with real detail about the video and song.
  If you cannot describe the video's visuals, focus on the song's musical style, significance, and release context.
- **director**: Music video director if known (string or null)
- **studio**: Record label (string or null)
- **tags**: Up to 10 descriptive tags (e.g. "live performance", "animated", "black and white", "concert footage")

**Formatting rules:**
- **Featuring credits:** Always use the format "Artist1 feat. Artist2" (never "ft.", "featuring", or "&" for featured artists)
- **Title suffixes:** Preserve meaningful version indicators like "(Remix)", "(Live)", "(Acoustic)", "(Long Version)", "(Radio Edit)", "(feat. X)". Strip noise suffixes like "(Official Video)", "[HD]", "(Music Video)", "(Lyric Video)".
- **Artist name style:** ALWAYS preserve the artist's exact official stylization (e.g. "CHVRCHES" not "Chvrches", "deadmau5" not "Deadmau5", "Florence + the Machine" not "Florence and the Machine", "SLACKCiRCUS" not "Slackcircus"). When uncertain, prefer the MusicBrainz canonical form. Never apply case normalization to artist names.

### Step 3 — Confidence Assessment
Rate your confidence for EACH field (0.0–1.0):
- **1.0**: Certain — well-known song, no ambiguity
- **0.8–0.9**: High confidence — very likely correct
- **0.5–0.7**: Moderate — educated guess, may need review
- **< 0.5**: Low — speculative, flag for user review

## RESPONSE FORMAT
Respond in **valid JSON only** — no markdown fences, no commentary:
{{
  "identity": {{
    "candidate_artist": "the actual performing artist",
    "candidate_title": "the actual song title",
    "evidence": {{
      "filename_match": true,
      "url_match": true,
      "metadata_consistent": true,
      "known_song": true
    }}
  }},
  "mismatch": {{
    "is_mismatch": false,
    "severity": "none",
    "reasons": []
  }},
  "proposed": {{
    "artist": "Corrected Artist Name",
    "title": "Corrected Song Title",
    "album": "Album Name or null",
    "year": 2024,
    "genres": ["Rock", "Alternative"],
    "plot": "Kodi-formatted description text",
    "director": "Director Name or null",
    "studio": "Label Name or null",
    "tags": ["tag1", "tag2"]
  }},
  "confidence": {{
    "artist": 0.95,
    "title": 0.95,
    "album": 0.8,
    "year": 0.9,
    "genres": 0.85,
    "plot": 0.7,
    "director": 0.5,
    "studio": 0.6,
    "tags": 0.7
  }},
  "change_summary": "Brief human-readable summary of what was changed and why"
}}"""

SYSTEM_PROMPT = (
    "You are a music video metadata expert. "
    "Verify and correct scraped metadata using your knowledge of music. "
    "Respond in valid JSON only — no markdown, no code fences, no extra text."
)


# ---------------------------------------------------------------------------
# Review Description Only — Prompt Template
# ---------------------------------------------------------------------------

REVIEW_DESCRIPTION_PROMPT = """You are a **Music Video Description Editor** for a Kodi media library.

## CONTEXT

**Current metadata:**
- Artist: {artist}
- Title: {title}
- Album: {album}
- Year: {year}
- Genres: {genres}

**File & source information:**
{file_context}

{platform_context}
**Current description:**
{plot}

## YOUR TASK

Review and rewrite the description above to make it suitable for a Kodi media library.

### Guidelines:
- Maximum length: {max_plot_length} characters
- Structure: "[Artist] performs '[Title]' from their [year] album [Album]. [Brief description of the music video's visual style, themes, or narrative.]"
- If the current description is already good and within the length limit, keep it as-is
- If it's too long, condense it while preserving key information
- If it's empty or generic, write a new concise description based on what you know about this song
- Remove any promotional language, links, or excessive detail
- Keep the tone encyclopedic and concise

## RESPONSE FORMAT
Respond in **valid JSON only** — no markdown fences, no commentary:
{{
  "identity": {{
    "candidate_artist": "{artist}",
    "candidate_title": "{title}",
    "evidence": {{
      "filename_match": true,
      "url_match": true,
      "metadata_consistent": true,
      "known_song": true
    }}
  }},
  "mismatch": {{
    "is_mismatch": false,
    "severity": "none",
    "reasons": []
  }},
  "proposed": {{
    "artist": "{artist}",
    "title": "{title}",
    "album": "{album}",
    "year": {year},
    "genres": [{genres}],
    "plot": "Your reviewed/rewritten description here",
    "director": null,
    "studio": null,
    "tags": []
  }},
  "confidence": {{
    "artist": 1.0,
    "title": 1.0,
    "album": 1.0,
    "year": 1.0,
    "genres": 1.0,
    "plot": 0.9,
    "director": 0.0,
    "studio": 0.0,
    "tags": 0.0
  }},
  "change_summary": "Reviewed and edited description to fit Kodi library format"
}}"""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_metadata_enrichment_prompt(
    scraped: Dict[str, Any],
    *,
    video_filename: Optional[str] = None,
    source_url: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    channel_name: Optional[str] = None,
    upload_date: Optional[str] = None,
    mismatch_signals: Optional[List[Dict[str, Any]]] = None,
    fingerprint_hint: Optional[Dict[str, Any]] = None,
    max_plot_length: int = 2000,
    review_description_only: bool = False,
    platform_description: Optional[str] = None,
    platform_tags: Optional[List[str]] = None,
    custom_enrichment_template: Optional[str] = None,
    custom_review_template: Optional[str] = None,
) -> str:
    """
    Build a complete enrichment prompt with all available context.

    This function is provider-agnostic — returns a plain text prompt string
    that any provider can send to its LLM.

    Args:
        scraped: Current metadata dict (artist, title, album, year, genres, plot)
        video_filename: Original video filename for context clues
        source_url: Video source URL (YouTube, Vimeo, etc.)
        duration_seconds: Video duration in seconds
        channel_name: YouTube/Vimeo channel name if available
        mismatch_signals: Pre-AI mismatch detector signals
        fingerprint_hint: Audio fingerprint match data (top match)
        max_plot_length: Maximum plot/description length

    Returns:
        Formatted prompt string ready to send to the AI.
    """
    # ── File & source context block ──
    file_lines: list[str] = []
    if video_filename:
        file_lines.append(f"- Filename: {video_filename}")
    if source_url:
        file_lines.append(f"- Source URL: {source_url}")
    if channel_name:
        file_lines.append(f"- Channel: {channel_name}")
    if duration_seconds is not None:
        mins, secs = divmod(int(duration_seconds), 60)
        file_lines.append(f"- Duration: {mins}:{secs:02d} ({duration_seconds:.0f}s)")
    if fingerprint_hint:
        fp_artist = fingerprint_hint.get("artist", "")
        fp_title = fingerprint_hint.get("title", "")
        fp_conf = fingerprint_hint.get("confidence", 0)
        if fp_artist or fp_title:
            file_lines.append(
                f"- Audio fingerprint match: {fp_artist} — {fp_title} "
                f"(confidence: {fp_conf:.0%})"
            )
    if upload_date:
        file_lines.append(f"- Upload date: {upload_date}")
    if platform_tags:
        file_lines.append(f"- Platform tags: {', '.join(platform_tags[:20])}")

    file_context = "\n".join(file_lines) if file_lines else "- No additional file/source information available"

    # ── Platform description block (YouTube video description, etc.) ──
    platform_context = ""
    if platform_description:
        # Truncate very long descriptions to avoid blowing up the prompt
        desc_trimmed = platform_description[:1500]
        if len(platform_description) > 1500:
            desc_trimmed += "..."
        platform_context = (
            "\n**Platform video description** (from the original upload — treat as a primary source of context):\n"
            f"```\n{desc_trimmed}\n```\n"
        )

    # ── Mismatch section ──
    mismatch_section = ""
    if mismatch_signals:
        signal_lines = []
        for sig in mismatch_signals:
            name = sig.get("name", "unknown")
            score = sig.get("score", 0)
            details = sig.get("details", "")
            signal_lines.append(f"  - {name}: score={score:.2f}" + (f" — {details}" if details else ""))
        if signal_lines:
            mismatch_section = (
                "**Pre-analysis mismatch signals** (automated heuristic checks flagged potential issues):\n"
                + "\n".join(signal_lines)
                + "\n\nPay special attention to these flags — the metadata may be wrong or from a different song entirely."
            )

    # ── Format genres ──
    genres_str = ", ".join(scraped.get("genres", [])) or "Unknown"

    # ── Build final prompt ──
    if review_description_only:
        # Focused prompt: only review/rewrite the description
        current_plot = scraped.get("plot") or "None provided"
        template = custom_review_template or REVIEW_DESCRIPTION_PROMPT
        prompt = template.format(
            artist=scraped.get("artist") or "Unknown",
            title=scraped.get("title") or "Unknown",
            album=scraped.get("album") or "Unknown",
            year=scraped.get("year") or "Unknown",
            genres=genres_str,
            plot=current_plot,
            max_plot_length=max_plot_length,
            file_context=file_context,
            platform_context=platform_context,
        )
    else:
        template = custom_enrichment_template or SMART_ENRICHMENT_PROMPT
        prompt = template.format(
            artist=scraped.get("artist") or "Unknown",
            title=scraped.get("title") or "Unknown",
            album=scraped.get("album") or "Unknown",
            year=scraped.get("year") or "Unknown",
            genres=genres_str,
            plot=scraped.get("plot") or "None provided",
            file_context=file_context,
            platform_context=platform_context,
            mismatch_section=mismatch_section,
            max_plot_length=max_plot_length,
        )

    return prompt
