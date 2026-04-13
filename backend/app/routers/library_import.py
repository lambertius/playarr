"""
Library Import API — Scan external directories and import existing video libraries.

Endpoints:
    POST /api/library-import/scan     Scan a directory and return found videos with parsed metadata
    POST /api/library-import/start    Start the import process with selected options
    GET  /api/library-import/preview  Preview regex pattern on sample filenames
"""
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ProcessingJob, JobStatus, VideoItem, Source
from app.worker import dispatch_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/library-import", tags=["Library Import"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    directory: str = Field(..., description="Absolute path to scan for video files")
    recursive: bool = Field(True, description="Scan subdirectories recursively")
    custom_regex: Optional[str] = Field(None, description="Custom regex with named groups (artist, title, year, resolution)")


class ScannedItem(BaseModel):
    file_path: str
    folder_path: str
    folder_name: str
    filename: str
    file_size_bytes: int
    # Parsed metadata (from NFO if available, otherwise filename)
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    genres: List[str] = []
    plot: Optional[str] = None
    resolution: Optional[str] = None
    source_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    # Metadata source info
    has_nfo: bool = False
    has_poster: bool = False
    has_thumb: bool = False
    metadata_source: str = "filename"  # "nfo", "filename", "regex"
    # Whether this video already exists in the library
    already_exists: bool = False
    existing_video_id: Optional[int] = None


class ScanResponse(BaseModel):
    total_found: int
    items: List[ScannedItem]
    already_in_library: int
    new_items: int
    scan_is_library: bool = False  # True when scanned dir is inside the library


class RegexPreviewRequest(BaseModel):
    pattern: str = Field(..., description="Regex pattern with named groups")
    filenames: List[str] = Field(..., description="Sample filenames to test against")


class RegexPreviewResult(BaseModel):
    filename: str
    matched: bool
    artist: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    resolution: Optional[str] = None


class RegexPreviewResponse(BaseModel):
    results: List[RegexPreviewResult]
    match_count: int
    total: int


class ImportOptions(BaseModel):
    """Configuration for the library import process."""
    # Core settings
    mode: str = Field("simple", description="'simple' or 'advanced'")
    file_handling: str = Field("copy", description="'copy', 'move', 'copy_to', 'move_to', or 'in_place'")
    custom_destination: Optional[str] = Field(None, description="Custom destination directory for copy_to/move_to modes")
    normalize_audio: bool = Field(False, description="Run audio normalization on imported videos")

    # YouTube source matching
    find_source_video: bool = Field(False, description="Search YouTube for source video links")
    source_match_duration: bool = Field(True, description="Use duration matching when finding source videos")
    source_match_min_confidence: float = Field(0.6, ge=0.0, le=1.0, description="Min confidence for auto-linking source")

    # Review mode
    review_mode: str = Field("skip", description="'basic' (all to review), 'advanced' (confidence-based), 'skip' (auto-approve)")
    # Advanced review: fields that must match perfectly, else push to review
    critical_fields: List[str] = Field(default_factory=list, description="Fields requiring perfect match for auto-approve (year, album, artist)")
    confidence_threshold: float = Field(0.8, ge=0.0, le=1.0, description="Min confidence for auto-approve in advanced review mode")

    # Metadata sources (advanced mode)
    scrape_wikipedia: bool = Field(True, description="Scrape Wikipedia for metadata (advanced mode)")
    scrape_musicbrainz: bool = Field(True, description="Scrape MusicBrainz for metadata (advanced mode)")
    scrape_tmvdb: bool = Field(False, description="Retrieve metadata from The Music Video DB (advanced mode)")
    ai_auto_analyse: bool = Field(False, description="Full AI enrichment after import (advanced mode)")
    ai_auto_fallback: bool = Field(False, description="AI enrichment only, no external scrapers (advanced mode)")

    # Custom regex for filename parsing
    custom_regex: Optional[str] = Field(None, description="Custom regex pattern for filename parsing")


class DuplicateAction(BaseModel):
    """User decision for a detected duplicate."""
    action: str = Field(..., description="'skip', 'overwrite', 'keep_both', 'review_later'")
    version_type: Optional[str] = Field(None, description="Version label for keep_both (e.g. 'alternate', 'cover', 'live')")


class ImportStartRequest(BaseModel):
    """Request to start a library import."""
    directory: str
    items: List[str] = Field(..., description="List of file_path values to import (from scan results)")
    options: ImportOptions
    duplicate_actions: Dict[str, DuplicateAction] = Field(
        default_factory=dict,
        description="Map of file_path -> action for items that already exist in library",
    )


class ImportStartResponse(BaseModel):
    job_id: int
    total_items: int
    message: str


class ExistingVideoDetail(BaseModel):
    """Summary of an existing library video for comparison."""
    id: int
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    resolution_label: Optional[str] = None
    version_type: Optional[str] = None
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    has_poster: bool = False
    has_thumb: bool = False
    song_rating: Optional[float] = None
    video_rating: Optional[float] = None
    created_at: Optional[str] = None


