"""
Full path repair: update VideoItem, MediaAsset, and AIThumbnail paths
to match what actually exists on disk at D:\MusicVideos\{artist}\{video}\.

The DB currently has paths under D:\MusicVideos\Library\{artist}\{video}\
which is empty. The actual files are in D:\MusicVideos\{artist}\{video}\.
"""
import os
import re
import sqlite3
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(__file__), "playarr.db")
MUSIC_ROOT = r"D:\MusicVideos"

SKIP_DIRS = {"Library", "_PlayarrCache", "PlayarrCache", "_archive", "_artists",
             "_albums", "Previews", "Archive", "previews", "workspaces"}


def build_folder_map():
    """Map folder basename -> actual full path on disk."""
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


def find_video_file(folder_path, old_file_basename):
    """Find the actual video file in a folder by basename."""
    if not os.path.isdir(folder_path):
        return None
    # Try exact basename first
    candidate = os.path.join(folder_path, old_file_basename)
    if os.path.isfile(candidate):
        return candidate
    # Try case-insensitive match
    lower = old_file_basename.lower()
    for fn in os.listdir(folder_path):
        if fn.lower() == lower:
            return os.path.join(folder_path, fn)
    # Try any video file in the folder
    video_exts = {".mkv", ".mp4", ".avi", ".webm", ".mov", ".wmv", ".flv"}
    videos = [f for f in os.listdir(folder_path)
              if os.path.splitext(f)[1].lower() in video_exts]
    if len(videos) == 1:
        return os.path.join(folder_path, videos[0])
    return None


def run():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    print("Building folder map from D:\\MusicVideos\\...")
    folder_map = build_folder_map()
    print(f"  Found {len(folder_map)} video folders on disk\n")

    # ── Phase 1: Fix VideoItem folder_path + file_path ──
    c.execute("SELECT id, folder_path, file_path, artist, title FROM video_items")
    videos = c.fetchall()

    v_matched = 0
    v_unmatched = []
    v_folder_fixed = 0
    v_file_fixed = 0

    for vid, old_folder, old_file, artist, title in videos:
        folder_basename = os.path.basename(old_folder) if old_folder else None
        if not folder_basename:
            v_unmatched.append((vid, artist, title, "no folder_path"))
            continue

        actual_folder = folder_map.get(folder_basename)
        if not actual_folder:
            v_unmatched.append((vid, artist, title, folder_basename))
            continue

        v_matched += 1

        # Update folder_path
        if old_folder != actual_folder:
            c.execute("UPDATE video_items SET folder_path = ? WHERE id = ?",
                      (actual_folder, vid))
            v_folder_fixed += 1

        # Update file_path
        old_file_basename = os.path.basename(old_file) if old_file else None
        if old_file_basename:
            actual_file = find_video_file(actual_folder, old_file_basename)
            if actual_file and old_file != actual_file:
                c.execute("UPDATE video_items SET file_path = ? WHERE id = ?",
                          (actual_file, vid))
                v_file_fixed += 1

    print(f"=== VideoItem Path Fix ===")
    print(f"Matched: {v_matched}/{len(videos)}")
    print(f"Folder paths fixed: {v_folder_fixed}")
    print(f"File paths fixed: {v_file_fixed}")
    if v_unmatched:
        print(f"Unmatched ({len(v_unmatched)}):")
        for vid, artist, title, reason in v_unmatched[:10]:
            print(f"  [{vid}] {artist} - {title} ({reason})")
        if len(v_unmatched) > 10:
            print(f"  ... and {len(v_unmatched) - 10} more")

    # ── Phase 2: Fix MediaAsset file_path ──
    c.execute("""
        SELECT ma.id, ma.video_id, ma.file_path, ma.asset_type
        FROM media_assets ma
        WHERE ma.file_path IS NOT NULL
    """)
    assets = c.fetchall()
    a_fixed = 0
    a_already_ok = 0
    a_broken = 0

    for aid, vid, old_asset_path, asset_type in assets:
        if os.path.isfile(old_asset_path):
            a_already_ok += 1
            continue

        # Get the updated folder_path for this video
        c.execute("SELECT folder_path FROM video_items WHERE id = ?", (vid,))
        row = c.fetchone()
        if not row or not row[0]:
            a_broken += 1
            continue

        new_folder = row[0]
        asset_basename = os.path.basename(old_asset_path)

        # Try to find the asset in the actual folder
        candidate = os.path.join(new_folder, asset_basename)
        if os.path.isfile(candidate):
            c.execute("UPDATE media_assets SET file_path = ? WHERE id = ?",
                      (candidate, aid))
            a_fixed += 1
        else:
            # Try case-insensitive
            found = False
            if os.path.isdir(new_folder):
                for fn in os.listdir(new_folder):
                    if fn.lower() == asset_basename.lower():
                        c.execute("UPDATE media_assets SET file_path = ? WHERE id = ?",
                                  (os.path.join(new_folder, fn), aid))
                        a_fixed += 1
                        found = True
                        break
            if not found:
                a_broken += 1

    print(f"\n=== MediaAsset Path Fix ===")
    print(f"Already OK: {a_already_ok}")
    print(f"Fixed: {a_fixed}")
    print(f"Broken (file not found): {a_broken}")

    # ── Phase 3: Verify AIThumbnail paths ──
    c.execute("SELECT id, file_path FROM ai_thumbnails")
    t_ok = 0
    t_broken = 0
    for tid, fp in c.fetchall():
        if fp and os.path.isfile(fp):
            t_ok += 1
        else:
            t_broken += 1

    print(f"\n=== AIThumbnail Status ===")
    print(f"Valid (file exists): {t_ok}")
    print(f"Broken (file missing): {t_broken}")

    conn.commit()

    # ── Final verification ──
    print(f"\n=== Final Verification ===")
    c.execute("SELECT file_path FROM video_items WHERE file_path IS NOT NULL")
    files_exist = sum(1 for (fp,) in c.fetchall() if os.path.isfile(fp))
    c.execute("SELECT COUNT(*) FROM video_items")
    total = c.fetchone()[0]
    print(f"Video files accessible: {files_exist}/{total}")

    c.execute("SELECT file_path FROM media_assets WHERE file_path IS NOT NULL")
    assets_exist = sum(1 for (fp,) in c.fetchall() if os.path.isfile(fp))
    c.execute("SELECT COUNT(*) FROM media_assets")
    total_assets = c.fetchone()[0]
    print(f"Media assets accessible: {assets_exist}/{total_assets}")

    c.execute("SELECT file_path FROM ai_thumbnails")
    thumbs_exist = sum(1 for (fp,) in c.fetchall() if os.path.isfile(fp))
    c.execute("SELECT COUNT(*) FROM ai_thumbnails")
    total_thumbs = c.fetchone()[0]
    print(f"Thumbnails accessible: {thumbs_exist}/{total_thumbs}")

    # Sample working video
    print(f"\nSample fixed items:")
    c.execute("""
        SELECT v.id, v.artist, v.title,
               v.file_path, v.folder_path,
               (SELECT COUNT(*) FROM ai_thumbnails t WHERE t.video_id = v.id) as tc
        FROM video_items v
        WHERE v.file_path IS NOT NULL
        LIMIT 5
    """)
    for row in c.fetchall():
        vid, artist, title, fp, fol, tc = row
        fe = os.path.isfile(fp) if fp else False
        print(f"  [{vid}] {artist} - {title}")
        print(f"    file: {fp} (exists={fe})")
        print(f"    folder: {fol}")
        print(f"    thumbnails: {tc}")

    conn.close()


if __name__ == "__main__":
    run()
