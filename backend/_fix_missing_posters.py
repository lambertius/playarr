"""
One-shot repair: create poster assets from video_thumb for videos missing posters.

Targets the INSTALLED (production) database and library.
Safe to re-run — skips videos that already have a poster asset.
"""
import sqlite3
import os
import shutil
import sys
from datetime import datetime, timezone

DB_PATH = os.path.join(os.environ["APPDATA"], "Playarr", "data", "playarr.db")

DRY_RUN = "--apply" not in sys.argv


def main():
    if not os.path.isfile(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Find videos with no poster but with a valid video_thumb
    c.execute("""
        SELECT vi.id, vi.artist, vi.title, vi.folder_path,
               ma.id as thumb_asset_id, ma.file_path as thumb_path
        FROM video_items vi
        JOIN media_assets ma ON ma.video_id = vi.id
             AND ma.asset_type = 'video_thumb'
             AND ma.status = 'valid'
        WHERE NOT EXISTS (
            SELECT 1 FROM media_assets ma2
            WHERE ma2.video_id = vi.id AND ma2.asset_type = 'poster'
        )
        ORDER BY vi.id
    """)
    rows = c.fetchall()

    if not rows:
        print("No videos need fixing — all have poster assets.")
        conn.close()
        return

    mode = "DRY RUN" if DRY_RUN else "APPLYING"
    print(f"[{mode}] Found {len(rows)} videos missing poster art:\n")

    fixed = 0
    skipped = 0

    for r in rows:
        vid = r["id"]
        artist = r["artist"]
        title = r["title"]
        folder = r["folder_path"]
        thumb_path = r["thumb_path"]

        label = f"  {vid:>5} {artist} - {title}"

        if not thumb_path or not os.path.isfile(thumb_path):
            print(f"{label}  SKIP (thumb file missing)")
            skipped += 1
            continue

        if not folder or not os.path.isdir(folder):
            print(f"{label}  SKIP (folder missing)")
            skipped += 1
            continue

        folder_name = os.path.basename(folder)
        poster_dst = os.path.join(folder, f"{folder_name}-poster.jpg")

        if os.path.isfile(poster_dst):
            print(f"{label}  SKIP (poster file already on disk)")
            skipped += 1
            continue

        if DRY_RUN:
            print(f"{label}  WOULD FIX")
            fixed += 1
            continue

        # Copy thumb -> poster
        shutil.copy2(thumb_path, poster_dst)
        file_size = os.path.getsize(poster_dst)
        now = datetime.now(timezone.utc).isoformat()

        c.execute("""
            INSERT INTO media_assets
                (video_id, asset_type, file_path, provenance, status,
                 file_size_bytes, last_validated_at, created_at)
            VALUES (?, 'poster', ?, 'video_thumb_fallback', 'valid',
                    ?, ?, ?)
        """, (vid, poster_dst, file_size, now, now))

        print(f"{label}  FIXED")
        fixed += 1

    if not DRY_RUN:
        conn.commit()

    conn.close()

    print(f"\nDone: {fixed} fixed, {skipped} skipped")
    if DRY_RUN:
        print("\nThis was a dry run. Re-run with --apply to commit changes.")


if __name__ == "__main__":
    main()