class ExistingDetailsRequest(BaseModel):
    video_ids: List[int] = Field(..., description="List of existing_video_id values to fetch")


class ExistingDetailsResponse(BaseModel):
    videos: Dict[int, ExistingVideoDetail]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/scan", response_model=ScanResponse)
def scan_directory(req: ScanRequest, db: Session = Depends(get_db)):
    """
    Scan a directory for video files and return parsed metadata for each.

    For each file found:
    1. Check for a sibling .nfo file and parse it
    2. Fall back to filename parsing (built-in patterns or custom regex)
    3. Check if the video already exists in the library (by file path or artist+title)
    """
    from app.services.filename_parser import scan_directory_for_videos, parse_filename
    from app.services.nfo_parser import find_nfo_for_video, parse_nfo_file, find_artwork_for_video

    if not os.path.isdir(req.directory):
        raise HTTPException(status_code=400, detail=f"Directory not found: {req.directory}")

    # Scan for video files
    raw_files = scan_directory_for_videos(req.directory, recursive=req.recursive)

    items: List[ScannedItem] = []
    already_count = 0

    for entry in raw_files:
        file_path = entry["file_path"]

        item = ScannedItem(
            file_path=file_path,
            folder_path=entry["folder_path"],
            folder_name=entry["folder_name"],
            filename=entry["filename"],
            file_size_bytes=entry["file_size_bytes"],
        )

        # Check for NFO
        nfo_path = find_nfo_for_video(file_path)
        if nfo_path:
            item.has_nfo = True
            parsed = parse_nfo_file(nfo_path)
            if parsed:
                item.artist = parsed.artist
                item.title = parsed.title
                item.album = parsed.album
                item.year = parsed.year
                item.genres = parsed.genres
                item.plot = parsed.plot
                item.source_url = parsed.source_url
                item.metadata_source = "nfo"
                if parsed.video_height:
                    item.resolution = f"{parsed.video_height}p"
                if parsed.runtime_minutes:
                    item.duration_seconds = parsed.runtime_minutes * 60

        # Fall back to filename parsing if NFO didn't provide enough data
        if not item.artist or not item.title:
            parsed_fn = parse_filename(entry["filename"], custom_pattern=req.custom_regex)
            if not item.artist and parsed_fn.artist:
                item.artist = parsed_fn.artist
            if not item.title and parsed_fn.title:
                item.title = parsed_fn.title
            if not item.resolution and parsed_fn.resolution:
                item.resolution = parsed_fn.resolution
            if not item.year and parsed_fn.year:
                item.year = parsed_fn.year
            if item.metadata_source != "nfo":
                item.metadata_source = "regex" if req.custom_regex else "filename"

        # Check for artwork
        artwork = find_artwork_for_video(file_path)
        item.has_poster = artwork["poster"] is not None
        item.has_thumb = artwork["thumb"] is not None

        # Check if already in library
        existing = _find_existing_video(db, file_path, item.artist, item.title)
        if existing:
            item.already_exists = True
            item.existing_video_id = existing.id
            already_count += 1

        items.append(item)

    # Detect if scanned directory is inside (or is) the library
    from app.config import get_settings
    _settings = get_settings()
    _norm_scan = os.path.normcase(os.path.normpath(req.directory))
    _norm_lib = os.path.normcase(os.path.normpath(_settings.library_dir))
    _scan_is_library = _norm_scan == _norm_lib or _norm_scan.startswith(_norm_lib + os.sep)

    return ScanResponse(
        total_found=len(items),
        items=items,
        already_in_library=already_count,
        new_items=len(items) - already_count,
        scan_is_library=_scan_is_library,
    )


@router.post("/preview-regex", response_model=RegexPreviewResponse)
def preview_regex(req: RegexPreviewRequest):
    """Test a regex pattern against sample filenames."""
    from app.services.filename_parser import parse_filename

    results = []
    match_count = 0
    for fn in req.filenames:
        parsed = parse_filename(fn, custom_pattern=req.pattern)
        matched = parsed.pattern_name == "custom"
        if matched:
            match_count += 1
        results.append(RegexPreviewResult(
            filename=fn,
            matched=matched,
            artist=parsed.artist,
            title=parsed.title,
            year=parsed.year,
            resolution=parsed.resolution,
        ))

    return RegexPreviewResponse(
        results=results,
        match_count=match_count,
        total=len(req.filenames),
    )


