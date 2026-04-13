"""Repair: create poster art for tracks missing it.

Strategy (mirrors import pipeline):
1. If the video has a CachedAsset album poster → copy that as the poster
2. If no album art, use the existing video_thumb or best AI thumbnail
3. Create a MediaAsset record in the DB
4. Re-write the .playarr.xml sidecar so the fix persists across imports/scans
"""
import os
import sys
import shutil
from datetime import datetime, timezone

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal
from app.models import VideoItem, MediaAsset
from app.ai.models import AIThumbnail
from app.metadata.models import CachedAsset
from app.services.playarr_xml import write_playarr_xml
from app.services.artwork_service import validate_file

DRY_RUN = "--dry-run" in sys.argv


def main():
    db = SessionLocal()
    try:
        # Find all videos with folder_path that lack a valid poster
        all_videos = db.query(VideoItem).filter(
            VideoItem.folder_path.isnot(None),
        ).all()

        missing = []
        for v in all_videos:
            has_poster = db.query(MediaAsset.id).filter(
                MediaAsset.video_id == v.id,
                MediaAsset.asset_type == "poster",
                MediaAsset.status.in_(["valid", "pending"]),
            ).first()
            if not has_poster:
                missing.append(v)

        print(f"Total videos: {len(all_videos)}")
        print(f"Missing poster: {len(missing)}")
        if not missing:
            print("Nothing to fix.")
            return

        fixed = 0
        for v in missing:
            print(f"\n--- id={v.id} | {v.artist} - {v.title} ---")

            if not v.folder_path or not os.path.isdir(v.folder_path):
                print(f"  SKIP: folder missing ({v.folder_path})")
                continue

            poster_src = None
            provenance = None
            source_url = None

            # Strategy 1: CachedAsset album poster
            if v.album_entity_id:
                ca = db.query(CachedAsset).filter(
                    CachedAsset.entity_id == v.album_entity_id,
                    CachedAsset.entity_type == "album",
                    CachedAsset.kind == "poster",
                    CachedAsset.status == "valid",
                ).first()
                if ca and ca.local_cache_path and os.path.isfile(ca.local_cache_path):
                    poster_src = ca.local_cache_path
                    provenance = "album_poster_repair"
                    source_url = ca.source_url
                    print(f"  Source: album CachedAsset ({ca.local_cache_path})")

            # Strategy 2: Existing video_thumb asset
            if not poster_src:
                thumb = db.query(MediaAsset).filter(
                    MediaAsset.video_id == v.id,
                    MediaAsset.asset_type == "video_thumb",
                    MediaAsset.status == "valid",
                ).first()
                if thumb and thumb.file_path and os.path.isfile(thumb.file_path):
                    poster_src = thumb.file_path
                    provenance = "video_thumb_fallback"
                    print(f"  Source: video_thumb asset ({thumb.file_path})")

            # Strategy 3: Existing thumb asset
            if not poster_src:
                thumb = db.query(MediaAsset).filter(
                    MediaAsset.video_id == v.id,
                    MediaAsset.asset_type == "thumb",
                    MediaAsset.status == "valid",
                ).first()
                if thumb and thumb.file_path and os.path.isfile(thumb.file_path):
                    poster_src = thumb.file_path
                    provenance = "thumb_fallback"
                    print(f"  Source: thumb asset ({thumb.file_path})")

            # Strategy 4: Best AI scene analysis thumbnail
            if not poster_src:
                best_thumb = db.query(AIThumbnail).filter(
                    AIThumbnail.video_id == v.id,
                ).order_by(AIThumbnail.is_selected.desc(), AIThumbnail.score_overall.desc()).first()
                if best_thumb and best_thumb.file_path and os.path.isfile(best_thumb.file_path):
                    poster_src = best_thumb.file_path
                    provenance = "ai_thumb_fallback"
                    print(f"  Source: AI thumbnail (score={best_thumb.score_overall:.2f})")

            if not poster_src:
                print(f"  SKIP: no source image found")
                continue

            # Build destination path
            folder_name = os.path.basename(v.folder_path)
            poster_dst = os.path.join(v.folder_path, f"{folder_name}-poster.jpg")

            if DRY_RUN:
                print(f"  DRY RUN: would copy {poster_src} → {poster_dst}")
                print(f"  DRY RUN: would create MediaAsset(poster, {provenance})")
                fixed += 1
                continue

            # Copy file
            try:
                shutil.copy2(poster_src, poster_dst)
            except Exception as e:
                print(f"  ERROR: copy failed: {e}")
                continue

            # Validate
            vr = validate_file(poster_dst) if os.path.isfile(poster_dst) else None

            # Create MediaAsset
            db.add(MediaAsset(
                video_id=v.id,
                asset_type="poster",
                file_path=poster_dst,
                source_url=source_url,
                provenance=provenance,
                status="valid" if (vr and vr.valid) else "invalid",
                width=vr.width if vr and vr.valid else None,
                height=vr.height if vr and vr.valid else None,
                file_size_bytes=vr.file_size_bytes if vr and vr.valid else None,
                file_hash=vr.file_hash if vr and vr.valid else None,
                last_validated_at=datetime.now(timezone.utc),
            ))
            db.commit()
            print(f"  Created poster: {poster_dst}")

            # Re-write sidecar XML
            try:
                xml_path = write_playarr_xml(v, db)
                if xml_path:
                    print(f"  Updated sidecar: {xml_path}")
            except Exception as e:
                print(f"  WARNING: sidecar write failed: {e}")

            fixed += 1

        print(f"\n{'=' * 50}")
        print(f"Fixed: {fixed} / {len(missing)}")
        if DRY_RUN:
            print("(DRY RUN - no changes made)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
