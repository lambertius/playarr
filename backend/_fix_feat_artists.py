"""
One-shot repair: normalize feat/ft/& artist strings to semicolon-separated format.

Updates:
  - VideoItem.artist  ("DJ Snake feat. Lil Jon" → "DJ Snake; Lil Jon")
  - VideoItem.artist_ids  (rebuilt from parsed artist names + existing MB IDs)
  - .playarr.xml sidecar  (re-written to reflect updated fields)

Does NOT rename files/folders — run a rename scan afterward if desired.

Usage:
    python _fix_feat_artists.py          # dry-run (report only)
    python _fix_feat_artists.py --apply  # commit changes
"""
import os
import sys

# Allow imports from the backend/app package
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal
from app.models import VideoItem
from app.services.source_validation import normalize_feat_to_semicolons, build_artist_ids
from app.services.playarr_xml import write_playarr_xml
from sqlalchemy.orm import joinedload

DRY_RUN = "--apply" not in sys.argv


def main():
    db = SessionLocal()
    try:
        items = (
            db.query(VideoItem)
            .options(
                joinedload(VideoItem.genres),
                joinedload(VideoItem.sources),
                joinedload(VideoItem.media_assets),
            )
            .all()
        )

        corrected = []

        for vi in items:
            original_artist = vi.artist or ""
            normalized = normalize_feat_to_semicolons(original_artist)

            if normalized == original_artist:
                continue

            # Build updated artist_ids, preserving existing MB IDs where possible
            existing_ids = vi.artist_ids or []
            # Collect MB ID for primary artist from existing data
            primary_mb_id = vi.mb_artist_id
            new_artist_ids = build_artist_ids(normalized, primary_mb_artist_id=primary_mb_id)

            # Try to carry over MB artist IDs from existing artist_ids entries by name
            old_by_name = {e.get("name", "").lower(): e.get("mb_artist_id") for e in existing_ids if e.get("mb_artist_id")}
            for entry in new_artist_ids:
                if not entry.get("mb_artist_id"):
                    mb_id = old_by_name.get(entry["name"].lower())
                    if mb_id:
                        entry["mb_artist_id"] = mb_id

            corrected.append({
                "id": vi.id,
                "old_artist": original_artist,
                "new_artist": normalized,
                "old_artist_ids": existing_ids,
                "new_artist_ids": new_artist_ids,
                "title": vi.title,
            })

            if not DRY_RUN:
                vi.artist = normalized
                vi.artist_ids = new_artist_ids

        if not DRY_RUN and corrected:
            db.flush()
            # Re-write XML sidecars for corrected items
            xml_ok = 0
            xml_skip = 0
            for rec in corrected:
                vi = db.get(VideoItem, rec["id"])
                if vi and vi.folder_path and os.path.isdir(vi.folder_path):
                    try:
                        write_playarr_xml(vi, db)
                        xml_ok += 1
                    except Exception as e:
                        print(f"  XML write failed for #{vi.id}: {e}")
                else:
                    xml_skip += 1
            db.commit()
            print(f"\nXML sidecars: {xml_ok} written, {xml_skip} skipped (folder missing)")

        # ── Report ──
        mode = "DRY RUN" if DRY_RUN else "APPLIED"
        print(f"\n{'='*70}")
        print(f"  Feat Artist Fix — {mode}")
        print(f"  Total tracks scanned: {len(items)}")
        print(f"  Tracks corrected: {len(corrected)}")
        print(f"{'='*70}\n")

        for rec in corrected:
            print(f"  #{rec['id']:>4}  {rec['old_artist']}")
            print(f"     →  {rec['new_artist']}")
            print(f"        Title: {rec['title']}")
            ids_str = ", ".join(f"{e['name']}" + (f" [{e['mb_artist_id'][:8]}…]" if e.get('mb_artist_id') else "") for e in rec['new_artist_ids'])
            print(f"        artist_ids: [{ids_str}]")
            print()

    finally:
        db.close()


if __name__ == "__main__":
    main()
