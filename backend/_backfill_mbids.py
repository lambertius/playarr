"""
MBID Backfill Script
====================
Populates MusicBrainz IDs and multi-artist (artist_ids) for existing library.

Phase 1: Fill missing mb_artist_id via MusicBrainz search
Phase 2: Build artist_ids JSON for tracks with "feat." / "ft." / "featuring"
Phase 3: Report artist name conflicts (same MBID, different name text)
"""
import re
import sys
import time
import logging
from difflib import SequenceMatcher

import musicbrainzngs
from sqlalchemy import func
from sqlalchemy.orm.attributes import flag_modified

# Bootstrap app
sys.path.insert(0, ".")
from app.database import SessionLocal
from app.models import VideoItem
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mbid_backfill")

# ── MusicBrainz init ──
settings = get_settings()
musicbrainzngs.set_useragent(
    settings.musicbrainz_app,
    settings.musicbrainz_version,
    settings.musicbrainz_contact,
)

_UNICODE_HYPHENS = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")

# feat. parsing patterns (from source_validation.py)
_FEAT_RE = re.compile(
    r"""
    \s*\(feat\.?\s+  |
    \s*\(featuring\s+ |
    \s*\(ft\.?\s+    |
    \s+feat\.?\s+    |
    \s+featuring\s+  |
    \s+ft\.?\s+
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Protect band names like "Florence + the Machine", "Tom Petty & The Heartbreakers"
_BAND_PROTECT = re.compile(r"\s*[+&]\s*the\b", re.IGNORECASE)


def parse_primary_artist(artist: str) -> str:
    """Extract the primary artist name, stripping featured artists."""
    if _BAND_PROTECT.search(artist):
        # Might be a band name — only strip if feat. pattern exists
        m = _FEAT_RE.search(artist)
        if m:
            return artist[: m.start()].strip()
        return artist.strip()
    m = _FEAT_RE.search(artist)
    if m:
        return artist[: m.start()].strip()
    return artist.strip()


def parse_featured_artists(artist: str) -> list[str]:
    """Extract featured artist names from artist string."""
    m = _FEAT_RE.search(artist)
    if not m:
        return []
    rest = artist[m.end():]
    # Remove trailing parenthesis
    rest = re.sub(r"\)\s*$", "", rest).strip()
    # Split on &, and, comma
    parts = re.split(r"\s*(?:&|,|\band\b)\s*", rest, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def lookup_artist_mbid(artist_name: str) -> str | None:
    """Search MusicBrainz for an artist and return the best-matching MBID."""
    try:
        time.sleep(1.1)  # Rate limit
        result = musicbrainzngs.search_artists(artist=artist_name, limit=5)
        artists = result.get("artist-list", [])
        if not artists:
            return None

        _norm = _UNICODE_HYPHENS.sub("-", artist_name.lower().strip())
        best_sim = 0.0
        best_id = None
        for candidate in artists:
            cand_name = candidate.get("name", "")
            cand_norm = _UNICODE_HYPHENS.sub("-", cand_name.lower().strip())
            sim = SequenceMatcher(None, _norm, cand_norm).ratio()
            if sim > best_sim:
                best_sim = sim
                best_id = candidate.get("id")

        if best_sim >= 0.60 and best_id:
            logger.info(f"  Matched '{artist_name}' -> MBID {best_id} (sim={best_sim:.2f})")
            return best_id
        else:
            logger.warning(f"  No good match for '{artist_name}' (best sim={best_sim:.2f})")
            return None
    except Exception as e:
        logger.error(f"  MB lookup failed for '{artist_name}': {e}")
        return None


def main():
    db = SessionLocal()

    # ── Phase 1: Fill missing mb_artist_id ──
    print("\n" + "=" * 60)
    print("PHASE 1: Fill missing mb_artist_id")
    print("=" * 60)

    missing = db.query(VideoItem).filter(VideoItem.mb_artist_id.is_(None)).all()
    print(f"Found {len(missing)} videos without mb_artist_id")

    filled = 0
    for video in missing:
        primary = parse_primary_artist(video.artist)
        logger.info(f"Looking up: '{primary}' (from '{video.artist} - {video.title}')")
        mbid = lookup_artist_mbid(primary)
        if mbid:
            video.mb_artist_id = mbid
            filled += 1

    if filled:
        db.commit()
    print(f"Filled {filled}/{len(missing)} missing mb_artist_id values")

    # ── Phase 2: Build artist_ids for feat. tracks ──
    print("\n" + "=" * 60)
    print("PHASE 2: Build artist_ids for feat. tracks")
    print("=" * 60)

    feat_videos = (
        db.query(VideoItem)
        .filter(
            VideoItem.artist_ids.is_(None),
            VideoItem.artist.op("LIKE")("%feat.%")
            | VideoItem.artist.op("LIKE")("%feat %")
            | VideoItem.artist.op("LIKE")("%featuring%")
            | VideoItem.artist.op("LIKE")("%ft.%")
            | VideoItem.artist.op("LIKE")("%(feat%")
            | VideoItem.artist.op("LIKE")("%(ft%")
        )
        .all()
    )
    print(f"Found {len(feat_videos)} videos with featured artists and no artist_ids")

    built = 0
    for video in feat_videos:
        primary = parse_primary_artist(video.artist)
        featured = parse_featured_artists(video.artist)
        if not featured:
            continue

        artist_list = [{"name": primary, "mb_artist_id": video.mb_artist_id}]
        for feat in featured:
            artist_list.append({"name": feat})

        video.artist_ids = artist_list
        flag_modified(video, "artist_ids")
        built += 1
        logger.info(f"  {video.artist} -> {[a['name'] for a in artist_list]}")

    if built:
        db.commit()
    print(f"Built artist_ids for {built} videos")

    # ── Phase 2b: Look up MBIDs for featured artists ──
    print("\n" + "=" * 60)
    print("PHASE 2b: Look up MBIDs for featured artists")
    print("=" * 60)

    # Cache: artist name -> MBID (avoid duplicate lookups)
    mbid_cache: dict[str, str | None] = {}

    videos_with_aids = db.query(VideoItem).filter(VideoItem.artist_ids.isnot(None)).all()
    enriched = 0
    for video in videos_with_aids:
        if not video.artist_ids:
            continue
        changed = False
        for entry in video.artist_ids:
            if entry.get("mb_artist_id"):
                continue  # already has MBID
            name = entry.get("name", "")
            if not name:
                continue

            if name not in mbid_cache:
                mbid_cache[name] = lookup_artist_mbid(name)
            mbid = mbid_cache[name]
            if mbid:
                entry["mb_artist_id"] = mbid
                changed = True

        if changed:
            flag_modified(video, "artist_ids")
            enriched += 1

    if enriched:
        db.commit()
    print(f"Enriched artist_ids with MBIDs for {enriched} videos")

    # ── Phase 3: Report artist name conflicts ──
    print("\n" + "=" * 60)
    print("PHASE 3: Artist name conflicts (same MBID, different name)")
    print("=" * 60)

    rows = (
        db.query(VideoItem.mb_artist_id, VideoItem.artist, func.count(VideoItem.id))
        .filter(VideoItem.mb_artist_id.isnot(None))
        .group_by(VideoItem.mb_artist_id, VideoItem.artist)
        .all()
    )

    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for mb_id, name, cnt in rows:
        groups[mb_id].append((name, cnt))

    conflicts = {k: v for k, v in groups.items() if len(v) > 1}
    if conflicts:
        print(f"Found {len(conflicts)} MBID(s) with conflicting artist names:\n")
        for mbid, entries in sorted(conflicts.items(), key=lambda x: sum(c for _, c in x[1]), reverse=True):
            total = sum(c for _, c in entries)
            print(f"  MBID: {mbid} ({total} videos)")
            for name, cnt in entries:
                hex_prefix = " ".join(f"{ord(c):04x}" for c in name[:5])
                print(f"    - \"{name}\" ({cnt} videos) [hex: {hex_prefix}...]")
            print()
        print("Use the Metadata Manager → Artist Consolidation tab to resolve these.")
    else:
        print("No conflicts found!")

    # ── Summary ──
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total = db.query(VideoItem).count()
    with_mb = db.query(VideoItem).filter(VideoItem.mb_artist_id.isnot(None)).count()
    with_aids = db.query(VideoItem).filter(VideoItem.artist_ids.isnot(None)).count()
    print(f"Total videos:        {total}")
    print(f"With mb_artist_id:   {with_mb} ({100*with_mb//total}%)")
    print(f"With artist_ids:     {with_aids}")
    print(f"Artist conflicts:    {len(conflicts)}")

    db.close()


if __name__ == "__main__":
    main()
