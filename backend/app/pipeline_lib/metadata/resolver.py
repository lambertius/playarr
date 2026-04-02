# AUTO-SEPARATED from metadata/resolver.py for pipeline_lib pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Metadata Resolver — Matching, confidence scoring, and merge strategy.

This module orchestrates multiple providers to produce canonical
Artist / Album / Track entities with the best available metadata.

Merge rules
-----------
* Structured fields (canonical_name, sort_name, MBID, year, release_date)
  prefer MusicBrainz (highest confidence for structured data).
* Narrative text (biography, plot) prefers Wikipedia.
* Genres are merged from all sources, deduplicated.
* Artwork is ranked by confidence; highest wins per ``kind``.
* Per-field provenance is tracked so the UI can display origin.
* Confidence scores determine which candidate wins a conflict.

Confidence thresholds
---------------------
* ≥ 0.85  — high confidence, auto-accept
* 0.50–0.84 — medium confidence, accept but flag needs_review=False
* < 0.50  — low confidence, flag needs_review=True

Fuzzy matching
--------------
Uses normalized Levenshtein distance for name matching when MBID is
unavailable.  ``difflib.SequenceMatcher`` is used (stdlib, no extra deps).
"""
import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from app.database import SessionLocal
from app.metadata.models import (
    ArtistEntity, AlbumEntity, TrackEntity, Genre,
)
from app.metadata.providers.base import MetadataProvider, ProviderResult, AssetCandidate
from app.pipeline_lib.metadata.providers.wikipedia import WikipediaProvider
from app.pipeline_lib.metadata.providers.musicbrainz import MusicBrainzProvider
from app.metadata.providers.coverartarchive import CoverArtArchiveProvider

logger = logging.getLogger(__name__)

# Singleton provider instances (created lazily)
_providers: Optional[List[MetadataProvider]] = None


def _get_providers() -> List[MetadataProvider]:
    global _providers
    if _providers is None:
        _providers = [
            MusicBrainzProvider(),
            WikipediaProvider(),
            CoverArtArchiveProvider(),
        ]
    return _providers


def _filter_providers(providers: List[MetadataProvider],
                      skip_musicbrainz: bool = False,
                      skip_wikipedia: bool = False) -> List[MetadataProvider]:
    """Return providers filtered by the active skip flags."""
    if not skip_musicbrainz and not skip_wikipedia:
        return providers
    filtered = []
    for p in providers:
        if skip_musicbrainz and p.name in ("musicbrainz", "coverartarchive"):
            continue
        if skip_wikipedia and p.name == "wikipedia":
            continue
        filtered.append(p)
    return filtered


# ---------------------------------------------------------------------------
# Fuzzy matching helpers
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Lower-case, strip, collapse whitespace."""
    import re
    return re.sub(r"\s+", " ", s.strip().lower())


def fuzzy_score(a: str, b: str) -> float:
    """Return 0.0–1.0 similarity between two strings."""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------

def _pick_best(field: str, candidates: List[ProviderResult],
               prefer: Optional[str] = None) -> Tuple[Any, str]:
    """
    Pick the best value for *field* across candidates.

    If *prefer* is given (a provider name), tie-break in its favour.
    The highest-confidence candidate that explicitly includes the field
    (even as None) is authoritative — a lower-confidence candidate's
    non-None value cannot override it.
    Returns (value, provenance).
    """
    # Track the highest-confidence candidate that has this field at all
    top_val = None
    top_conf = -1.0
    top_prov = ""
    for c in candidates:
        if field not in c.fields:
            continue
        eff_conf = c.confidence
        if prefer and c.provenance == prefer:
            eff_conf += 0.05  # slight preference boost
        if eff_conf > top_conf:
            top_val = c.fields[field]
            top_conf = eff_conf
            top_prov = c.provenance
    return top_val, top_prov


def _merge_genres(candidates: List[ProviderResult]) -> List[str]:
    """Merge genre lists from all candidates, deduplicated."""
    seen: set = set()
    merged: List[str] = []
    for c in candidates:
        for g in c.fields.get("genres", []):
            low = g.lower()
            if low not in seen:
                seen.add(low)
                merged.append(g)
    return merged


def _merge_assets(all_assets: List[AssetCandidate]) -> Dict[str, AssetCandidate]:
    """
    Merge asset candidates — keep highest-confidence per kind.
    Returns dict: kind → AssetCandidate.
    """
    best: Dict[str, AssetCandidate] = {}
    for a in all_assets:
        existing = best.get(a.kind)
        if not existing or a.confidence > existing.confidence:
            best[a.kind] = a
    return best


