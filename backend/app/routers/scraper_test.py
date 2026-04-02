"""
Scraper Test API â€” Run scraping pipelines against a URL without downloading.

Returns metadata results with provenance tracking so users can see
exactly what each scraping mode produces.
"""
import datetime
import json
import logging
import os
import re
import time
from typing import Any, Dict, Generator, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.services.url_utils import identify_provider, canonicalize_url, is_playlist_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scraper-test", tags=["Scraper Test"])


# â”€â”€ Request / Response schemas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ScraperTestRequest(BaseModel):
    url: str = Field(..., description="YouTube or Vimeo video URL (no playlists)")
    artist_override: Optional[str] = Field(None, description="Override artist")
    title_override: Optional[str] = Field(None, description="Override title")
    scrape_wikipedia: bool = Field(False)
    scrape_musicbrainz: bool = Field(False)
    wikipedia_url: Optional[str] = Field(None, description="Direct Wikipedia URL (bypass search)")
    musicbrainz_url: Optional[str] = Field(None, description="Direct MusicBrainz recording URL (bypass search)")
    ai_auto: bool = Field(False, description="AI Auto mode (AI + scrapers)")
    ai_only: bool = Field(False, description="AI Only mode (no external scrapers)")


class ImportTestRequest(BaseModel):
    """Request model for import-mode scraper test."""
    directory: str = Field(..., description="Directory containing the video file")
    file_name: Optional[str] = Field(None, description="Specific video file name (required if multiple videos in directory)")
    artist_override: Optional[str] = Field(None, description="Override artist")
    title_override: Optional[str] = Field(None, description="Override title")
    scrape_wikipedia: bool = Field(False)
    scrape_musicbrainz: bool = Field(False)
    wikipedia_url: Optional[str] = Field(None, description="Direct Wikipedia URL (bypass search)")
    musicbrainz_url: Optional[str] = Field(None, description="Direct MusicBrainz recording URL (bypass search)")
    ai_auto: bool = Field(False, description="AI Auto mode (AI + scrapers)")
    ai_only: bool = Field(False, description="AI Only mode (no external scrapers)")


class DirectoryScanResult(BaseModel):
    """Result of scanning a directory for video files."""
    directory: str
    video_files: List[str] = []
    nfo_files: List[str] = []
    has_multiple: bool = False


class ProvenanceField(BaseModel):
    """A metadata field with its source provenance."""
    value: Any = None
    source: str = "none"  # e.g. "yt-dlp", "parsed", "musicbrainz", "wikipedia", "ai", "ai_review", "override"


class ArtworkCandidate(BaseModel):
    """An artwork URL found by a scraper."""
    url: str
    source: str  # "wikipedia", "yt-dlp", "musicbrainz_coverart"
    art_type: str = "poster"  # "artist", "album", "poster", "fanart"
    applied: bool = False  # Whether this would be used as the final artwork


class BeforeAfterField(BaseModel):
    """Shows a field's value before and after AI modification."""
    field: str
    before: Any = None
    after: Any = None
    source: str = ""  # Which AI stage changed it


class ScraperTestResult(BaseModel):
    # Identity
    url: str
    canonical_url: str
    provider: str
    video_id: str

    # yt-dlp raw metadata
    ytdlp_title: Optional[str] = None
    ytdlp_uploader: Optional[str] = None
    ytdlp_channel: Optional[str] = None
    ytdlp_artist: Optional[str] = None
    ytdlp_track: Optional[str] = None
    ytdlp_album: Optional[str] = None
    ytdlp_duration: Optional[float] = None
    ytdlp_upload_date: Optional[str] = None
    ytdlp_thumbnail: Optional[str] = None
    ytdlp_description: Optional[str] = None
    ytdlp_tags: List[str] = []

    # Parsed artist/title (from yt-dlp title parsing)
    parsed_artist: str = ""
    parsed_title: str = ""

    # Final resolved metadata with provenance
    artist: ProvenanceField = ProvenanceField()
    title: ProvenanceField = ProvenanceField()
    album: ProvenanceField = ProvenanceField()
    year: ProvenanceField = ProvenanceField()
    genres: ProvenanceField = ProvenanceField()
    plot: ProvenanceField = ProvenanceField()
    image_url: ProvenanceField = ProvenanceField()
    mb_artist_id: ProvenanceField = ProvenanceField()
    mb_recording_id: ProvenanceField = ProvenanceField()
    mb_release_id: ProvenanceField = ProvenanceField()
    mb_release_group_id: ProvenanceField = ProvenanceField()
    imdb_url: ProvenanceField = ProvenanceField()

    # Source URLs retrieved
    source_urls: Dict[str, str] = {}  # e.g. {"wikipedia": "https://...", "musicbrainz": "https://...", "imdb": "https://..."}

    # Artwork candidates from all sources
    artwork_candidates: List[ArtworkCandidate] = []

    # Before/after AI changes
    ai_changes: List[BeforeAfterField] = []

    # Scraping diagnostics
    scraper_sources_used: List[str] = []
    pipeline_log: List[str] = []
    pipeline_failures: List[Dict[str, str]] = []

    # Mode that was tested
    mode: str = ""

    # AI details (if applicable)
    ai_source_resolution: Optional[Dict[str, Any]] = None
    ai_final_review: Optional[Dict[str, Any]] = None

    # Output file path (relative to project root)
    output_file: Optional[str] = None

    # Import-mode fields (only populated in import mode)
    import_directory: Optional[str] = None
    import_file: Optional[str] = None
    import_identity_source: Optional[str] = None  # "nfo" or "filename"
    import_nfo_found: Optional[bool] = None
    import_youtube_match: Optional[Dict[str, Any]] = None
    import_quality: Optional[Dict[str, Any]] = None


# â”€â”€ Per-test output file writer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_SCRAPER_TEST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "logs", "scraper_tests",
)


def _safe_filename(text: str) -> str:
    """Sanitize a string for use as a filename component."""
    text = re.sub(r'[<>:"/\\|?*]', '_', text)
    text = re.sub(r'\s+', '_', text).strip('_')
    return text[:80] or "unknown"


def _write_scraper_test_file(
    *,
    req,
    mode: str,
    provider: str,
    video_id: str,
    canonical: str,
    ytdlp_meta: Dict[str, Any],
    parsed_artist: str,
    parsed_title: str,
    artist: str,
    artist_source: str,
    title: str,
    title_source: str,
    metadata: Dict[str, Any],
    sources_used: List[str],
    source_urls: Dict[str, str],
    artwork_candidates: List,
    ai_changes: List,
    pre_logs: List[str],
    logs: List[str],
) -> Optional[str]:
    """Write a detailed text trace file for a scraper test run.

    Returns the relative path to the file (from project root), or None on error.
    """
    try:
        os.makedirs(_SCRAPER_TEST_DIR, exist_ok=True)

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_artist = _safe_filename(artist)
        safe_title = _safe_filename(title)
        filename = f"{stamp}_{safe_artist}_{safe_title}.txt"
        filepath = os.path.join(_SCRAPER_TEST_DIR, filename)

        lines: List[str] = []

        def section(heading: str):
            lines.append("")
            lines.append("=" * 72)
            lines.append(f"  {heading}")
            lines.append("=" * 72)
            lines.append("")

        def kv(key: str, val: Any):
            lines.append(f"  {key:30s}: {val}")

        # â”€â”€ Header â”€â”€
        lines.append("SCRAPER TEST TRACE")
        lines.append(f"Generated: {datetime.datetime.now().isoformat()}")
        lines.append(f"File:      {filename}")

        section("1. REQUEST")
        kv("URL", req.url)
        kv("Canonical URL", canonical)
        kv("Provider", provider)
        kv("Video ID", video_id)
        kv("Mode", mode)
        kv("Artist Override", req.artist_override or "(none)")
        kv("Title Override", req.title_override or "(none)")
        kv("Scrape Wikipedia", req.scrape_wikipedia)
        kv("Scrape MusicBrainz", req.scrape_musicbrainz)
        kv("AI Auto", req.ai_auto)
        kv("AI Only", req.ai_only)

        section("2. YT-DLP RAW METADATA")
        kv("Title", ytdlp_meta.get("title", "(none)"))
        kv("Artist", ytdlp_meta.get("artist", "(none)"))
        kv("Track", ytdlp_meta.get("track", "(none)"))
        kv("Album", ytdlp_meta.get("album", "(none)"))
        kv("Uploader", ytdlp_meta.get("uploader", "(none)"))
        kv("Channel", ytdlp_meta.get("channel", "(none)"))
        kv("Duration", ytdlp_meta.get("duration", "(none)"))
        kv("Upload Date", ytdlp_meta.get("upload_date", "(none)"))
        kv("Thumbnail", ytdlp_meta.get("thumbnail", "(none)"))
        tags = ytdlp_meta.get("tags", [])
        kv("Tags", f"{len(tags)} tag(s)")
        if tags:
            for t in tags[:30]:
                lines.append(f"    - {t}")
        desc = ytdlp_meta.get("description", "")
        kv("Description Length", f"{len(desc)} chars")
        if desc:
            lines.append(f"    (first 500 chars):")
            lines.append(f"    {desc[:500]}")

        section("3. ARTIST / TITLE RESOLUTION")
        kv("Parsed Artist", parsed_artist or "(none)")
        kv("Parsed Title", parsed_title or "(none)")
        kv("Final Artist", f"{artist}  [source: {artist_source}]")
        kv("Final Title", f"{title}  [source: {title_source}]")

        section("4. AI SOURCE RESOLUTION")
        ai_sr = metadata.get("ai_source_resolution")
        if ai_sr and isinstance(ai_sr, dict):
            identity = ai_sr.get("identity", {})
            sources_ai = ai_sr.get("sources", {})
            kv("AI Corrected Artist", identity.get("artist", "(none)"))
            kv("AI Corrected Title", identity.get("title", "(none)"))
            kv("AI Genre", identity.get("genre", "(none)"))
            kv("AI Type", identity.get("type", "(none)"))
            lines.append("")
            lines.append("  AI Discovered Sources:")
            for src_key, src_val in sorted(sources_ai.items()):
                lines.append(f"    {src_key:30s}: {src_val}")
            # Prompt and response if present
            if ai_sr.get("prompt"):
                lines.append("")
                lines.append("  AI Prompt (first 1000 chars):")
                lines.append(f"    {str(ai_sr['prompt'])[:1000]}")
            if ai_sr.get("raw_response"):
                lines.append("")
                lines.append("  AI Response (first 2000 chars):")
                lines.append(f"    {str(ai_sr['raw_response'])[:2000]}")
            if ai_sr.get("error"):
                lines.append("")
                lines.append(f"  AI ERROR: {ai_sr['error']}")
        else:
            lines.append("  (skipped or not available)")

        section("5. MUSICBRAINZ RESOLUTION")
        kv("MB Artist ID", metadata.get("mb_artist_id", "(none)"))
        kv("MB Recording ID", metadata.get("mb_recording_id", "(none)"))
        kv("MB Release ID", metadata.get("mb_release_id", "(none)"))
        kv("MB Release Group ID", metadata.get("mb_release_group_id", "(none)"))
        kv("MB Album", metadata.get("album", "(none)"))
        kv("MB Year", metadata.get("year", "(none)"))
        kv("MB Album Release ID", metadata.get("mb_album_release_id", "(none)"))
        kv("MB Album Release Group ID", metadata.get("mb_album_release_group_id", "(none)"))
        # Show MB source URL if available
        mb_url = source_urls.get("musicbrainz") or source_urls.get("musicbrainz_release")
        kv("MB Source URL", mb_url or "(none)")
        mb_artist_url = source_urls.get("musicbrainz_artist")
        kv("MB Artist URL", mb_artist_url or "(none)")

        section("6. WIKIPEDIA RESOLUTION")
        kv("Image URL (wiki)", metadata.get("image_url") if "wikipedia" in str(sources_used) else "(none)")
        kv("Plot", f"{len(str(metadata.get('plot', '')))} chars" if metadata.get("plot") else "(none)")
        kv("Genres (wiki)", metadata.get("genres") if "wikipedia" in str(sources_used) else "(none)")
        wiki_url = source_urls.get("wikipedia")
        kv("Wikipedia URL", wiki_url or "(none)")

        section("7. IMDB RESOLUTION")
        kv("IMDB URL", metadata.get("imdb_url", "(none)"))

        section("8. AI FINAL REVIEW")
        ai_fr = metadata.get("ai_final_review")
        if ai_fr and isinstance(ai_fr, dict):
            corrections = ai_fr.get("corrections", [])
            kv("Corrections Count", len(corrections))
            for c in corrections:
                lines.append(f"    - {c}")
            if ai_fr.get("prompt"):
                lines.append("")
                lines.append("  AI Review Prompt (first 1000 chars):")
                lines.append(f"    {str(ai_fr['prompt'])[:1000]}")
            if ai_fr.get("raw_response"):
                lines.append("")
                lines.append("  AI Review Response (first 2000 chars):")
                lines.append(f"    {str(ai_fr['raw_response'])[:2000]}")
            if ai_fr.get("error"):
                lines.append(f"  AI REVIEW ERROR: {ai_fr['error']}")
        else:
            lines.append("  (skipped or not available)")

        section("9. AI BEFORE / AFTER CHANGES")
        if ai_changes:
            for change in ai_changes:
                field = change.field if hasattr(change, 'field') else change.get('field', '?')
                before = change.before if hasattr(change, 'before') else change.get('before')
                after = change.after if hasattr(change, 'after') else change.get('after')
                src = change.source if hasattr(change, 'source') else change.get('source', '')
                lines.append(f"  {field}:")
                lines.append(f"    Before: {before}")
                lines.append(f"    After:  {after}")
                lines.append(f"    Source: {src}")
                lines.append("")
        else:
            lines.append("  (no AI changes)")

        section("10. FINAL RESOLVED METADATA")
        kv("Artist", metadata.get("artist", "(none)"))
        kv("Title", metadata.get("title", "(none)"))
        kv("Album", metadata.get("album", "(none)"))
        kv("Year", metadata.get("year", "(none)"))
        kv("Genres", metadata.get("genres", "(none)"))
        kv("Image URL", metadata.get("image_url", "(none)"))
        kv("Plot Length", f"{len(str(metadata.get('plot', '')))} chars" if metadata.get("plot") else "(none)")
        kv("Scraper Sources Used", sources_used)

        section("11. SOURCE URLS")
        for url_key, url_val in sorted(source_urls.items()):
            kv(url_key, url_val)

        section("12. ARTWORK CANDIDATES")
        if artwork_candidates:
            for i, cand in enumerate(artwork_candidates, 1):
                url = cand.url if hasattr(cand, 'url') else cand.get('url', '?')
                source = cand.source if hasattr(cand, 'source') else cand.get('source', '?')
                art_type = cand.art_type if hasattr(cand, 'art_type') else cand.get('art_type', '?')
                applied = cand.applied if hasattr(cand, 'applied') else cand.get('applied', False)
                status = " *** APPLIED ***" if applied else ""
                lines.append(f"  [{i}] {source} / {art_type}{status}")
                lines.append(f"      {url}")
                lines.append("")
        else:
            lines.append("  (no artwork candidates)")

        section("13. PIPELINE FAILURES")
        failures = metadata.get("pipeline_failures", [])
        if failures:
            for f in failures:
                lines.append(f"  [{f.get('code', '?')}] {f.get('description', '?')}")
                lines.append("")
        else:
            lines.append("  (no failures)")

        section("14. FULL PIPELINE LOG")
        full_log = pre_logs + metadata.get("pipeline_log", [])
        for entry in full_log:
            lines.append(f"  {entry}")

        # Write file
        content = "\n".join(lines) + "\n"
        with open(filepath, "w", encoding="utf-8") as fh:
            fh.write(content)

        rel_path = os.path.relpath(filepath, os.path.dirname(os.path.dirname(_SCRAPER_TEST_DIR)))
        logger.info(f"Scraper test trace written to: {filepath}")
        return rel_path

    except Exception as e:
        logger.warning(f"Failed to write scraper test trace file: {e}")
        return None


