"""
Backfill Playarr Content IDs for existing library.

Computes playarr_track_id and playarr_video_id for all videos that don't
have them yet.  Also optionally computes video_phash if ffmpeg is available.

Usage:
    cd backend
    python _backfill_content_ids.py [--phash]

  --phash   Also compute perceptual hashes (requires ffmpeg, much slower)
"""
import logging
import os
import sys
import time

# Ensure the backend package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    do_phash = "--phash" in sys.argv

    from app.database import SessionLocal
    from app.models import VideoItem
    from app.services.content_id import compute_ids_for_video, compute_phash

    db = SessionLocal()
    try:
        videos = db.query(VideoItem).all()
        total = len(videos)
        logger.info(f"Found {total} videos in library")

        updated = 0
        phash_count = 0

        for i, v in enumerate(videos, 1):
            changed = False

            # Compute content IDs if missing
            if not v.playarr_track_id or not v.playarr_video_id:
                ids = compute_ids_for_video(v)
                v.playarr_track_id = ids["playarr_track_id"]
                v.playarr_video_id = ids["playarr_video_id"]
                changed = True

            # Optionally compute pHash
            if do_phash and not v.video_phash and v.file_path and os.path.isfile(v.file_path):
                phash = compute_phash(v.file_path)
                if phash:
                    v.video_phash = phash
                    # Recompute video ID with phash included
                    from app.services.content_id import compute_video_id
                    v.playarr_video_id = compute_video_id(
                        artist=v.artist,
                        title=v.title,
                        version_type=v.version_type or "normal",
                        mb_recording_id=v.mb_recording_id,
                        video_phash=phash,
                    )
                    phash_count += 1
                    changed = True

            if changed:
                updated += 1

            if i % 100 == 0 or i == total:
                db.commit()
                logger.info(f"  Progress: {i}/{total} ({updated} updated, {phash_count} phashes)")

        db.commit()

        # Summary
        with_vid = db.query(VideoItem).filter(VideoItem.playarr_video_id.isnot(None)).count()
        with_tid = db.query(VideoItem).filter(VideoItem.playarr_track_id.isnot(None)).count()
        with_ph = db.query(VideoItem).filter(VideoItem.video_phash.isnot(None)).count()

        logger.info(f"\n{'='*50}")
        logger.info(f"BACKFILL COMPLETE")
        logger.info(f"  Total videos: {total}")
        logger.info(f"  Updated: {updated}")
        logger.info(f"  With playarr_video_id: {with_vid} ({with_vid*100//total}%)")
        logger.info(f"  With playarr_track_id: {with_tid} ({with_tid*100//total}%)")
        logger.info(f"  With video_phash: {with_ph}")
        logger.info(f"{'='*50}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