# ---------------------------------------------------------------------------
# Entity resolution: Artist
# ---------------------------------------------------------------------------

def resolve_artist(name: str, mb_artist_id: Optional[str] = None,
                   skip_musicbrainz: bool = False,
                   skip_wikipedia: bool = False) -> Dict[str, Any]:
    """
    Resolve an artist name to canonical metadata using all providers.

    Returns a dict with:
        canonical_name, sort_name, mb_artist_id, country, disambiguation,
        biography, genres, aliases, confidence, needs_review,
        assets (dict of kind→AssetCandidate), field_provenance
    """
    providers = _filter_providers(_get_providers(), skip_musicbrainz, skip_wikipedia)
    candidates: List[ProviderResult] = []
    all_assets: List[AssetCandidate] = []

    # If we have an MBID, fetch directly
    if mb_artist_id:
        for p in providers:
            try:
                r = p.get_artist(mb_artist_id)
                if r:
                    candidates.append(r)
            except Exception as e:
                logger.warning(f"Provider {p.name} get_artist failed: {e}")

    # Search by name across providers
    for p in providers:
        try:
            results = p.search_artist(name)
            candidates.extend(results)
        except Exception as e:
            logger.warning(f"Provider {p.name} search_artist failed: {e}")

    # Gather assets
    for p in providers:
        try:
            mbid = mb_artist_id
            if not mbid:
                for c in candidates:
                    if c.fields.get("mb_artist_id"):
                        mbid = c.fields["mb_artist_id"]
                        break
            assets = p.get_artist_assets(name, mbid=mbid)
            all_assets.extend(assets)
        except Exception as e:
            logger.warning(f"Provider {p.name} get_artist_assets failed: {e}")

    if not candidates:
        return {
            "canonical_name": name,
            "confidence": 0.3,
            "needs_review": True,
            "genres": [],
            "assets": {},
            "field_provenance": {},
        }

    # Merge: structured fields prefer MusicBrainz, narrative prefers Wikipedia
    field_prov: Dict[str, str] = {}

    canonical_name, pn = _pick_best("canonical_name", candidates, prefer="musicbrainz")
    field_prov["canonical_name"] = pn
    sort_name, pn = _pick_best("sort_name", candidates, prefer="musicbrainz")
    field_prov["sort_name"] = pn
    mbid, pn = _pick_best("mb_artist_id", candidates, prefer="musicbrainz")
    field_prov["mb_artist_id"] = pn
    country, pn = _pick_best("country", candidates, prefer="musicbrainz")
    field_prov["country"] = pn
    disambiguation, pn = _pick_best("disambiguation", candidates, prefer="musicbrainz")
    field_prov["disambiguation"] = pn
    biography, pn = _pick_best("biography", candidates, prefer="wikipedia")
    field_prov["biography"] = pn
    aliases, pn = _pick_best("aliases", candidates, prefer="musicbrainz")
    field_prov["aliases"] = pn

    genres = _merge_genres(candidates)
    best_assets = _merge_assets(all_assets)

    # Overall confidence = max across candidates
    overall_conf = max(c.confidence for c in candidates)
    needs_review = overall_conf < 0.50

    return {
        "canonical_name": canonical_name or name,
        "sort_name": sort_name,
        "mb_artist_id": mbid or mb_artist_id,
        "country": country,
        "disambiguation": disambiguation,
        "biography": biography,
        "aliases": aliases,
        "genres": genres,
        "confidence": overall_conf,
        "needs_review": needs_review,
        "assets": best_assets,
        "field_provenance": field_prov,
    }


# ---------------------------------------------------------------------------
# Entity resolution: Album
# ---------------------------------------------------------------------------