@router.post("/existing-details", response_model=ExistingDetailsResponse)
def get_existing_details(req: ExistingDetailsRequest, db: Session = Depends(get_db)):
    """
    Fetch details of existing library videos for duplicate comparison.
    """
    from app.models import MediaAsset

    if not req.video_ids:
        return ExistingDetailsResponse(videos={})

    videos = db.query(VideoItem).filter(VideoItem.id.in_(req.video_ids)).all()
    result: Dict[int, ExistingVideoDetail] = {}
    for v in videos:
        # Check for poster/thumb assets
        has_poster = db.query(MediaAsset).filter(
            MediaAsset.video_id == v.id,
            MediaAsset.asset_type == "poster",
        ).first() is not None
        has_thumb = db.query(MediaAsset).filter(
            MediaAsset.video_id == v.id,
            MediaAsset.asset_type == "thumb",
        ).first() is not None

        result[v.id] = ExistingVideoDetail(
            id=v.id,
            artist=v.artist,
            title=v.title,
            album=v.album,
            year=v.year,
            resolution_label=v.resolution_label,
            version_type=v.version_type or "normal",
            file_path=v.file_path,
            file_size_bytes=v.file_size_bytes,
            has_poster=has_poster,
            has_thumb=has_thumb,
            song_rating=v.song_rating,
            video_rating=v.video_rating,
            created_at=v.created_at.isoformat() if v.created_at else None,
        )

    return ExistingDetailsResponse(videos=result)


@router.post("/start", response_model=ImportStartResponse)
def start_import(req: ImportStartRequest, db: Session = Depends(get_db)):
    """
    Start a library import process.

    Creates a parent tracking job and individual child jobs for each video.
    Includes duplicate_actions for items the user chose to overwrite/keep_both/review.
    """
    from app.tasks import library_import_task

    if not req.items:
        raise HTTPException(status_code=400, detail="No items selected for import")

    if not os.path.isdir(req.directory):
        raise HTTPException(status_code=400, detail=f"Directory not found: {req.directory}")

    # Block AI modes when no AI provider is configured
    if req.options.ai_auto_analyse or req.options.ai_auto_fallback:
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

    # Serialize duplicate actions
    dup_actions = {k: v.model_dump() for k, v in req.duplicate_actions.items()}

    # Create parent tracking job
    _lib_label = "Library Import (AI Only)" if req.options.ai_auto_fallback else "Library Import (AI Auto)" if req.options.ai_auto_analyse else "Library Import"
    parent = ProcessingJob(
        job_type="library_import",
        status=JobStatus.queued,
        display_name=f"Library Import ({len(req.items)} videos)",
        action_label=_lib_label,
        input_params={
            "directory": req.directory,
            "file_paths": req.items,
            "options": req.options.model_dump(),
            "duplicate_actions": dup_actions,
        },
        started_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    # Dispatch the import task
    dispatch_task(
        library_import_task,
        job_id=parent.id,
    )

    return ImportStartResponse(
        job_id=parent.id,
        total_items=len(req.items),
        message=f"Import started for {len(req.items)} videos",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_existing_video(
    db: Session,
    file_path: str,
    artist: Optional[str],
    title: Optional[str],
) -> Optional[VideoItem]:
    """Check if a video already exists in the library."""
    # Check by file path
    existing = db.query(VideoItem).filter(VideoItem.file_path == file_path).first()
    if existing:
        return existing

    # Check by normalized file path (in case of mount path differences)
    norm_path = os.path.normpath(file_path)
    existing = db.query(VideoItem).filter(VideoItem.file_path == norm_path).first()
    if existing:
        return existing

    # Check by artist + title (fuzzy — case-insensitive)
    if artist and title:
        from sqlalchemy import func
        existing = db.query(VideoItem).filter(
            func.lower(VideoItem.artist) == artist.lower(),
            func.lower(VideoItem.title) == title.lower(),
        ).first()
        if existing:
            return existing

        # Fallback: primary artist prefix match + title.
        # Handles filenames without feat./featuring separators
        # (e.g. "A Great Big World Christina Aguilera" vs
        #  "A Great Big World feat. Christina Aguilera").
        from app.services.source_validation import parse_multi_artist
        query_primary, _ = parse_multi_artist(artist)
        qp_lower = query_primary.lower()
        title_matches = db.query(VideoItem).filter(
            func.lower(VideoItem.title) == title.lower(),
        ).all()
        for candidate in title_matches:
            db_primary, _ = parse_multi_artist(candidate.artist or "")
            dp_lower = db_primary.lower()
            if dp_lower == qp_lower:
                return candidate
            if qp_lower.startswith(dp_lower) or dp_lower.startswith(qp_lower):
                return candidate

        # Fuzzy fallback: comparison-key matching strips punctuation,
        # accents, and special characters (AC/DC≈ACDC, Don't≈Dont,
        # blink-182≈Blink182, Gotye; Kimbra≈Gotye Kimbra, etc.)
        from app.matching.normalization import make_comparison_key
        incoming_artist_key = make_comparison_key(artist)
        incoming_title_key = make_comparison_key(title)
        if incoming_artist_key and incoming_title_key:
            all_videos = db.query(VideoItem).filter(
                VideoItem.artist.isnot(None),
                VideoItem.title.isnot(None),
            ).all()
            for candidate in all_videos:
                if (make_comparison_key(candidate.artist or "") == incoming_artist_key
                        and make_comparison_key(candidate.title or "") == incoming_title_key):
                    return candidate

    return None
