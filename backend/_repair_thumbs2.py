"""
Comprehensive thumbnail repair script.

Problem:
- 192 VideoItems with folder_path at D:\MusicVideos\Library\{artist}\{video}\ (doesn't exist)
- Actual video folders at D:\MusicVideos\{artist}\{video}\
- AIThumbnail.file_path points to deleted D:\MusicVideos\PlayarrCache\assets\thumbnails\{id}\
- ~91 old folders have 1 thumb_*.jpg each (the selected thumbnail)
- Other 11 thumbs per video only existed in deleted cache — unrecoverable

Strategy:
1. Build mapping: video_id -> actual folder at D:\MusicVideos\{artist}\{video}\
2. For each video, discover available thumb_*.jpg files in actual folder
3. Copy thumb files into cache at D:\MusicVideos\Library\_PlayarrCache\thumbnails\{id}\
4. Update matching AIThumbnail records with correct file_path
5. Delete AIThumbnail records whose files can't be recovered
6. Clean up AISceneAnalysis records that end up with zero thumbnails
"""
import os
import re
import shutil
import sqlite3
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(__file__), "playarr.db")
MUSIC_ROOT = r"D:\MusicVideos"
LIBRARY_DIR = r"D:\MusicVideos\Library"
CACHE_THUMBS = os.path.join(LIBRARY_DIR, "_PlayarrCache", "thumbnails")

SKIP_DIRS = {"Library", "_PlayarrCache", "PlayarrCache", "_archive", "_artists",
             "_albums", "Previews", "Archive", "previews", "workspaces"}


def build_actual_folder_map():
    """
    Build mapping: folder_basename -> actual_folder_path
    from D:\MusicVideos\{artist}\{video}\ structure.
    """
    result = {}
    for artist_dir in os.listdir(MUSIC_ROOT):
        if artist_dir in SKIP_DIRS:
            continue
        artist_path = os.path.join(MUSIC_ROOT, artist_dir)
        if not os.path.isdir(artist_path):
            continue
        for video_dir in os.listdir(artist_path):
            video_path = os.path.join(artist_path, video_dir)
            if not os.path.isdir(video_path):
                continue
            result[video_dir] = video_path
    return result


def run():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Build mapping from folder basename to actual path
    print("Building folder mapping from D:\\MusicVideos\\...")
    actual_folders = build_actual_folder_map()
    print(f"  Found {len(actual_folders)} actual video folders\n")

    # Load all video items
    c.execute("SELECT id, folder_path, artist, title FROM video_items WHERE folder_path IS NOT NULL")
    videos = c.fetchall()
    print(f"VideoItems in DB: {len(videos)}")

    stats = {
        "matched": 0,
        "unmatched": 0,
        "thumbs_recovered": 0,
        "thumbs_deleted": 0,
        "scene_analyses_cleaned": 0,
        "files_copied": 0,
    }

    for vid, folder_path, artist, title in videos:
        folder_basename = os.path.basename(folder_path)
        actual_folder = actual_folders.get(folder_basename)

        if not actual_folder:
            stats["unmatched"] += 1
            continue

        stats["matched"] += 1

        # Find thumb files in actual folder
        thumb_files = {}
        for fn in os.listdir(actual_folder):
            m = re.match(r"thumb_([\d.]+)\.jpg$", fn)
            if m:
                ts_str = m.group(1)
                thumb_files[ts_str] = os.path.join(actual_folder, fn)

        if not thumb_files:
            # No thumb files on disk — all 12 entries are unrecoverable
            c.execute("SELECT COUNT(*) FROM ai_thumbnails WHERE video_id = ?", (vid,))
            cnt = c.fetchone()[0]
            if cnt > 0:
                c.execute("DELETE FROM ai_thumbnails WHERE video_id = ?", (vid,))
                stats["thumbs_deleted"] += cnt
                c.execute("DELETE FROM ai_scene_analyses WHERE video_id = ?", (vid,))
                stats["scene_analyses_cleaned"] += 1
            continue

        # Ensure cache dir exists
        cache_dir = os.path.join(CACHE_THUMBS, str(vid))
        os.makedirs(cache_dir, exist_ok=True)

        # Copy available thumb files to cache
        ts_to_cache_path = {}
        for ts_str, src_path in thumb_files.items():
            fn = os.path.basename(src_path)
            dst = os.path.join(cache_dir, fn)
            if not os.path.isfile(dst):
                shutil.copy2(src_path, dst)
                stats["files_copied"] += 1
            ts_to_cache_path[ts_str] = dst

        # Update AIThumbnail records
        c.execute(
            "SELECT id, timestamp_sec, file_path FROM ai_thumbnails WHERE video_id = ?",
            (vid,),
        )
        thumb_rows = c.fetchall()

        for tid, ts_sec, old_path in thumb_rows:
            ts_str = f"{ts_sec:.2f}"
            new_path = ts_to_cache_path.get(ts_str)

            if new_path and os.path.isfile(new_path):
                c.execute(
                    "UPDATE ai_thumbnails SET file_path = ? WHERE id = ?",
                    (new_path, tid),
                )
                stats["thumbs_recovered"] += 1
            else:
                # This thumbnail's file doesn't exist anywhere — remove
                c.execute("DELETE FROM ai_thumbnails WHERE id = ?", (tid,))
                stats["thumbs_deleted"] += 1

        # If zero thumbnails remain for a scene analysis, clean it up
        c.execute("SELECT COUNT(*) FROM ai_thumbnails WHERE video_id = ?", (vid,))
        remaining = c.fetchone()[0]
        if remaining == 0:
            c.execute("DELETE FROM ai_scene_analyses WHERE video_id = ?", (vid,))
            stats["scene_analyses_cleaned"] += 1

    conn.commit()

    # Final verification
    c.execute("SELECT COUNT(*) FROM ai_thumbnails")
    total_thumbs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM ai_scene_analyses")
    total_sa = c.fetchone()[0]

    # Count thumbs with existing files
    c.execute("SELECT id, file_path FROM ai_thumbnails")
    existing = sum(1 for _, fp in c.fetchall() if fp and os.path.isfile(fp))

    conn.close()

    print(f"\n=== Results ===")
    print(f"Videos matched to actual folders: {stats['matched']}/{len(videos)}")
    print(f"Videos unmatched: {stats['unmatched']}")
    print(f"Thumbnail records recovered (path updated): {stats['thumbs_recovered']}")
    print(f"Thumbnail records deleted (file unrecoverable): {stats['thumbs_deleted']}")
    print(f"Scene analyses cleaned (no thumbs left): {stats['scene_analyses_cleaned']}")
    print(f"Files copied to cache: {stats['files_copied']}")
    print(f"\nFinal DB state:")
    print(f"  AISceneAnalysis records: {total_sa}")
    print(f"  AIThumbnail records: {total_thumbs}")
    print(f"  Thumbnails with existing files: {existing}/{total_thumbs}")

    # Show a sample
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    print(f"\nSample recovered thumbnails:")
    c.execute("""
        SELECT t.id, t.video_id, t.file_path, v.artist, v.title
        FROM ai_thumbnails t JOIN video_items v ON v.id = t.video_id
        LIMIT 10
    """)
    for row in c.fetchall():
        exists = os.path.isfile(row[2]) if row[2] else False
        print(f"  [{row[1]}] {row[3]} - {row[4]}: exists={exists}")
        print(f"         {row[2]}")
    conn.close()


if __name__ == "__main__":
    run()