def resolve_album(artist: str, title: str,
                  mb_release_id: Optional[str] = None,
                  skip_musicbrainz: bool = False,
                  skip_wikipedia: bool = False) -> Dict[str, Any]:
    """
    Resolve an album to canonical metadata.

    Returns dict with:
        title, year, release_date, mb_release_id, album_type, genres,
        confidence, needs_review, assets, field_provenance
    """
    providers = _filter_providers(_get_providers(), skip_musicbrainz, skip_wikipedia)
    candidates: List[ProviderResult] = []
    all_assets: List[AssetCandidate] = []

    if mb_release_id:
        for p in providers:
            try:
                r = p.get_album(mb_release_id)
                if r:
                    candidates.append(r)
            except Exception as e:
                logger.warning(f"Provider {p.name} get_album failed: {e}")

    for p in providers:
        try:
            results = p.search_album(artist, title)
            candidates.extend(results)
        except Exception as e:
            logger.warning(f"Provider {p.name} search_album failed: {e}")

    # Assets
    for p in providers:
        try:
            mbid = mb_release_id
            if not mbid:
                for c in candidates:
                    if c.fields.get("mb_release_id"):
                        mbid = c.fields["mb_release_id"]
                        break
            assets = p.get_album_assets(artist, title, mbid=mbid)
            all_assets.extend(assets)
        except Exception as e:
            logger.warning(f"Provider {p.name} get_album_assets failed: {e}")

    if not candidates:
        return {
            "title": title,
            "confidence": 0.3,
            "needs_review": True,
            "genres": [],
            "assets": {},
            "field_provenance": {},
        }

    field_prov: Dict[str, str] = {}
    resolved_title, pn = _pick_best("title", candidates, prefer="musicbrainz")
    field_prov["title"] = pn
    year, pn = _pick_best("year", candidates, prefer="musicbrainz")
    field_prov["year"] = pn
    release_date, pn = _pick_best("release_date", candidates, prefer="musicbrainz")
    field_prov["release_date"] = pn
    mbid, pn = _pick_best("mb_release_id", candidates, prefer="musicbrainz")
    field_prov["mb_release_id"] = pn
    album_type, pn = _pick_best("album_type", candidates, prefer="musicbrainz")
    field_prov["album_type"] = pn

    genres = _merge_genres(candidates)
    best_assets = _merge_assets(all_assets)
    overall_conf = max(c.confidence for c in candidates)

    return {
        "title": resolved_title or title,
        "year": year,
        "release_date": release_date,
        "mb_release_id": mbid or mb_release_id,
        "album_type": album_type,
        "genres": genres,
        "confidence": overall_conf,
        "needs_review": overall_conf < 0.50,
        "assets": best_assets,
        "field_provenance": field_prov,
    }


# ---------------------------------------------------------------------------
# Entity resolution: Track
# ---------------------------------------------------------------------------

def resolve_track(artist: str, title: str,
                  mb_recording_id: Optional[str] = None,
                  skip_musicbrainz: bool = False,
                  skip_wikipedia: bool = False) -> Dict[str, Any]:
    """
    Resolve a track / recording to canonical metadata.

    Returns dict with:
        title, artist, album, year, mb_recording_id, mb_release_id,
        mb_artist_id, genres, plot, duration_seconds,
        confidence, needs_review, field_provenance
    """
    providers = _filter_providers(_get_providers(), skip_musicbrainz, skip_wikipedia)
    candidates: List[ProviderResult] = []

    if mb_recording_id:
        for p in providers:
            try:
                r = p.get_track(mb_recording_id)
                if r:
                    candidates.append(r)
            except Exception as e:
                logger.warning(f"Provider {p.name} get_track failed: {e}")

    for p in providers:
        try:
            results = p.search_track(artist, title)
            candidates.extend(results)
        except Exception as e:
            logger.warning(f"Provider {p.name} search_track failed: {e}")

    # Assets
    all_assets: List[AssetCandidate] = []
    for p in providers:
        try:
            assets = p.get_track_assets(artist, title, mbid=mb_recording_id)
            all_assets.extend(assets)
        except Exception as e:
            logger.warning(f"Provider {p.name} get_track_assets failed: {e}")

    if not candidates:
        # Still merge any assets found even without metadata candidates
        best_assets = _merge_assets(all_assets) if all_assets else {}
        return {
            "title": title,
            "artist": artist,
            "confidence": 0.3,
            "needs_review": True,
            "genres": [],
            "assets": best_assets,
            "field_provenance": {},
        }

    field_prov: Dict[str, str] = {}
    resolved_title, pn = _pick_best("title", candidates, prefer="musicbrainz")
    field_prov["title"] = pn
    resolved_artist, pn = _pick_best("artist", candidates, prefer="musicbrainz")
    field_prov["artist"] = pn
    album, pn = _pick_best("album", candidates, prefer="musicbrainz")
    field_prov["album"] = pn
    year, pn = _pick_best("year", candidates, prefer="musicbrainz")
    field_prov["year"] = pn
    rec_id, pn = _pick_best("mb_recording_id", candidates, prefer="musicbrainz")
    field_prov["mb_recording_id"] = pn
    rel_id, pn = _pick_best("mb_release_id", candidates, prefer="musicbrainz")
    field_prov["mb_release_id"] = pn
    art_id, pn = _pick_best("mb_artist_id", candidates, prefer="musicbrainz")
    field_prov["mb_artist_id"] = pn
    plot, pn = _pick_best("plot", candidates, prefer="wikipedia")
    field_prov["plot"] = pn
    duration, pn = _pick_best("duration_seconds", candidates, prefer="musicbrainz")
    field_prov["duration_seconds"] = pn

    genres = _merge_genres(candidates)
    best_assets = _merge_assets(all_assets)
    overall_conf = max(c.confidence for c in candidates)

    return {
        "title": resolved_title or title,
        "artist": resolved_artist or artist,
        "album": album,
        "year": year,
        "mb_recording_id": rec_id or mb_recording_id,
        "mb_release_id": rel_id,
        "mb_artist_id": art_id,
        "genres": genres,
        "plot": plot,
        "duration_seconds": duration,
        "confidence": overall_conf,
        "needs_review": overall_conf < 0.50,
        "assets": best_assets,
        "field_provenance": field_prov,
    }


