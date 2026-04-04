"""
Repair thumbnail metadata: match old thumb_*.jpg files to current VideoItems,
copy them to the correct locations, and update AIThumbnail DB records.

Situation:
- VideoItems have folder_path under D:\MusicVideos\Library\{artist}\{video}\
- AIThumbnail.file_path points to dead D:\MusicVideos\PlayarrCache\assets\...
- Actual thumb_*.jpg files live in old D:\MusicVideos\{artist}\{video}\ folders
- Need to: copy thumbs into current folder + cache, fix DB paths
"""
import os
import re
import shutil
import sqlite3
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(__file__), "playarr.db")
LIBRARY_BASE = r"D:\MusicVideos"
LIBRARY_DIR = r"D:\MusicVideos\Library"
CACHE_THUMBS = os.path.join(LIBRARY_DIR, "_PlayarrCache", "thumbnails")

# Directories that are NOT old video folders
SKIP_DIRS = {"Library", "_PlayarrCache", "PlayarrCache", "_archive", "_artists",
             "_albums", "Previews", "Archive", "previews", "workspaces"}


def discover_old_thumb_files():
    """Find thumb_*.jpg files under D:\\MusicVideos (outside Library/)."""
    # Build mapping: folder_basename -> list of (full_path, filename)
    result = defaultdict(list)
    for artist_dir in os.listdir(LIBRARY_BASE):
        if artist_dir in SKIP_DIRS:
            continue
        artist_path = os.path.join(LIBRARY_BASE, artist_dir)
        if not os.path.isdir(artist_path):
            continue
        for video_dir in os.listdir(artist_path):
            video_path = os.path.join(artist_path, video_dir)
            if not os.path.isdir(video_path):
                continue
            thumbs = [f for f in os.listdir(video_path)
                      if re.match(r"thumb_[\d.]+\.jpg$", f)]
            if thumbs:
                for fn in thumbs:
                    result[video_dir].append(os.path.join(video_path, fn))
    return result


def run():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 1) Discover old thumb files by folder basename
    print("Discovering old thumb files...")
    old_thumbs = discover_old_thumb_files()
    total_old = sum(len(v) for v in old_thumbs.values())
    print(f"  Found {total_old} thumb files across {len(old_thumbs)} folders\n")

    # 2) Load all VideoItems
    c.execute("SELECT id, folder_path FROM video_items WHERE folder_path IS NOT NULL")
    videos = c.fetchall()
    print(f"VideoItems: {len(videos)}")

    matched = 0
    files_copied_to_folder = 0
    files_copied_to_cache = 0
    db_records_updated = 0
    unmatched_videos = []

    for vid, folder_path in videos:
        folder_basename = os.path.basename(folder_path)

        # Match by folder basename
        source_files = old_thumbs.get(folder_basename, [])
        if not source_files:
            unmatched_videos.append((vid, folder_basename))
            continue

        matched += 1

        # Ensure cache dir exists for this video
        cache_dir = os.path.join(CACHE_THUMBS, str(vid))
        os.makedirs(cache_dir, exist_ok=True)

        # Build mapping: timestamp -> source file
        ts_to_source = {}
        for src_path in source_files:
            fn = os.path.basename(src_path)
            ts_match = re.search(r"thumb_([\d.]+)\.jpg$", fn)
            if ts_match:
                ts = ts_match.group(1)
                ts_to_source[ts] = src_path

        # Copy thumb files to current video folder
        for ts, src_path in ts_to_source.items():
            fn = os.path.basename(src_path)

            # Copy to video folder (for XML export portability)
            dst_folder = os.path.join(folder_path, fn)
            if not os.path.isfile(dst_folder):
                try:
                    shutil.copy2(src_path, dst_folder)
                    files_copied_to_folder += 1
                except OSError as e:
                    print(f"  WARN: copy to folder failed: {e}")

            # Copy to cache dir (for runtime serving)
            dst_cache = os.path.join(cache_dir, fn)
            if not os.path.isfile(dst_cache):
                try:
                    shutil.copy2(src_path, dst_cache)
                    files_copied_to_cache += 1
                except OSError as e:
                    print(f"  WARN: copy to cache failed: {e}")

        # Update AIThumbnail records for this video
        c.execute(
            "SELECT id, timestamp_sec, file_path FROM ai_thumbnails WHERE video_id = ?",
            (vid,),
        )
        thumb_rows = c.fetchall()
        for tid, ts_sec, old_path in thumb_rows:
            ts_str = f"{ts_sec:.2f}"
            new_fn = f"thumb_{ts_str}.jpg"
            new_path = os.path.join(cache_dir, new_fn)
            if os.path.isfile(new_path) and old_path != new_path:
                c.execute(
                    "UPDATE ai_thumbnails SET file_path = ? WHERE id = ?",
                    (new_path, tid),
                )
                db_records_updated += 1

    conn.commit()
    conn.close()

    print(f"\n=== Results ===")
    print(f"Videos matched to old thumb dirs: {matched}/{len(videos)}")
    print(f"Thumb files copied to video folders: {files_copied_to_folder}")
    print(f"Thumb files copied to cache dirs: {files_copied_to_cache}")
    print(f"DB records updated: {db_records_updated}")
    if unmatched_videos:
        print(f"\nUnmatched videos ({len(unmatched_videos)}):")
        for vid, bn in unmatched_videos[:15]:
            print(f"  [{vid}] {bn}")
        if len(unmatched_videos) > 15:
            print(f"  ... and {len(unmatched_videos) - 15} more")

    # Verify: check a sample of updated records
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, video_id, file_path FROM ai_thumbnails LIMIT 5")
    print(f"\nSample updated thumbnails:")
    for row in c.fetchall():
        exists = os.path.isfile(row[2]) if row[2] else False
        print(f"  Thumb {row[0]}: video_id={row[1]} exists={exists} path={row[2]}")
    conn.close()


if __name__ == "__main__":
    run()