# â”€â”€ Endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("/run", response_model=ScraperTestResult)
def run_scraper_test(req: ScraperTestRequest, db: Session = Depends(get_db)):
    """
    Run the scraping pipeline against a URL without downloading the video.

    Extracts yt-dlp metadata (no download), then runs the selected scraping
    mode and returns the full result with provenance for each field.
    """
    # â”€â”€ Validate: reject AI modes without a provider â”€â”€
    if req.ai_auto or req.ai_only:
        from app.models import AppSetting
        row = db.query(AppSetting).filter(
            AppSetting.key == "ai_provider",
            AppSetting.user_id.is_(None),
        ).first()
        if (row.value if row else "none") == "none":
            raise HTTPException(
                status_code=400,
                detail="AI mode requires an AI provider. Go to Settings and select an AI provider (OpenAI, Gemini, Claude, or Local) before using AI Auto or AI Only.",
            )

    # â”€â”€ Validate: reject playlists â”€â”€
    if is_playlist_url(req.url):
        raise HTTPException(status_code=400, detail="Playlists are not supported â€” please provide a single video URL.")

    # â”€â”€ Identify provider and video ID â”€â”€
    try:
        provider, video_id = identify_provider(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    canonical = canonicalize_url(provider, video_id)

    # â”€â”€ Pre-pipeline trace log (captures all decisions before unified_metadata runs) â”€â”€
    pre_logs: List[str] = []
    pre_logs.append(f"[scraper-test] URL: {req.url}")
    pre_logs.append(f"[scraper-test] Provider: {provider.value}, Video ID: {video_id}")
    pre_logs.append(f"[scraper-test] Canonical URL: {canonical}")

    # â”€â”€ Determine mode label â”€â”€
    if req.ai_only:
        mode = "AI Only"
    elif req.ai_auto:
        mode = "AI Auto"
    elif req.scrape_wikipedia and req.scrape_musicbrainz:
        mode = "Wiki + MB"
    elif req.scrape_wikipedia:
        mode = "Wiki Only"
    elif req.scrape_musicbrainz:
        mode = "MB Only"
    else:
        mode = "No Scraping"
    pre_logs.append(f"[scraper-test] Mode: {mode}")

    # â”€â”€ Extract yt-dlp metadata (no download) â”€â”€
    from app.services.downloader import get_available_formats, extract_metadata_from_ytdlp

    pre_logs.append("[scraper-test] Fetching yt-dlp metadata (no download)...")
    try:
        _formats, info_dict = get_available_formats(req.url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch video metadata: {e}")

    ytdlp_meta = extract_metadata_from_ytdlp(info_dict)
    pre_logs.append(f"[scraper-test] yt-dlp title: {ytdlp_meta.get('title', '(none)')}")
    pre_logs.append(f"[scraper-test] yt-dlp artist: {ytdlp_meta.get('artist', '(none)')}")
    pre_logs.append(f"[scraper-test] yt-dlp track: {ytdlp_meta.get('track', '(none)')}")
    pre_logs.append(f"[scraper-test] yt-dlp album: {ytdlp_meta.get('album', '(none)')}")
    pre_logs.append(f"[scraper-test] yt-dlp uploader: {ytdlp_meta.get('uploader', '(none)')}")
    pre_logs.append(f"[scraper-test] yt-dlp channel: {ytdlp_meta.get('channel', '(none)')}")
    pre_logs.append(f"[scraper-test] yt-dlp duration: {ytdlp_meta.get('duration', '(none)')}")
    pre_logs.append(f"[scraper-test] yt-dlp upload_date: {ytdlp_meta.get('upload_date', '(none)')}")
    _tag_count = len(ytdlp_meta.get("tags", []))
    pre_logs.append(f"[scraper-test] yt-dlp tags: {_tag_count} tag(s)")
    _desc_len = len(ytdlp_meta.get("description", ""))
    pre_logs.append(f"[scraper-test] yt-dlp description: {_desc_len} chars")

    # â”€â”€ Parse artist / title from yt-dlp metadata â”€â”€
    from app.scraper.metadata_resolver import (
        extract_artist_title, clean_title,
        _detect_artist_title_swap, _clean_ytdlp_artist,
        extract_featuring_credit,
    )

    raw_title = ytdlp_meta.get("title", "")
    parsed_artist, parsed_title = extract_artist_title(raw_title)
    pre_logs.append(f"[scraper-test] Parsed from title: artist='{parsed_artist}', title='{parsed_title}'")

    uploader = ytdlp_meta.get("uploader", "") or ""
    channel = ytdlp_meta.get("channel", "") or ""

    # Swap detection: cross-reference against uploader/channel
    _orig_artist, _orig_title = parsed_artist, parsed_title
    parsed_artist, parsed_title = _detect_artist_title_swap(
        parsed_artist, parsed_title, uploader, channel,
    )
    if (parsed_artist, parsed_title) != (_orig_artist, _orig_title):
        pre_logs.append(f"[scraper-test] Swap detected: artist='{parsed_artist}', title='{parsed_title}'")

    # Validate yt-dlp artist field (reject channel names)
    yt_artist = _clean_ytdlp_artist(
        ytdlp_meta.get("artist", ""), uploader, channel,
    )

    # Apply same logic as _determine_artist_title_from_ytdlp
    if req.artist_override:
        artist = req.artist_override
        artist_source = "override"
        pre_logs.append(f"[scraper-test] Artist: '{artist}' (source: override)")
    elif yt_artist:
        artist = clean_title(yt_artist)
        artist_source = "yt-dlp"
        pre_logs.append(f"[scraper-test] Artist: '{artist}' (source: yt-dlp artist field, validated)")
    elif parsed_artist:
        artist = parsed_artist
        artist_source = "parsed"
        pre_logs.append(f"[scraper-test] Artist: '{artist}' (source: parsed from title)")
    else:
        artist = uploader or channel or ""
        artist_source = "yt-dlp"
        pre_logs.append(f"[scraper-test] Artist: '{artist}' (source: yt-dlp uploader/channel fallback)")

    if req.title_override:
        title = req.title_override
        title_source = "override"
        pre_logs.append(f"[scraper-test] Title: '{title}' (source: override)")
    elif ytdlp_meta.get("track"):
        title = clean_title(ytdlp_meta["track"])
        title_source = "yt-dlp"
        pre_logs.append(f"[scraper-test] Title: '{title}' (source: yt-dlp track field)")
    elif parsed_title:
        title = parsed_title
        title_source = "parsed"
        pre_logs.append(f"[scraper-test] Title: '{title}' (source: parsed from title)")
    else:
        title = clean_title(raw_title) if raw_title else ""
        title_source = "parsed"
        pre_logs.append(f"[scraper-test] Title: '{title}' (source: cleaned raw title)")

    # Strip duplicated artist prefix from title
    if artist and title:
        _title_before_strip = title
        for sep in [" - ", " — ", " – ", " : "]:
            prefix = artist + sep
            if title.lower().startswith(prefix.lower()):
                title = title[len(prefix):].strip()
                break
        if title != _title_before_strip:
            pre_logs.append(f"[scraper-test] Stripped artist prefix from title: '{_title_before_strip}' â†’ '{title}'")

    # Extract featuring credits from title and merge into artist
    if title:
        _title_before_feat = title
        title, feat_credit = extract_featuring_credit(title)
        if feat_credit and artist and feat_credit.lower() not in artist.lower():
            artist = f"{artist} ft. {feat_credit}"
            pre_logs.append(f"[scraper-test] Featuring credit extracted: '{feat_credit}' merged into artist")

    artist = artist or "Unknown Artist"
    title = title or "Unknown Title"

    # â”€â”€ Compute skip flags â”€â”€
    if req.ai_only:
        skip_wiki = True
        skip_mb = True
        skip_ai = False
    elif req.ai_auto:
        skip_wiki = False
        skip_mb = False
        skip_ai = False
    else:
        skip_wiki = not req.scrape_wikipedia
        skip_mb = not req.scrape_musicbrainz
        skip_ai = True
    pre_logs.append(f"[scraper-test] Skip flags: wiki={skip_wiki}, mb={skip_mb}, ai={skip_ai}")
    pre_logs.append(f"[scraper-test] Entering unified metadata pipeline with: artist='{artist}', title='{title}'")

    # â”€â”€ Run unified metadata resolution â”€â”€
    from app.scraper.unified_metadata import resolve_metadata_unified

    logs: List[str] = []

    metadata = resolve_metadata_unified(
        artist=artist,
        title=title,
        db=db,
        source_url=canonical,
        platform_title=ytdlp_meta.get("title", ""),
        channel_name=ytdlp_meta.get("channel") or ytdlp_meta.get("uploader") or "",
        platform_description=ytdlp_meta.get("description", ""),
        platform_tags=ytdlp_meta.get("tags"),
        upload_date=ytdlp_meta.get("upload_date", ""),
        duration_seconds=ytdlp_meta.get("duration"),
        ytdlp_metadata=info_dict,
        skip_wikipedia=skip_wiki,
        skip_musicbrainz=skip_mb,
        skip_ai=skip_ai,
        wikipedia_url=req.wikipedia_url if not skip_wiki else None,
        musicbrainz_url=req.musicbrainz_url if not skip_mb else None,
        log_callback=lambda msg: logs.append(msg),
        _test_mode=True,
    )

    # â”€â”€ Determine provenance for each resolved field â”€â”€
    sources_used = metadata.get("scraper_sources_used", [])
    has_mb = any(s.startswith("musicbrainz:") for s in sources_used)
    has_wiki = any(s.startswith("wikipedia:") for s in sources_used)
    has_imdb = any(s.startswith("imdb:") for s in sources_used)
    has_ai_source = metadata.get("ai_source_resolution") is not None
    has_ai_review = metadata.get("ai_final_review") is not None
    ai_review_changes = []
    if has_ai_review and metadata["ai_final_review"]:
        ai_review_changes = [c.split(":")[0] for c in metadata["ai_final_review"].get("corrections", [])]

    def _prov(field_name: str, val: Any, default_source: str) -> ProvenanceField:
        """Determine the most accurate provenance for a field."""
        if val is None or val == "" or val == []:
            return ProvenanceField(value=val, source="none")

        # AI final review changed this field?
        if field_name in ai_review_changes:
            return ProvenanceField(value=val, source="ai_review")

        # AI source resolution provided this?
        if has_ai_source and not skip_ai:
            ai_sr = metadata.get("ai_source_resolution", {})
            if ai_sr and isinstance(ai_sr, dict):
                identity = ai_sr.get("identity", {})
                if identity.get(field_name) and str(identity[field_name]) == str(val):
                    return ProvenanceField(value=val, source="ai")

        return ProvenanceField(value=val, source=default_source)

    # Artist/title provenance
    resolved_artist = metadata.get("artist", artist)
    resolved_title = metadata.get("title", title)

    # What scraper provided album/year/genres/plot?
    album_source = "musicbrainz" if has_mb and metadata.get("album") else ("ai" if has_ai_source else "none")
    year_source = "musicbrainz" if has_mb and metadata.get("year") else ("ai" if has_ai_source else "none")
    genre_source = "wikipedia" if has_wiki and metadata.get("genres") else ("musicbrainz" if has_mb and metadata.get("genres") else ("ai" if has_ai_source else "none"))
    plot_source = "wikipedia" if has_wiki and metadata.get("plot") else ("ai" if has_ai_source else "none")
    image_source = "wikipedia" if has_wiki and metadata.get("image_url") else ("musicbrainz" if has_mb and metadata.get("image_url") else ("ai" if has_ai_source else "none"))
    mb_source = "musicbrainz" if has_mb else "none"
    imdb_source = "imdb" if has_imdb else ("ai" if has_ai_source and metadata.get("imdb_url") else "none")

    # â”€â”€ Build artwork candidates list â”€â”€
    artwork_candidates = []
    # yt-dlp thumbnail (always available, but not used as library artwork)
    if ytdlp_meta.get("thumbnail"):
        artwork_candidates.append(ArtworkCandidate(
            url=ytdlp_meta["thumbnail"],
            source="yt-dlp",
            art_type="poster",
            applied=False,
        ))
    # Scraper-found artwork candidates (from unified pipeline â€” wikipedia infobox)
    for cand in metadata.get("_artwork_candidates", []):
        artwork_candidates.append(ArtworkCandidate(
            url=cand["url"],
            source=cand.get("source", "unknown"),
            art_type=cand.get("art_type", "poster"),
            applied=cand.get("applied", False),
        ))

    # â”€â”€ Wikipedia album/artist URLs from unified pipeline â”€â”€
    # unified_metadata now discovers these via cross-links and search â€”
    # read them from _source_urls instead of re-fetching.
    _wiki_album_url: Optional[str] = metadata.get("_source_urls", {}).get("wikipedia_album")
    _wiki_artist_url: Optional[str] = metadata.get("_source_urls", {}).get("wikipedia_artist")
    if _wiki_album_url:
        pre_logs.append(f"[scraper-test] Wikipedia album page (from pipeline): {_wiki_album_url}")
    if _wiki_artist_url:
        pre_logs.append(f"[scraper-test] Wikipedia artist page (from pipeline): {_wiki_artist_url}")

    # â”€â”€ Fetch artist artwork from dedicated scrapers â”€â”€
    resolved_artist_name = metadata.get("artist", artist)
    resolved_album_name = metadata.get("album")
    resolved_mb_artist = metadata.get("mb_artist_id")
    pre_logs.append(f"[scraper-test] Fetching dedicated artist artwork for: '{resolved_artist_name}' (mb_artist_id={resolved_mb_artist})")

    try:
        from app.scraper.artist_album_scraper import get_artist_artwork
        artist_art = get_artist_artwork(resolved_artist_name, mb_artist_id=resolved_mb_artist)
        if artist_art.get("image_url"):
            artwork_candidates.append(ArtworkCandidate(
                url=artist_art["image_url"],
                source="artist_scraper",
                art_type="artist",
                applied=False,
            ))
            pre_logs.append(f"[scraper-test] Artist art found: {artist_art['image_url']}")
        else:
            pre_logs.append(f"[scraper-test] No artist art found")
        if artist_art.get("fanart_url"):
            artwork_candidates.append(ArtworkCandidate(
                url=artist_art["fanart_url"],
                source="artist_scraper",
                art_type="fanart",
                applied=False,
            ))
            pre_logs.append(f"[scraper-test] Artist fanart found: {artist_art['fanart_url']}")
        # If the artist scraper found an MB artist ID we didn't have, capture it
        if artist_art.get("mb_artist_id") and not resolved_mb_artist:
            resolved_mb_artist = artist_art["mb_artist_id"]
            pre_logs.append(f"[scraper-test] Artist scraper discovered MB artist ID: {resolved_mb_artist}")
    except Exception as e:
        pre_logs.append(f"[scraper-test] Artist artwork fetch failed: {e}")
        logger.warning(f"Scraper test: artist artwork fetch failed: {e}")

    # â”€â”€ Fetch album artwork from dedicated scrapers â”€â”€
    # Call MB/CAA and Wikipedia separately so both candidates are surfaced
    # (the combined get_album_artwork returns only the winner).
    if resolved_album_name:
        pre_logs.append(f"[scraper-test] Fetching dedicated album artwork for: '{resolved_album_name}' by '{resolved_artist_name}'")
        try:
            from app.scraper.artist_album_scraper import (
                get_album_artwork_musicbrainz, get_album_artwork_wikipedia,
            )
            album_art_mb = get_album_artwork_musicbrainz(resolved_album_name, resolved_artist_name)
            if album_art_mb.get("image_url"):
                artwork_candidates.append(ArtworkCandidate(
                    url=album_art_mb["image_url"],
                    source="album_scraper",
                    art_type="album",
                    applied=False,
                ))
                pre_logs.append(f"[scraper-test] Album art (CAA): {album_art_mb['image_url']}")
            else:
                pre_logs.append("[scraper-test] No album art from CAA")
            album_art_wiki = get_album_artwork_wikipedia(resolved_album_name, resolved_artist_name, wiki_url=_wiki_album_url)
            if album_art_wiki.get("image_url"):
                _existing_urls = {c.url for c in artwork_candidates}
                if album_art_wiki["image_url"] not in _existing_urls:
                    artwork_candidates.append(ArtworkCandidate(
                        url=album_art_wiki["image_url"],
                        source="album_scraper_wiki",
                        art_type="album",
                        applied=False,
                    ))
                    pre_logs.append(f"[scraper-test] Album art (Wikipedia): {album_art_wiki['image_url']}")
                else:
                    pre_logs.append("[scraper-test] Album art (Wikipedia): already in candidates from cross-link")
            else:
                pre_logs.append("[scraper-test] No album art from Wikipedia search")
        except Exception as e:
            pre_logs.append(f"[scraper-test] Album artwork fetch failed: {e}")
            logger.warning(f"Scraper test: album artwork fetch failed: {e}")
    else:
        pre_logs.append(f"[scraper-test] No album resolved â€” skipping album artwork fetch")

    # If MB resolved, add CoverArtArchive poster/album art.
    # When the single's release-group differs from the parent album's
    # release-group, this is a single â€” use the release-group CAA
    # endpoint as poster art.  Otherwise treat it as album art.
    from app.scraper.artwork_selection import fetch_caa_artwork
    _caa_validated, _caa_source, _caa_art_type = fetch_caa_artwork(
        mb_release_id=metadata.get("mb_release_id"),
        mb_release_group_id=metadata.get("mb_release_group_id"),
        mb_album_release_group_id=metadata.get("mb_album_release_group_id"),
        mb_album_release_id=metadata.get("mb_album_release_id"),
    )
    if _caa_validated:
        existing_urls = {c.url for c in artwork_candidates}
        if _caa_validated not in existing_urls:
            artwork_candidates.append(ArtworkCandidate(
                url=_caa_validated,
                source=_caa_source,
                art_type=_caa_art_type,
                applied=False,
            ))
            pre_logs.append(f"[scraper-test] CAA artwork ({_caa_art_type}): {_caa_validated}")
        else:
            pre_logs.append(f"[scraper-test] CAA artwork ({_caa_art_type}): already in candidates")
    else:
        pre_logs.append("[scraper-test] CAA artwork: none found")

    # Mark which candidate is the actual applied one
    final_image = metadata.get("image_url")
    if final_image:
        found = False
        for cand in artwork_candidates:
            if cand.url == final_image:
                cand.applied = True
                found = True
        if not found:
            artwork_candidates.append(ArtworkCandidate(url=final_image, source="unknown", art_type="poster", applied=True))

    # Apply priority selection across all art types (shared logic)
    from app.scraper.artwork_selection import apply_candidate_priorities, is_single_release
    _is_single = is_single_release(
        metadata.get("mb_release_group_id"),
        metadata.get("mb_album_release_group_id"),
    )
    apply_candidate_priorities(artwork_candidates, is_single=_is_single)

    # â”€â”€ Build before/after AI changes â”€â”€
    ai_changes = []
    pre_snapshot = metadata.get("_pre_ai_snapshot", {})
    if pre_snapshot:
        _COMPARE_FIELDS = ("artist", "title", "album", "year", "genres", "plot", "image_url")
        for field_name in _COMPARE_FIELDS:
            before_val = pre_snapshot.get(field_name)
            after_val = metadata.get(field_name)
            # Normalize for comparison
            before_str = str(before_val) if before_val is not None else ""
            after_str = str(after_val) if after_val is not None else ""
            if before_str != after_str:
                ai_changes.append(BeforeAfterField(
                    field=field_name,
                    before=before_val,
                    after=after_val,
                    source="ai_review",
                ))

    # â”€â”€ Collect source URLs â”€â”€
    source_urls = {k: v for k, v in metadata.get("_source_urls", {}).items() if v}
    # Ensure canonical video URL is included
    source_urls["video"] = canonical
    # Add MB artist URL if we have an MBID
    if metadata.get("mb_artist_id") and "musicbrainz_artist" not in source_urls:
        source_urls["musicbrainz_artist"] = f"https://musicbrainz.org/artist/{metadata['mb_artist_id']}"
    # Add MB release URL if we have a release MBID
    if metadata.get("mb_release_id") and "musicbrainz_release" not in source_urls:
        source_urls["musicbrainz_release"] = f"https://musicbrainz.org/release/{metadata['mb_release_id']}"
    # Add Cover Art Archive URL â€" only if we validated art exists
    if "coverartarchive" not in source_urls and _caa_validated:
        if _caa_art_type == "poster" and metadata.get("mb_release_group_id"):
            source_urls["coverartarchive"] = f"https://coverartarchive.org/release-group/{metadata['mb_release_group_id']}"
        elif metadata.get("mb_release_id"):
            source_urls["coverartarchive"] = f"https://coverartarchive.org/release/{metadata['mb_release_id']}"
    # Add MB release group URL if we have an RG MBID
    if metadata.get("mb_release_group_id") and "musicbrainz_release_group" not in source_urls:
        source_urls["musicbrainz_release_group"] = f"https://musicbrainz.org/release-group/{metadata['mb_release_group_id']}"
    # Add MB album (parent) release / release-group URLs if resolved
    if metadata.get("mb_album_release_id") and "musicbrainz_album_release" not in source_urls:
        source_urls["musicbrainz_album_release"] = f"https://musicbrainz.org/release/{metadata['mb_album_release_id']}"
    if metadata.get("mb_album_release_group_id") and "musicbrainz_album" not in source_urls:
        source_urls["musicbrainz_album"] = f"https://musicbrainz.org/release-group/{metadata['mb_album_release_group_id']}"
    # Add Wikipedia album/artist URLs discovered via cross-link
    if _wiki_album_url and "wikipedia_album" not in source_urls:
        source_urls["wikipedia_album"] = _wiki_album_url
    if _wiki_artist_url and "wikipedia_artist" not in source_urls:
        source_urls["wikipedia_artist"] = _wiki_artist_url

    # â”€â”€ Post-pipeline summary for trace log â”€â”€
    pre_logs.append(f"[scraper-test] â”€â”€ Pipeline complete â”€â”€")
    pre_logs.append(f"[scraper-test] Resolved artist: '{resolved_artist}' (source: {artist_source})")
    pre_logs.append(f"[scraper-test] Resolved title: '{resolved_title}' (source: {title_source})")
    pre_logs.append(f"[scraper-test] Resolved album: '{metadata.get('album')}' (source: {album_source})")
    pre_logs.append(f"[scraper-test] Resolved year: {metadata.get('year')} (source: {year_source})")
    pre_logs.append(f"[scraper-test] Resolved genres: {metadata.get('genres')} (source: {genre_source})")
    pre_logs.append(f"[scraper-test] Resolved image_url: {'yes' if metadata.get('image_url') else 'none'} (source: {image_source})")
    pre_logs.append(f"[scraper-test] Resolved plot: {'yes (' + str(len(str(metadata.get('plot', '')))) + ' chars)' if metadata.get('plot') else 'none'} (source: {plot_source})")
    pre_logs.append(f"[scraper-test] MB IDs: artist={metadata.get('mb_artist_id')}, recording={metadata.get('mb_recording_id')}, release={metadata.get('mb_release_id')}, rg={metadata.get('mb_release_group_id')}")
    pre_logs.append(f"[scraper-test] IMDB: {metadata.get('imdb_url') or 'none'}")
    pre_logs.append(f"[scraper-test] Source URLs: {list(source_urls.keys())}")
    pre_logs.append(f"[scraper-test] Artwork candidates: {len(artwork_candidates)}")
    pre_logs.append(f"[scraper-test] AI changes: {len(ai_changes)}")
    pre_logs.append(f"[scraper-test] Pipeline failures: {len(metadata.get('pipeline_failures', []))}")
    pre_logs.append(f"[scraper-test] Scraper sources used: {sources_used}")

    # â”€â”€ Write per-test output file â”€â”€
    output_file_path = _write_scraper_test_file(
        req=req, mode=mode, provider=provider.value, video_id=video_id,
        canonical=canonical, ytdlp_meta=ytdlp_meta, parsed_artist=parsed_artist,
        parsed_title=parsed_title, artist=artist, artist_source=artist_source,
        title=title, title_source=title_source, metadata=metadata,
        sources_used=sources_used, source_urls=source_urls,
        artwork_candidates=artwork_candidates, ai_changes=ai_changes,
        pre_logs=pre_logs, logs=logs,
    )

    result = ScraperTestResult(
        url=req.url,
        canonical_url=canonical,
        provider=provider.value,
        video_id=video_id,

        # yt-dlp raw
        ytdlp_title=ytdlp_meta.get("title"),
        ytdlp_uploader=ytdlp_meta.get("uploader"),
        ytdlp_channel=ytdlp_meta.get("channel"),
        ytdlp_artist=ytdlp_meta.get("artist"),
        ytdlp_track=ytdlp_meta.get("track"),
        ytdlp_album=ytdlp_meta.get("album"),
        ytdlp_duration=ytdlp_meta.get("duration"),
        ytdlp_upload_date=ytdlp_meta.get("upload_date"),
        ytdlp_thumbnail=ytdlp_meta.get("thumbnail"),
        ytdlp_description=(ytdlp_meta.get("description") or "")[:500],
        ytdlp_tags=ytdlp_meta.get("tags", [])[:20],

        # Parsed
        parsed_artist=parsed_artist or "",
        parsed_title=parsed_title or "",

        # Resolved with provenance
        artist=_prov("artist", resolved_artist, artist_source),
        title=_prov("title", resolved_title, title_source),
        album=_prov("album", metadata.get("album"), album_source),
        year=_prov("year", metadata.get("year"), year_source),
        genres=_prov("genres", metadata.get("genres"), genre_source),
        plot=_prov("plot", metadata.get("plot"), plot_source),
        image_url=_prov("image_url", metadata.get("image_url"), image_source),
        mb_artist_id=_prov("mb_artist_id", metadata.get("mb_artist_id"), mb_source),
        mb_recording_id=_prov("mb_recording_id", metadata.get("mb_recording_id"), mb_source),
        mb_release_id=_prov("mb_release_id", metadata.get("mb_release_id"), mb_source),
        mb_release_group_id=_prov("mb_release_group_id", metadata.get("mb_release_group_id"), mb_source),
        imdb_url=_prov("imdb_url", metadata.get("imdb_url"), imdb_source),

        # Source URLs
        source_urls=source_urls,

        # Artwork candidates
        artwork_candidates=artwork_candidates,

        # Before/after AI
        ai_changes=ai_changes,

        # Diagnostics â€” merge pre-pipeline logs + unified pipeline log into one trace
        scraper_sources_used=sources_used,
        pipeline_log=pre_logs + metadata.get("pipeline_log", []),
        pipeline_failures=metadata.get("pipeline_failures", []),

        mode=mode,

        # AI details
        ai_source_resolution=metadata.get("ai_source_resolution"),
        ai_final_review=metadata.get("ai_final_review"),

        output_file=output_file_path,
    )

    return result


# ── Streaming endpoint with progress ──────────────────────────────

_STEPS = [
    "Validating URL",
    "Fetching yt-dlp metadata",
    "Parsing artist & title",
    "Running metadata pipeline",
    "Fetching artist artwork",
    "Fetching album artwork",
    "Validating CAA artwork",
    "Applying artwork priorities",
    "Writing output file",
]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/run-stream")
def run_scraper_test_stream(req: ScraperTestRequest):
    """SSE streaming version of the scraper test — emits progress events."""

    def _generate() -> Generator[str, None, None]:
        step_idx = 0
        total = len(_STEPS)
        t0 = time.monotonic()

        def emit_start(idx: int):
            return _sse("progress", {
                "step": idx + 1, "total": total,
                "label": _STEPS[idx], "status": "running",
                "elapsed_ms": 0,
            })

        def emit_done(idx: int, start: float):
            return _sse("progress", {
                "step": idx + 1, "total": total,
                "label": _STEPS[idx], "status": "complete",
                "elapsed_ms": round((time.monotonic() - start) * 1000),
            })

        def emit_sub(idx: int, sub_label: str, start: float):
            return _sse("progress", {
                "step": idx + 1, "total": total,
                "label": _STEPS[idx], "status": "running",
                "sub_label": sub_label,
                "elapsed_ms": round((time.monotonic() - start) * 1000),
            })

        db = SessionLocal()
        try:
            # ── Step 0: Validate URL ──
            step_idx = 0
            s = time.monotonic()
            yield emit_start(step_idx)

            if req.ai_auto or req.ai_only:
                from app.models import AppSetting
                row = db.query(AppSetting).filter(
                    AppSetting.key == "ai_provider",
                    AppSetting.user_id.is_(None),
                ).first()
                if (row.value if row else "none") == "none":
                    yield _sse("fail", {"detail": "AI mode requires an AI provider. Go to Settings and select one."})
                    return

            if is_playlist_url(req.url):
                yield _sse("fail", {"detail": "Playlists are not supported."})
                return

            try:
                provider, video_id = identify_provider(req.url)
            except ValueError as e:
                yield _sse("fail", {"detail": str(e)})
                return

            canonical = canonicalize_url(provider, video_id)
            yield emit_done(step_idx, s)

            # ── Pre-pipeline trace log ──
            pre_logs: List[str] = []
            pre_logs.append(f"[scraper-test] URL: {req.url}")
            pre_logs.append(f"[scraper-test] Provider: {provider.value}, Video ID: {video_id}")
            pre_logs.append(f"[scraper-test] Canonical URL: {canonical}")

            # ── Determine mode label ──
            if req.ai_only:
                mode = "AI Only"
            elif req.ai_auto:
                mode = "AI Auto"
            elif req.scrape_wikipedia and req.scrape_musicbrainz:
                mode = "Wiki + MB"
            elif req.scrape_wikipedia:
                mode = "Wiki Only"
            elif req.scrape_musicbrainz:
                mode = "MB Only"
            else:
                mode = "No Scraping"
            pre_logs.append(f"[scraper-test] Mode: {mode}")

            # ── Step 1: Fetch yt-dlp metadata ──
            step_idx = 1
            s = time.monotonic()
            yield emit_start(step_idx)

            from app.services.downloader import get_available_formats, extract_metadata_from_ytdlp

            pre_logs.append("[scraper-test] Fetching yt-dlp metadata (no download)...")
            try:
                _formats, info_dict = get_available_formats(req.url)
            except Exception as e:
                yield _sse("fail", {"detail": f"Failed to fetch video metadata: {e}"})
                return

            ytdlp_meta = extract_metadata_from_ytdlp(info_dict)
            pre_logs.append(f"[scraper-test] yt-dlp title: {ytdlp_meta.get('title', '(none)')}")
            pre_logs.append(f"[scraper-test] yt-dlp artist: {ytdlp_meta.get('artist', '(none)')}")
            pre_logs.append(f"[scraper-test] yt-dlp track: {ytdlp_meta.get('track', '(none)')}")
            pre_logs.append(f"[scraper-test] yt-dlp album: {ytdlp_meta.get('album', '(none)')}")
            pre_logs.append(f"[scraper-test] yt-dlp uploader: {ytdlp_meta.get('uploader', '(none)')}")
            pre_logs.append(f"[scraper-test] yt-dlp channel: {ytdlp_meta.get('channel', '(none)')}")
            pre_logs.append(f"[scraper-test] yt-dlp duration: {ytdlp_meta.get('duration', '(none)')}")
            pre_logs.append(f"[scraper-test] yt-dlp upload_date: {ytdlp_meta.get('upload_date', '(none)')}")
            _tag_count = len(ytdlp_meta.get("tags", []))
            pre_logs.append(f"[scraper-test] yt-dlp tags: {_tag_count} tag(s)")
            _desc_len = len(ytdlp_meta.get("description", ""))
            pre_logs.append(f"[scraper-test] yt-dlp description: {_desc_len} chars")
            yield emit_done(step_idx, s)

            # ── Step 2: Parse artist / title ──
            step_idx = 2
            s = time.monotonic()
            yield emit_start(step_idx)

            from app.scraper.metadata_resolver import extract_artist_title, clean_title

            raw_title = ytdlp_meta.get("title", "")
            parsed_artist, parsed_title = extract_artist_title(raw_title)
            pre_logs.append(f"[scraper-test] Parsed from title: artist='{parsed_artist}', title='{parsed_title}'")

            if req.artist_override:
                artist = req.artist_override
                artist_source = "override"
            elif ytdlp_meta.get("artist"):
                artist = clean_title(ytdlp_meta["artist"])
                artist_source = "yt-dlp"
            elif parsed_artist:
                artist = parsed_artist
                artist_source = "parsed"
            else:
                artist = ytdlp_meta.get("uploader") or ytdlp_meta.get("channel") or ""
                artist_source = "yt-dlp"

            if req.title_override:
                title = req.title_override
                title_source = "override"
            elif ytdlp_meta.get("track"):
                title = clean_title(ytdlp_meta["track"])
                title_source = "yt-dlp"
            elif parsed_title:
                title = parsed_title
                title_source = "parsed"
            else:
                title = clean_title(raw_title) if raw_title else ""
                title_source = "parsed"

            if artist and title:
                for sep in [" - ", " \u2013 ", " \u2014 ", " : "]:
                    prefix = artist + sep
                    if title.lower().startswith(prefix.lower()):
                        title = title[len(prefix):].strip()
                        break

            artist = artist or "Unknown Artist"
            title = title or "Unknown Title"

            # Compute skip flags
            if req.ai_only:
                skip_wiki = True
                skip_mb = True
                skip_ai = False
            elif req.ai_auto:
                skip_wiki = False
                skip_mb = False
                skip_ai = False
            else:
                skip_wiki = not req.scrape_wikipedia
                skip_mb = not req.scrape_musicbrainz
                skip_ai = True

            yield emit_done(step_idx, s)

            # ── Step 3: Unified metadata pipeline ──
            step_idx = 3
            s = time.monotonic()
            yield emit_start(step_idx)

            from app.scraper.unified_metadata import resolve_metadata_unified

            logs: List[str] = []
            _last_sub: List[Optional[str]] = [None]

            def _log_with_progress(msg: str):
                logs.append(msg)
                # Detect sub-step changes from log markers
                sub = None
                if "ai_source_resolution:started" in msg or "Running AI source resolution" in msg:
                    sub = "AI Source Resolution"
                elif "MusicBrainz:" in msg and "search" in msg.lower():
                    sub = "MusicBrainz lookup"
                elif "Wikipedia:" in msg and ("search" in msg.lower() or "using" in msg.lower()):
                    sub = "Wikipedia scraping"
                elif "IMDB:" in msg:
                    sub = "IMDB lookup"
                elif "ai_final_review:started" in msg or "Running AI final review" in msg:
                    sub = "AI Final Review"
                if sub and sub != _last_sub[0]:
                    _last_sub[0] = sub

            metadata = resolve_metadata_unified(
                artist=artist,
                title=title,
                db=db,
                source_url=canonical,
                platform_title=ytdlp_meta.get("title", ""),
                channel_name=ytdlp_meta.get("channel") or ytdlp_meta.get("uploader") or "",
                platform_description=ytdlp_meta.get("description", ""),
                platform_tags=ytdlp_meta.get("tags"),
                upload_date=ytdlp_meta.get("upload_date", ""),
                duration_seconds=ytdlp_meta.get("duration"),
                ytdlp_metadata=info_dict,
                skip_wikipedia=skip_wiki,
                skip_musicbrainz=skip_mb,
                skip_ai=skip_ai,
                wikipedia_url=req.wikipedia_url if not skip_wiki else None,
                musicbrainz_url=req.musicbrainz_url if not skip_mb else None,
                log_callback=_log_with_progress,
                _test_mode=True,
            )
            yield emit_done(step_idx, s)

            # ── Provenance logic ──
            sources_used = metadata.get("scraper_sources_used", [])
            has_mb = any(s_.startswith("musicbrainz:") for s_ in sources_used)
            has_wiki = any(s_.startswith("wikipedia:") for s_ in sources_used)
            has_imdb = any(s_.startswith("imdb:") for s_ in sources_used)
            has_ai_source = metadata.get("ai_source_resolution") is not None
            has_ai_review = metadata.get("ai_final_review") is not None
            ai_review_changes = []
            if has_ai_review and metadata["ai_final_review"]:
                ai_review_changes = [c.split(":")[0] for c in metadata["ai_final_review"].get("corrections", [])]

            def _prov(field_name: str, val: Any, default_source: str) -> dict:
                if val is None or val == "" or val == []:
                    return {"value": val, "source": "none"}
                if field_name in ai_review_changes:
                    return {"value": val, "source": "ai_review"}
                if has_ai_source and not skip_ai:
                    ai_sr = metadata.get("ai_source_resolution", {})
                    if ai_sr and isinstance(ai_sr, dict):
                        identity = ai_sr.get("identity", {})
                        if identity.get(field_name) and str(identity[field_name]) == str(val):
                            return {"value": val, "source": "ai"}
                return {"value": val, "source": default_source}

            resolved_artist = metadata.get("artist", artist)
            resolved_title = metadata.get("title", title)
            resolved_album_name = metadata.get("album")
            resolved_mb_artist = metadata.get("mb_artist_id")

            album_source = "musicbrainz" if has_mb and metadata.get("album") else ("ai" if has_ai_source else "none")
            year_source = "musicbrainz" if has_mb and metadata.get("year") else ("ai" if has_ai_source else "none")
            genre_source = "wikipedia" if has_wiki and metadata.get("genres") else ("musicbrainz" if has_mb and metadata.get("genres") else ("ai" if has_ai_source else "none"))
            plot_source = "wikipedia" if has_wiki and metadata.get("plot") else ("ai" if has_ai_source else "none")
            image_source = "wikipedia" if has_wiki and metadata.get("image_url") else ("musicbrainz" if has_mb and metadata.get("image_url") else ("ai" if has_ai_source else "none"))
            mb_source = "musicbrainz" if has_mb else "none"
            imdb_source = "imdb" if has_imdb else ("ai" if has_ai_source and metadata.get("imdb_url") else "none")

            # ── Build artwork candidates ──
            artwork_candidates = []
            if ytdlp_meta.get("thumbnail"):
                artwork_candidates.append(ArtworkCandidate(
                    url=ytdlp_meta["thumbnail"], source="yt-dlp", art_type="poster", applied=False,
                ))
            for cand in metadata.get("_artwork_candidates", []):
                artwork_candidates.append(ArtworkCandidate(
                    url=cand["url"], source=cand.get("source", "unknown"),
                    art_type=cand.get("art_type", "poster"), applied=cand.get("applied", False),
                ))

            _wiki_album_url: Optional[str] = metadata.get("_source_urls", {}).get("wikipedia_album")
            _wiki_artist_url: Optional[str] = metadata.get("_source_urls", {}).get("wikipedia_artist")

            resolved_artist_name = metadata.get("artist", artist)

            # ── Step 4: Artist artwork ──
            step_idx = 4
            s = time.monotonic()
            yield emit_start(step_idx)

            try:
                from app.scraper.artist_album_scraper import get_artist_artwork
                artist_art = get_artist_artwork(resolved_artist_name, mb_artist_id=resolved_mb_artist)
                if artist_art.get("image_url"):
                    artwork_candidates.append(ArtworkCandidate(
                        url=artist_art["image_url"], source="artist_scraper", art_type="artist", applied=False,
                    ))
                if artist_art.get("fanart_url"):
                    artwork_candidates.append(ArtworkCandidate(
                        url=artist_art["fanart_url"], source="artist_scraper", art_type="fanart", applied=False,
                    ))
                if artist_art.get("mb_artist_id") and not resolved_mb_artist:
                    resolved_mb_artist = artist_art["mb_artist_id"]
            except Exception as e:
                pre_logs.append(f"[scraper-test] Artist artwork fetch failed: {e}")
            yield emit_done(step_idx, s)

            # ── Step 5: Album artwork ──
            step_idx = 5
            s = time.monotonic()
            yield emit_start(step_idx)

            if resolved_album_name:
                try:
                    from app.scraper.artist_album_scraper import (
                        get_album_artwork_musicbrainz, get_album_artwork_wikipedia,
                    )
                    if req.scrape_musicbrainz or req.ai_auto:
                        album_art_mb = get_album_artwork_musicbrainz(resolved_album_name, resolved_artist_name)
                        if album_art_mb.get("image_url"):
                            artwork_candidates.append(ArtworkCandidate(
                                url=album_art_mb["image_url"], source="album_scraper", art_type="album", applied=False,
                            ))
                    album_art_wiki = get_album_artwork_wikipedia(resolved_album_name, resolved_artist_name, wiki_url=_wiki_album_url)
                    if album_art_wiki.get("image_url"):
                        _existing_urls = {c.url for c in artwork_candidates}
                        if album_art_wiki["image_url"] not in _existing_urls:
                            artwork_candidates.append(ArtworkCandidate(
                                url=album_art_wiki["image_url"], source="album_scraper_wiki", art_type="album", applied=False,
                            ))
                except Exception as e:
                    pre_logs.append(f"[scraper-test] Album artwork fetch failed: {e}")
            yield emit_done(step_idx, s)

            # ── Step 6: CAA artwork ──
            step_idx = 6
            s = time.monotonic()
            yield emit_start(step_idx)

            from app.scraper.artwork_selection import fetch_caa_artwork
            _caa_validated, _caa_source, _caa_art_type = fetch_caa_artwork(
                mb_release_id=metadata.get("mb_release_id"),
                mb_release_group_id=metadata.get("mb_release_group_id"),
                mb_album_release_group_id=metadata.get("mb_album_release_group_id"),
                mb_album_release_id=metadata.get("mb_album_release_id"),
            )
            if _caa_validated:
                existing_urls = {c.url for c in artwork_candidates}
                if _caa_validated not in existing_urls:
                    artwork_candidates.append(ArtworkCandidate(
                        url=_caa_validated, source=_caa_source, art_type=_caa_art_type, applied=False,
                    ))

            final_image = metadata.get("image_url")
            if final_image:
                found = False
                for cand in artwork_candidates:
                    if cand.url == final_image:
                        cand.applied = True
                        found = True
                if not found:
                    artwork_candidates.append(ArtworkCandidate(url=final_image, source="unknown", art_type="poster", applied=True))
            yield emit_done(step_idx, s)

            # ── Step 7: Candidate priorities ──
            step_idx = 7
            s = time.monotonic()
            yield emit_start(step_idx)

            from app.scraper.artwork_selection import apply_candidate_priorities, is_single_release
            _is_single = is_single_release(
                metadata.get("mb_release_group_id"),
                metadata.get("mb_album_release_group_id"),
            )
            apply_candidate_priorities(artwork_candidates, is_single=_is_single)

            # Build AI changes
            ai_changes = []
            pre_snapshot = metadata.get("_pre_ai_snapshot", {})
            if pre_snapshot:
                _COMPARE_FIELDS = ("artist", "title", "album", "year", "genres", "plot", "image_url")
                for fn in _COMPARE_FIELDS:
                    bv = pre_snapshot.get(fn)
                    av = metadata.get(fn)
                    if str(bv) if bv is not None else "" != str(av) if av is not None else "":
                        ai_changes.append(BeforeAfterField(field=fn, before=bv, after=av, source="ai_review"))

            # Collect source URLs
            source_urls = {k: v for k, v in metadata.get("_source_urls", {}).items() if v}
            source_urls["video"] = canonical
            if metadata.get("mb_artist_id") and "musicbrainz_artist" not in source_urls:
                source_urls["musicbrainz_artist"] = f"https://musicbrainz.org/artist/{metadata['mb_artist_id']}"
            if metadata.get("mb_release_id") and "musicbrainz_release" not in source_urls:
                source_urls["musicbrainz_release"] = f"https://musicbrainz.org/release/{metadata['mb_release_id']}"
            if "coverartarchive" not in source_urls and _caa_validated:
                if _caa_art_type == "poster" and metadata.get("mb_release_group_id"):
                    source_urls["coverartarchive"] = f"https://coverartarchive.org/release-group/{metadata['mb_release_group_id']}"
                elif metadata.get("mb_release_id"):
                    source_urls["coverartarchive"] = f"https://coverartarchive.org/release/{metadata['mb_release_id']}"
            if metadata.get("mb_release_group_id") and "musicbrainz_release_group" not in source_urls:
                source_urls["musicbrainz_release_group"] = f"https://musicbrainz.org/release-group/{metadata['mb_release_group_id']}"
            if metadata.get("mb_album_release_id") and "musicbrainz_album_release" not in source_urls:
                source_urls["musicbrainz_album_release"] = f"https://musicbrainz.org/release/{metadata['mb_album_release_id']}"
            if metadata.get("mb_album_release_group_id") and "musicbrainz_album" not in source_urls:
                source_urls["musicbrainz_album"] = f"https://musicbrainz.org/release-group/{metadata['mb_album_release_group_id']}"
            if _wiki_album_url and "wikipedia_album" not in source_urls:
                source_urls["wikipedia_album"] = _wiki_album_url
            if _wiki_artist_url and "wikipedia_artist" not in source_urls:
                source_urls["wikipedia_artist"] = _wiki_artist_url
            yield emit_done(step_idx, s)

            # ── Step 8: Write output file ──
            step_idx = 8
            s = time.monotonic()
            yield emit_start(step_idx)

            output_file_path = _write_scraper_test_file(
                req=req, mode=mode, provider=provider.value, video_id=video_id,
                canonical=canonical, ytdlp_meta=ytdlp_meta, parsed_artist=parsed_artist,
                parsed_title=parsed_title, artist=artist, artist_source=artist_source,
                title=title, title_source=title_source, metadata=metadata,
                sources_used=sources_used, source_urls=source_urls,
                artwork_candidates=artwork_candidates, ai_changes=ai_changes,
                pre_logs=pre_logs, logs=logs,
            )
            yield emit_done(step_idx, s)

            # ── Emit final result ──
            result = ScraperTestResult(
                url=req.url,
                canonical_url=canonical,
                provider=provider.value,
                video_id=video_id,
                ytdlp_title=ytdlp_meta.get("title"),
                ytdlp_uploader=ytdlp_meta.get("uploader"),
                ytdlp_channel=ytdlp_meta.get("channel"),
                ytdlp_artist=ytdlp_meta.get("artist"),
                ytdlp_track=ytdlp_meta.get("track"),
                ytdlp_album=ytdlp_meta.get("album"),
                ytdlp_duration=ytdlp_meta.get("duration"),
                ytdlp_upload_date=ytdlp_meta.get("upload_date"),
                ytdlp_thumbnail=ytdlp_meta.get("thumbnail"),
                ytdlp_description=(ytdlp_meta.get("description") or "")[:500],
                ytdlp_tags=ytdlp_meta.get("tags", [])[:20],
                parsed_artist=parsed_artist or "",
                parsed_title=parsed_title or "",
                artist=_prov("artist", resolved_artist, artist_source),
                title=_prov("title", resolved_title, title_source),
                album=_prov("album", metadata.get("album"), album_source),
                year=_prov("year", metadata.get("year"), year_source),
                genres=_prov("genres", metadata.get("genres"), genre_source),
                plot=_prov("plot", metadata.get("plot"), plot_source),
                image_url=_prov("image_url", metadata.get("image_url"), image_source),
                mb_artist_id=_prov("mb_artist_id", metadata.get("mb_artist_id"), mb_source),
                mb_recording_id=_prov("mb_recording_id", metadata.get("mb_recording_id"), mb_source),
                mb_release_id=_prov("mb_release_id", metadata.get("mb_release_id"), mb_source),
                mb_release_group_id=_prov("mb_release_group_id", metadata.get("mb_release_group_id"), mb_source),
                imdb_url=_prov("imdb_url", metadata.get("imdb_url"), imdb_source),
                source_urls=source_urls,
                artwork_candidates=artwork_candidates,
                ai_changes=ai_changes,
                scraper_sources_used=sources_used,
                pipeline_log=pre_logs + metadata.get("pipeline_log", []),
                pipeline_failures=metadata.get("pipeline_failures", []),
                mode=mode,
                ai_source_resolution=metadata.get("ai_source_resolution"),
                ai_final_review=metadata.get("ai_final_review"),
                output_file=output_file_path,
            )

            yield _sse("result", result.model_dump())
            yield _sse("done", {"total_ms": round((time.monotonic() - t0) * 1000)})

        except Exception as e:
            logger.exception("Scraper test stream error")
            yield _sse("fail", {"detail": str(e)})
        finally:
            db.close()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/run-stream")
def run_scraper_test_stream_get(
    url: str = Query(...),
    artist_override: Optional[str] = Query(None),
    title_override: Optional[str] = Query(None),
    scrape_wikipedia: bool = Query(False),
    scrape_musicbrainz: bool = Query(False),
    wikipedia_url: Optional[str] = Query(None),
    musicbrainz_url: Optional[str] = Query(None),
    ai_auto: bool = Query(False),
    ai_only: bool = Query(False),
):
    """GET version of the SSE streaming scraper test (for EventSource)."""
    req = ScraperTestRequest(
        url=url,
        artist_override=artist_override,
        title_override=title_override,
        scrape_wikipedia=scrape_wikipedia,
        scrape_musicbrainz=scrape_musicbrainz,
        wikipedia_url=wikipedia_url,
        musicbrainz_url=musicbrainz_url,
        ai_auto=ai_auto,
        ai_only=ai_only,
    )
    return run_scraper_test_stream(req)


# ══════════════════════════════════════════════════════════════
#  IMPORT MODE — scan directory + run scraper test from file
# ══════════════════════════════════════════════════════════════

_VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.webm', '.mov', '.flv', '.wmv'}


@router.get("/scan-directory")
def scan_directory(directory: str = Query(..., description="Directory path to scan for video files")):
    """Scan a directory for video files and NFOs."""
    directory = os.path.normpath(directory)
    if not os.path.isdir(directory):
        raise HTTPException(status_code=400, detail=f"Directory not found: {directory}")

    video_files = []
    nfo_files = []
    try:
        for f in sorted(os.listdir(directory)):
            ext = os.path.splitext(f)[1].lower()
            if ext in _VIDEO_EXTS:
                video_files.append(f)
            elif ext == '.nfo':
                nfo_files.append(f)
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {directory}")

    return DirectoryScanResult(
        directory=directory,
        video_files=video_files,
        nfo_files=nfo_files,
        has_multiple=len(video_files) > 1,
    )


@router.get("/run-import-stream")
def run_import_test_stream_get(
    directory: str = Query(...),
    file_name: Optional[str] = Query(None),
    artist_override: Optional[str] = Query(None),
    title_override: Optional[str] = Query(None),
    scrape_wikipedia: bool = Query(False),
    scrape_musicbrainz: bool = Query(False),
    wikipedia_url: Optional[str] = Query(None),
    musicbrainz_url: Optional[str] = Query(None),
    ai_auto: bool = Query(False),
    ai_only: bool = Query(False),
):
    """GET SSE streaming import-mode scraper test (for EventSource)."""
    req = ImportTestRequest(
        directory=directory,
        file_name=file_name,
        artist_override=artist_override,
        title_override=title_override,
        scrape_wikipedia=scrape_wikipedia,
        scrape_musicbrainz=scrape_musicbrainz,
        wikipedia_url=wikipedia_url,
        musicbrainz_url=musicbrainz_url,
        ai_auto=ai_auto,
        ai_only=ai_only,
    )
    return run_import_test_stream(req)


_IMPORT_STEPS = [
    "Scanning directory",       # 1
    "Parsing identity (NFO/filename)", # 2
    "Analyzing media quality",  # 3
    "Searching YouTube match",  # 4
    "Fetching yt-dlp metadata", # 5
    "Running metadata pipeline",# 6
    "Fetching artist artwork",  # 7
    "Fetching album artwork",   # 8
    "Validating CAA artwork",   # 9
    "Applying artwork priorities", # 10
    "Writing output file",      # 11
]


@router.post("/run-import-stream")
def run_import_test_stream(req: ImportTestRequest):
    """SSE streaming import-mode scraper test — mirrors the URL mode but
    uses NFO/filename parsing instead of URL identification."""

    def _generate() -> Generator[str, None, None]:
        t0 = time.monotonic()
        total = len(_IMPORT_STEPS)

        def _sse(event: str, data: Any) -> str:
            return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

        def emit_start(idx: int, sub_label: str = "") -> str:
            return _sse("progress", {
                "step": idx + 1, "total": total,
                "label": _IMPORT_STEPS[idx], "status": "running",
                "sub_label": sub_label, "elapsed_ms": 0,
            })

        def emit_done(idx: int, start_time: float) -> str:
            return _sse("progress", {
                "step": idx + 1, "total": total,
                "label": _IMPORT_STEPS[idx], "status": "complete",
                "sub_label": "", "elapsed_ms": round((time.monotonic() - start_time) * 1000),
            })

        db = SessionLocal()
        try:
            pre_logs: List[str] = []
            logs: List[str] = []

            # ── Step 0: Scan directory & locate video file ──
            step_idx = 0
            s = time.monotonic()
            yield emit_start(step_idx)

            directory = os.path.normpath(req.directory)
            if not os.path.isdir(directory):
                yield _sse("fail", {"detail": f"Directory not found: {directory}"})
                return

            video_files = [
                f for f in os.listdir(directory)
                if os.path.splitext(f)[1].lower() in _VIDEO_EXTS
            ]
            if not video_files:
                yield _sse("fail", {"detail": f"No video files found in {directory}"})
                return

            if req.file_name:
                if req.file_name not in video_files:
                    yield _sse("fail", {"detail": f"File '{req.file_name}' not found in directory"})
                    return
                chosen_file = req.file_name
            elif len(video_files) == 1:
                chosen_file = video_files[0]
            else:
                yield _sse("fail", {"detail": "Multiple video files found — select one first"})
                return

            source_path = os.path.join(directory, chosen_file)
            pre_logs.append(f"[import-test] Directory: {directory}")
            pre_logs.append(f"[import-test] Video file: {chosen_file}")
            pre_logs.append(f"[import-test] Full path: {source_path}")
            yield emit_done(step_idx, s)

            # ── Step 1: Parse identity (NFO + filename) ──
            step_idx = 1
            s = time.monotonic()
            yield emit_start(step_idx)

            from app.pipeline_lib.services.nfo_parser import find_nfo_for_video, parse_nfo_file
            from app.pipeline_lib.services.filename_parser import parse_filename

            parsed_artist = parsed_title = parsed_album = None
            parsed_year = None
            parsed_genres: List[str] = []
            parsed_plot = ""
            nfo_source_url = ""
            identity_source = "filename"

            nfo_path = find_nfo_for_video(source_path)
            if nfo_path:
                parsed_nfo = parse_nfo_file(nfo_path)
                if parsed_nfo:
                    parsed_artist = parsed_nfo.artist
                    parsed_title = parsed_nfo.title
                    parsed_album = parsed_nfo.album
                    parsed_year = parsed_nfo.year
                    parsed_genres = parsed_nfo.genres or []
                    parsed_plot = parsed_nfo.plot or ""
                    nfo_source_url = parsed_nfo.source_url or ""
                    identity_source = "nfo"
                    pre_logs.append(f"[import-test] NFO parsed: artist='{parsed_artist}', title='{parsed_title}'")
                    if parsed_album:
                        pre_logs.append(f"[import-test] NFO album: {parsed_album}")
                    if nfo_source_url:
                        pre_logs.append(f"[import-test] NFO source URL: {nfo_source_url}")

            # Filename fallback for missing artist/title
            parsed_fn = parse_filename(os.path.basename(source_path))
            fn_artist = parsed_fn.artist
            fn_title = parsed_fn.title
            fn_year = parsed_fn.year
            pre_logs.append(f"[import-test] Filename parsed: artist='{fn_artist}', title='{fn_title}', pattern='{parsed_fn.pattern_name}'")

            if not parsed_artist:
                parsed_artist = fn_artist
                identity_source = "filename"
            if not parsed_title:
                parsed_title = fn_title
                identity_source = "filename" if identity_source != "nfo" else identity_source
            if not parsed_year and fn_year:
                parsed_year = fn_year

            # Apply overrides
            if req.artist_override:
                artist = req.artist_override
                artist_source = "override"
            elif parsed_artist:
                artist = parsed_artist
                artist_source = identity_source
            else:
                artist = "Unknown Artist"
                artist_source = "fallback"

            if req.title_override:
                title = req.title_override
                title_source = "override"
            elif parsed_title:
                title = parsed_title
                title_source = identity_source
            else:
                title = os.path.splitext(chosen_file)[0]
                title_source = "fallback"

            pre_logs.append(f"[import-test] Artist: '{artist}' (source: {artist_source})")
            pre_logs.append(f"[import-test] Title: '{title}' (source: {title_source})")
            yield emit_done(step_idx, s)

            # ── Step 2: Analyze media quality ──
            step_idx = 2
            s = time.monotonic()
            yield emit_start(step_idx)

            ffprobe_data: Dict[str, Any] = {}
            try:
                from app.pipeline_lib.services.media_analyzer import extract_quality_signature
                ffprobe_data = extract_quality_signature(source_path)
                pre_logs.append(f"[import-test] Quality: {ffprobe_data.get('width', '?')}x{ffprobe_data.get('height', '?')} "
                                f"{ffprobe_data.get('video_codec', '?')} {ffprobe_data.get('duration_seconds', '?')}s")
            except Exception as e:
                pre_logs.append(f"[import-test] Quality analysis warning: {e}")
            yield emit_done(step_idx, s)

            # ── Step 3: YouTube match ──
            step_idx = 3
            s = time.monotonic()
            yield emit_start(step_idx)

            yt_match: Optional[Dict[str, Any]] = None
            try:
                from app.pipeline_lib.services.youtube_matcher import find_best_youtube_match
                duration = ffprobe_data.get("duration_seconds")
                match = find_best_youtube_match(
                    artist, title,
                    duration_seconds=int(duration) if duration else None,
                )
                if match:
                    yt_match = {
                        "url": match.url,
                        "video_id": getattr(match, "video_id", ""),
                        "title": getattr(match, "title", ""),
                        "channel": getattr(match, "channel", ""),
                        "score": match.overall_score,
                    }
                    pre_logs.append(f"[import-test] YouTube match: {match.url} (score={match.overall_score:.2f})")
                else:
                    pre_logs.append("[import-test] No YouTube match found")
            except Exception as e:
                pre_logs.append(f"[import-test] YouTube match error: {e}")
            yield emit_done(step_idx, s)

            # ── Step 4: yt-dlp metadata from YouTube match ──
            step_idx = 4
            s = time.monotonic()
            yield emit_start(step_idx)

            ytdlp_meta: Dict[str, Any] = {}
            info_dict: Optional[Dict] = None
            canonical = nfo_source_url or ""
            if yt_match:
                try:
                    from app.services.downloader import get_available_formats, extract_metadata_from_ytdlp
                    ytdlp_url = nfo_source_url if nfo_source_url else yt_match["url"]
                    _formats, info_dict = get_available_formats(ytdlp_url)
                    ytdlp_meta = extract_metadata_from_ytdlp(info_dict)
                    if not canonical:
                        canonical = yt_match["url"]
                    pre_logs.append(f"[import-test] yt-dlp title: {ytdlp_meta.get('title', '(none)')}")
                except Exception as e:
                    pre_logs.append(f"[import-test] yt-dlp metadata warning: {e}")
            else:
                pre_logs.append("[import-test] Skipping yt-dlp metadata (no YouTube match)")
            yield emit_done(step_idx, s)

            # ── Step 5: Unified metadata pipeline ──
            step_idx = 5
            s = time.monotonic()
            yield emit_start(step_idx)

            # Compute skip flags
            if req.ai_only:
                skip_wiki = True
                skip_mb = True
                skip_ai = False
                mode = "AI Only"
            elif req.ai_auto:
                skip_wiki = False
                skip_mb = False
                skip_ai = False
                mode = "AI Auto"
            elif req.scrape_wikipedia and req.scrape_musicbrainz:
                skip_wiki = False
                skip_mb = False
                skip_ai = True
                mode = "Wiki + MB"
            elif req.scrape_wikipedia:
                skip_wiki = False
                skip_mb = True
                skip_ai = True
                mode = "Wiki Only"
            elif req.scrape_musicbrainz:
                skip_wiki = True
                skip_mb = False
                skip_ai = True
                mode = "MB Only"
            else:
                skip_wiki = True
                skip_mb = True
                skip_ai = True
                mode = "No Scraping"
            pre_logs.append(f"[import-test] Mode: {mode}")
            pre_logs.append(f"[import-test] Skip flags: wiki={skip_wiki}, mb={skip_mb}, ai={skip_ai}")

            def _log_with_progress(msg: str):
                logs.append(msg)
                ml = msg.lower()
                sub = ""
                if "musicbrainz" in ml:
                    sub = "MusicBrainz"
                elif "wikipedia" in ml:
                    sub = "Wikipedia"
                elif "imdb" in ml:
                    sub = "IMDB"
                elif "ai" in ml and "source" in ml:
                    sub = "AI Source Resolution"
                elif "ai" in ml and "review" in ml:
                    sub = "AI Final Review"

            from app.scraper.unified_metadata import resolve_metadata_unified

            metadata = resolve_metadata_unified(
                artist=artist,
                title=title,
                db=db,
                source_url=canonical,
                platform_title=ytdlp_meta.get("title", ""),
                channel_name=ytdlp_meta.get("channel") or ytdlp_meta.get("uploader") or "",
                platform_description=ytdlp_meta.get("description", ""),
                platform_tags=ytdlp_meta.get("tags"),
                upload_date=ytdlp_meta.get("upload_date", ""),
                duration_seconds=ffprobe_data.get("duration_seconds") or ytdlp_meta.get("duration"),
                ytdlp_metadata=info_dict,
                filename=chosen_file,
                folder_name=directory,
                skip_wikipedia=skip_wiki,
                skip_musicbrainz=skip_mb,
                skip_ai=skip_ai,
                wikipedia_url=req.wikipedia_url if not skip_wiki else None,
                musicbrainz_url=req.musicbrainz_url if not skip_mb else None,
                log_callback=_log_with_progress,
                _test_mode=True,
            )
            yield emit_done(step_idx, s)

            # ── Provenance logic ──
            sources_used = metadata.get("scraper_sources_used", [])
            has_mb = any(s_.startswith("musicbrainz:") for s_ in sources_used)
            has_wiki = any(s_.startswith("wikipedia:") for s_ in sources_used)
            has_imdb = any(s_.startswith("imdb:") for s_ in sources_used)
            has_ai_source = metadata.get("ai_source_resolution") is not None
            has_ai_review = metadata.get("ai_final_review") is not None
            ai_review_changes = []
            if has_ai_review and metadata["ai_final_review"]:
                ai_review_changes = [c.split(":")[0] for c in metadata["ai_final_review"].get("corrections", [])]

            def _prov(field_name: str, val: Any, default_source: str) -> dict:
                if val is None or val == "" or val == []:
                    return {"value": val, "source": "none"}
                if field_name in ai_review_changes:
                    return {"value": val, "source": "ai_review"}
                if has_ai_source and not skip_ai:
                    ai_sr = metadata.get("ai_source_resolution", {})
                    if ai_sr and isinstance(ai_sr, dict):
                        identity = ai_sr.get("identity", {})
                        if identity.get(field_name) and str(identity[field_name]) == str(val):
                            return {"value": val, "source": "ai"}
                return {"value": val, "source": default_source}

            resolved_artist = metadata.get("artist", artist)
            resolved_title = metadata.get("title", title)
            resolved_album_name = metadata.get("album") or parsed_album
            resolved_mb_artist = metadata.get("mb_artist_id")

            album_source = "musicbrainz" if has_mb and metadata.get("album") else ("nfo" if parsed_album else ("ai" if has_ai_source else "none"))
            year_source = "musicbrainz" if has_mb and metadata.get("year") else ("nfo" if parsed_year else ("ai" if has_ai_source else "none"))
            genre_source = "wikipedia" if has_wiki and metadata.get("genres") else ("musicbrainz" if has_mb and metadata.get("genres") else ("nfo" if parsed_genres else ("ai" if has_ai_source else "none")))
            plot_source = "wikipedia" if has_wiki and metadata.get("plot") else ("nfo" if parsed_plot else ("ai" if has_ai_source else "none"))
            image_source = "wikipedia" if has_wiki and metadata.get("image_url") else ("musicbrainz" if has_mb and metadata.get("image_url") else ("ai" if has_ai_source else "none"))
            mb_source = "musicbrainz" if has_mb else "none"
            imdb_source = "imdb" if has_imdb else ("ai" if has_ai_source and metadata.get("imdb_url") else "none")

            # ── Build artwork candidates ──
            artwork_candidates = []
            if ytdlp_meta.get("thumbnail"):
                artwork_candidates.append(ArtworkCandidate(
                    url=ytdlp_meta["thumbnail"], source="yt-dlp", art_type="poster", applied=False,
                ))
            for cand in metadata.get("_artwork_candidates", []):
                artwork_candidates.append(ArtworkCandidate(
                    url=cand["url"], source=cand.get("source", "unknown"),
                    art_type=cand.get("art_type", "poster"), applied=cand.get("applied", False),
                ))

            _wiki_album_url: Optional[str] = metadata.get("_source_urls", {}).get("wikipedia_album")
            _wiki_artist_url: Optional[str] = metadata.get("_source_urls", {}).get("wikipedia_artist")
            resolved_artist_name = metadata.get("artist", artist)

            # ── Step 6: Artist artwork ──
            step_idx = 6
            s = time.monotonic()
            yield emit_start(step_idx)
            try:
                from app.scraper.artist_album_scraper import get_artist_artwork
                artist_art = get_artist_artwork(resolved_artist_name, mb_artist_id=resolved_mb_artist)
                if artist_art.get("image_url"):
                    artwork_candidates.append(ArtworkCandidate(
                        url=artist_art["image_url"], source="artist_scraper", art_type="artist", applied=False,
                    ))
                if artist_art.get("fanart_url"):
                    artwork_candidates.append(ArtworkCandidate(
                        url=artist_art["fanart_url"], source="artist_scraper", art_type="fanart", applied=False,
                    ))
                if artist_art.get("mb_artist_id") and not resolved_mb_artist:
                    resolved_mb_artist = artist_art["mb_artist_id"]
            except Exception as e:
                pre_logs.append(f"[import-test] Artist artwork fetch failed: {e}")
            yield emit_done(step_idx, s)

            # ── Step 7: Album artwork ──
            step_idx = 7
            s = time.monotonic()
            yield emit_start(step_idx)
            if resolved_album_name:
                try:
                    from app.scraper.artist_album_scraper import (
                        get_album_artwork_musicbrainz, get_album_artwork_wikipedia,
                    )
                    if req.scrape_musicbrainz or req.ai_auto:
                        album_art_mb = get_album_artwork_musicbrainz(resolved_album_name, resolved_artist_name)
                        if album_art_mb.get("image_url"):
                            artwork_candidates.append(ArtworkCandidate(
                                url=album_art_mb["image_url"], source="album_scraper", art_type="album", applied=False,
                            ))
                    album_art_wiki = get_album_artwork_wikipedia(resolved_album_name, resolved_artist_name, wiki_url=_wiki_album_url)
                    if album_art_wiki.get("image_url"):
                        _existing_urls = {c.url for c in artwork_candidates}
                        if album_art_wiki["image_url"] not in _existing_urls:
                            artwork_candidates.append(ArtworkCandidate(
                                url=album_art_wiki["image_url"], source="album_scraper_wiki", art_type="album", applied=False,
                            ))
                except Exception as e:
                    pre_logs.append(f"[import-test] Album artwork fetch failed: {e}")
            yield emit_done(step_idx, s)

            # ── Step 8: CAA artwork ──
            step_idx = 8
            s = time.monotonic()
            yield emit_start(step_idx)
            from app.scraper.artwork_selection import fetch_caa_artwork
            _caa_validated, _caa_source, _caa_art_type = fetch_caa_artwork(
                mb_release_id=metadata.get("mb_release_id"),
                mb_release_group_id=metadata.get("mb_release_group_id"),
                mb_album_release_group_id=metadata.get("mb_album_release_group_id"),
                mb_album_release_id=metadata.get("mb_album_release_id"),
            )
            if _caa_validated:
                existing_urls = {c.url for c in artwork_candidates}
                if _caa_validated not in existing_urls:
                    artwork_candidates.append(ArtworkCandidate(
                        url=_caa_validated, source=_caa_source, art_type=_caa_art_type, applied=False,
                    ))

            final_image = metadata.get("image_url")
            if final_image:
                found = False
                for cand in artwork_candidates:
                    if cand.url == final_image:
                        cand.applied = True
                        found = True
                if not found:
                    artwork_candidates.append(ArtworkCandidate(url=final_image, source="unknown", art_type="poster", applied=True))
            yield emit_done(step_idx, s)

            # ── Step 9: Candidate priorities ──
            step_idx = 9
            s = time.monotonic()
            yield emit_start(step_idx)
            from app.scraper.artwork_selection import apply_candidate_priorities, is_single_release
            _is_single = is_single_release(
                metadata.get("mb_release_group_id"),
                metadata.get("mb_album_release_group_id"),
            )
            apply_candidate_priorities(artwork_candidates, is_single=_is_single)

            # Build AI changes
            ai_changes = []
            pre_snapshot = metadata.get("_pre_ai_snapshot", {})
            if pre_snapshot:
                _COMPARE_FIELDS = ("artist", "title", "album", "year", "genres", "plot", "image_url")
                for fn in _COMPARE_FIELDS:
                    bv = pre_snapshot.get(fn)
                    av = metadata.get(fn)
                    if str(bv) if bv is not None else "" != str(av) if av is not None else "":
                        ai_changes.append(BeforeAfterField(field=fn, before=bv, after=av, source="ai_review"))

            # Collect source URLs
            source_urls: Dict[str, str] = {k: v for k, v in metadata.get("_source_urls", {}).items() if v}
            if canonical:
                source_urls["video"] = canonical
            if metadata.get("mb_artist_id") and "musicbrainz_artist" not in source_urls:
                source_urls["musicbrainz_artist"] = f"https://musicbrainz.org/artist/{metadata['mb_artist_id']}"
            if metadata.get("mb_release_id") and "musicbrainz_release" not in source_urls:
                source_urls["musicbrainz_release"] = f"https://musicbrainz.org/release/{metadata['mb_release_id']}"
            if "coverartarchive" not in source_urls and _caa_validated:
                if _caa_art_type == "poster" and metadata.get("mb_release_group_id"):
                    source_urls["coverartarchive"] = f"https://coverartarchive.org/release-group/{metadata['mb_release_group_id']}"
                elif metadata.get("mb_release_id"):
                    source_urls["coverartarchive"] = f"https://coverartarchive.org/release/{metadata['mb_release_id']}"
            if metadata.get("mb_release_group_id") and "musicbrainz_release_group" not in source_urls:
                source_urls["musicbrainz_release_group"] = f"https://musicbrainz.org/release-group/{metadata['mb_release_group_id']}"
            if metadata.get("mb_album_release_id") and "musicbrainz_album_release" not in source_urls:
                source_urls["musicbrainz_album_release"] = f"https://musicbrainz.org/release/{metadata['mb_album_release_id']}"
            if metadata.get("mb_album_release_group_id") and "musicbrainz_album" not in source_urls:
                source_urls["musicbrainz_album"] = f"https://musicbrainz.org/release-group/{metadata['mb_album_release_group_id']}"
            if _wiki_album_url and "wikipedia_album" not in source_urls:
                source_urls["wikipedia_album"] = _wiki_album_url
            if _wiki_artist_url and "wikipedia_artist" not in source_urls:
                source_urls["wikipedia_artist"] = _wiki_artist_url
            yield emit_done(step_idx, s)

            # ── Step 10: Write output file ──
            step_idx = 10
            s = time.monotonic()
            yield emit_start(step_idx)

            # Build a minimal request-like object for the trace file writer
            class _FakeReq:
                url = source_path
                artist_override = req.artist_override
                title_override = req.title_override
                scrape_wikipedia = req.scrape_wikipedia
                scrape_musicbrainz = req.scrape_musicbrainz
                ai_auto = req.ai_auto
                ai_only = req.ai_only

            output_file_path = _write_scraper_test_file(
                req=_FakeReq(),
                mode=f"Import: {mode}",
                provider="library",
                video_id=chosen_file,
                canonical=canonical,
                ytdlp_meta=ytdlp_meta,
                parsed_artist=parsed_artist or fn_artist or "",
                parsed_title=parsed_title or fn_title or "",
                artist=artist,
                artist_source=artist_source,
                title=title,
                title_source=title_source,
                metadata=metadata,
                sources_used=sources_used,
                source_urls=source_urls,
                artwork_candidates=artwork_candidates,
                ai_changes=ai_changes,
                pre_logs=pre_logs,
                logs=logs,
            )
            yield emit_done(step_idx, s)

            # ── Emit final result ──
            result = ScraperTestResult(
                url=source_path,
                canonical_url=canonical or source_path,
                provider="library",
                video_id=chosen_file,
                ytdlp_title=ytdlp_meta.get("title"),
                ytdlp_uploader=ytdlp_meta.get("uploader"),
                ytdlp_channel=ytdlp_meta.get("channel"),
                ytdlp_artist=ytdlp_meta.get("artist"),
                ytdlp_track=ytdlp_meta.get("track"),
                ytdlp_album=ytdlp_meta.get("album"),
                ytdlp_duration=ytdlp_meta.get("duration") or ffprobe_data.get("duration_seconds"),
                ytdlp_upload_date=ytdlp_meta.get("upload_date"),
                ytdlp_thumbnail=ytdlp_meta.get("thumbnail"),
                ytdlp_description=(ytdlp_meta.get("description") or "")[:500],
                ytdlp_tags=ytdlp_meta.get("tags", [])[:20],
                parsed_artist=parsed_artist or fn_artist or "",
                parsed_title=parsed_title or fn_title or "",
                artist=_prov("artist", resolved_artist, artist_source),
                title=_prov("title", resolved_title, title_source),
                album=_prov("album", metadata.get("album") or parsed_album, album_source),
                year=_prov("year", metadata.get("year") or parsed_year, year_source),
                genres=_prov("genres", metadata.get("genres") or parsed_genres, genre_source),
                plot=_prov("plot", metadata.get("plot") or parsed_plot, plot_source),
                image_url=_prov("image_url", metadata.get("image_url"), image_source),
                mb_artist_id=_prov("mb_artist_id", metadata.get("mb_artist_id"), mb_source),
                mb_recording_id=_prov("mb_recording_id", metadata.get("mb_recording_id"), mb_source),
                mb_release_id=_prov("mb_release_id", metadata.get("mb_release_id"), mb_source),
                mb_release_group_id=_prov("mb_release_group_id", metadata.get("mb_release_group_id"), mb_source),
                imdb_url=_prov("imdb_url", metadata.get("imdb_url"), imdb_source),
                source_urls=source_urls,
                artwork_candidates=artwork_candidates,
                ai_changes=ai_changes,
                scraper_sources_used=sources_used,
                pipeline_log=pre_logs + metadata.get("pipeline_log", []),
                pipeline_failures=metadata.get("pipeline_failures", []),
                mode=f"Import: {mode}",
                ai_source_resolution=metadata.get("ai_source_resolution"),
                ai_final_review=metadata.get("ai_final_review"),
                output_file=output_file_path,
                # Import-specific fields
                import_directory=directory,
                import_file=chosen_file,
                import_identity_source=identity_source,
                import_nfo_found=nfo_path is not None,
                import_youtube_match=yt_match,
                import_quality=ffprobe_data if ffprobe_data else None,
            )

            yield _sse("result", result.model_dump())
            yield _sse("done", {"total_ms": round((time.monotonic() - t0) * 1000)})

        except Exception as e:
            logger.exception("Import test stream error")
            yield _sse("fail", {"detail": str(e)})
        finally:
            db.close()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