# ---------------------------------------------------------------------------
# DB entity upsert helpers (get-or-create with merge)
# ---------------------------------------------------------------------------

def get_or_create_artist(db, name: str, resolved: Optional[Dict] = None) -> ArtistEntity:
    """
    Find or create an ArtistEntity, optionally merging resolved metadata.

    Matching priority: MBID → canonical_name (case-insensitive).
    Safeguard: resolved canonical_name must be similar to input name
    (fuzzy_score >= 0.65) to prevent cross-contamination from wrong
    scraper results.
    """
    from app.tasks import _get_or_create_genre

    if resolved is None:
        resolved = {}

    mbid = resolved.get("mb_artist_id")
    canonical = resolved.get("canonical_name", name)

    # Safeguard: if resolved canonical name diverges significantly from input,
    # it likely came from a wrong Wikipedia/scraper match — fall back to input.
    if canonical and name and canonical.lower() != name.lower():
        sim = fuzzy_score(canonical, name)
        if sim < 0.65:
            logger.warning(
                f"Entity safeguard: resolved canonical '{canonical}' diverges from "
                f"input '{name}' (similarity={sim:.2f}), falling back to input name"
            )
            canonical = name

    # Try MBID match first
    entity = None
    if mbid:
        entity = db.query(ArtistEntity).filter(ArtistEntity.mb_artist_id == mbid).first()
        # Cross-validate: MBID-matched entity name must be similar to the input.
        # A low similarity indicates the entity was contaminated with the wrong MBID.
        if entity and name:
            sim = fuzzy_score(entity.canonical_name, name)
            if sim < 0.65:
                # Before rejecting, try with primary artist only (strip feat./ft. credits)
                import re as _re
                _primary = _re.split(r'\s+(?:feat\.?|ft\.?|featuring)\s+', name, maxsplit=1, flags=_re.IGNORECASE)[0].strip()
                _primary_sim = fuzzy_score(entity.canonical_name, _primary) if _primary != name else 0.0
                if _primary_sim >= 0.65:
                    logger.info(
                        f"Entity safeguard: MBID match accepted via primary artist "
                        f"'{_primary}' (similarity={_primary_sim:.2f}) for input '{name}'"
                    )
                else:
                    logger.warning(
                        f"Entity safeguard: MBID-matched entity '{entity.canonical_name}' "
                        f"diverges from input '{name}' (similarity={sim:.2f}), ignoring MBID match"
                    )
                    entity = None
                    mbid = None  # Don't carry rejected MBID to a new entity

    # Fallback to name match
    if not entity:
        entity = db.query(ArtistEntity).filter(
            ArtistEntity.canonical_name.ilike(canonical)
        ).first()
        # Also try matching on primary artist (without feat. credits)
        if not entity:
            import re as _re
            _primary = _re.split(r'\s+(?:feat\.?|ft\.?|featuring)\s+', canonical, maxsplit=1, flags=_re.IGNORECASE)[0].strip()
            if _primary.lower() != canonical.lower():
                entity = db.query(ArtistEntity).filter(
                    ArtistEntity.canonical_name.ilike(_primary)
                ).first()

    if entity:
        # Always refresh metadata fields from resolved data when available,
        # regardless of confidence — re-resolution should correct stale data.
        if resolved:
            for field in ["sort_name", "country", "disambiguation", "biography", "aliases"]:
                if field in resolved:
                    setattr(entity, field, resolved[field])
            if mbid and not entity.mb_artist_id:
                entity.mb_artist_id = mbid
            # Only increase confidence, never decrease
            new_conf = resolved.get("confidence", 0)
            if new_conf >= entity.confidence:
                entity.confidence = new_conf
                entity.needs_review = resolved.get("needs_review", entity.needs_review)
            # Update genres
            if resolved.get("genres"):
                entity.genres.clear()
                db.flush()
                seen_ids = set()
                for g in resolved["genres"]:
                    genre = _get_or_create_genre(db, g)
                    if genre.id not in seen_ids:
                        seen_ids.add(genre.id)
                        entity.genres.append(genre)
        return entity

    # Create new
    entity = ArtistEntity(
        canonical_name=canonical,
        sort_name=resolved.get("sort_name"),
        mb_artist_id=mbid,
        country=resolved.get("country"),
        disambiguation=resolved.get("disambiguation"),
        biography=resolved.get("biography"),
        aliases=resolved.get("aliases"),
        confidence=resolved.get("confidence", 0.5),
        needs_review=resolved.get("needs_review", True),
    )
    db.add(entity)
    db.flush()

    if resolved.get("genres"):
        seen_ids = set()
        for g in resolved["genres"]:
            genre = _get_or_create_genre(db, g)
            if genre.id not in seen_ids:
                seen_ids.add(genre.id)
                entity.genres.append(genre)

    return entity


def get_or_create_album(db, artist_entity: ArtistEntity,
                        title: str, resolved: Optional[Dict] = None) -> AlbumEntity:
    """Find or create an AlbumEntity."""
    from app.tasks import _get_or_create_genre

    if resolved is None:
        resolved = {}

    mbid = resolved.get("mb_release_id")

    entity = None
    if mbid:
        entity = db.query(AlbumEntity).filter(AlbumEntity.mb_release_id == mbid).first()
    if not entity:
        entity = db.query(AlbumEntity).filter(
            AlbumEntity.title.ilike(title),
            AlbumEntity.artist_id == artist_entity.id,
        ).first()

    if entity:
        if resolved:
            for field in ["year", "release_date", "album_type"]:
                val = resolved.get(field)
                if val is not None:
                    setattr(entity, field, val)
            if mbid and not entity.mb_release_id:
                entity.mb_release_id = mbid
            _rg = resolved.get("mb_release_group_id")
            if _rg and not entity.mb_release_group_id:
                entity.mb_release_group_id = _rg
            new_conf = resolved.get("confidence", 0)
            if new_conf >= entity.confidence:
                entity.confidence = new_conf
                entity.needs_review = resolved.get("needs_review", entity.needs_review)
            if resolved.get("genres"):
                entity.genres.clear()
                db.flush()
                seen_ids = set()
                for g in resolved["genres"]:
                    genre = _get_or_create_genre(db, g)
                    if genre.id not in seen_ids:
                        seen_ids.add(genre.id)
                        entity.genres.append(genre)
        return entity

    entity = AlbumEntity(
        title=title,
        artist_id=artist_entity.id,
        year=resolved.get("year"),
        release_date=resolved.get("release_date"),
        mb_release_id=mbid,
        mb_release_group_id=resolved.get("mb_release_group_id"),
        album_type=resolved.get("album_type"),
        confidence=resolved.get("confidence", 0.5),
        needs_review=resolved.get("needs_review", True),
    )
    db.add(entity)
    db.flush()

    if resolved.get("genres"):
        seen_ids = set()
        for g in resolved["genres"]:
            genre = _get_or_create_genre(db, g)
            if genre.id not in seen_ids:
                seen_ids.add(genre.id)
                entity.genres.append(genre)

    return entity


def get_or_create_track(db, artist_entity: ArtistEntity,
                        album_entity: Optional[AlbumEntity],
                        title: str, resolved: Optional[Dict] = None) -> TrackEntity:
    """Find or create a TrackEntity."""
    if resolved is None:
        resolved = {}

    mbid = resolved.get("mb_recording_id")

    entity = None
    if mbid:
        entity = db.query(TrackEntity).filter(TrackEntity.mb_recording_id == mbid).first()
    if not entity:
        entity = db.query(TrackEntity).filter(
            TrackEntity.title.ilike(title),
            TrackEntity.artist_id == artist_entity.id,
        ).first()

    if entity:
        if resolved.get("confidence", 0) > entity.confidence:
            if album_entity:
                entity.album_id = album_entity.id
            if mbid and not entity.mb_recording_id:
                entity.mb_recording_id = mbid
            if resolved.get("duration_seconds"):
                entity.duration_seconds = resolved["duration_seconds"]
            entity.confidence = resolved.get("confidence", entity.confidence)
            entity.needs_review = resolved.get("needs_review", entity.needs_review)
        return entity

    entity = TrackEntity(
        title=title,
        artist_id=artist_entity.id,
        album_id=album_entity.id if album_entity else None,
        mb_recording_id=mbid,
        duration_seconds=resolved.get("duration_seconds"),
        confidence=resolved.get("confidence", 0.5),
        needs_review=resolved.get("needs_review", True),
    )
    db.add(entity)
    db.flush()
    return entity
