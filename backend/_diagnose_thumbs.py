"""Diagnose thumbnail state in the database."""
import sqlite3
import os
import glob

db_path = os.path.join(os.path.dirname(__file__), "playarr.db")
if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    raise SystemExit(1)

print(f"DB at {db_path}")
conn = sqlite3.connect(db_path)
c = conn.cursor()

# Count video items
c.execute("SELECT COUNT(*) FROM video_items")
print(f"VideoItems: {c.fetchone()[0]}")

# Count scene analyses
c.execute("SELECT COUNT(*) FROM ai_scene_analyses")
print(f"AISceneAnalysis: {c.fetchone()[0]}")

# Count thumbnails
c.execute("SELECT COUNT(*) FROM ai_thumbnails")
print(f"AIThumbnail: {c.fetchone()[0]}")

# Check orphaned scene analyses (video_id not in video_items)
c.execute("SELECT COUNT(*) FROM ai_scene_analyses WHERE video_id NOT IN (SELECT id FROM video_items)")
print(f"Orphaned scene analyses: {c.fetchone()[0]}")

# Check orphaned thumbnails
c.execute("SELECT COUNT(*) FROM ai_thumbnails WHERE video_id NOT IN (SELECT id FROM video_items)")
print(f"Orphaned thumbnails: {c.fetchone()[0]}")

# Videos WITH scene analyses
c.execute("SELECT COUNT(DISTINCT v.id) FROM video_items v JOIN ai_scene_analyses sa ON sa.video_id = v.id")
print(f"Videos with scene analyses: {c.fetchone()[0]}")

# Videos WITHOUT scene analyses
c.execute("SELECT COUNT(*) FROM video_items v WHERE v.id NOT IN (SELECT video_id FROM ai_scene_analyses)")
print(f"Videos without scene analyses: {c.fetchone()[0]}")

# Sample thumbnails - check file paths exist
print("\nSample thumbnails:")
c.execute("SELECT t.id, t.video_id, t.file_path FROM ai_thumbnails t LIMIT 10")
for row in c.fetchall():
    exists = os.path.isfile(row[2]) if row[2] else False
    print(f"  Thumb {row[0]}: video_id={row[1]} exists={exists} path={row[2]}")

# Get library_dir setting
c.execute("SELECT value FROM settings WHERE key = 'library_dir'")
row = c.fetchone()
library_dir = row[0] if row else None
print(f"\nlibrary_dir: {library_dir}")

# Check _PlayarrCache/thumbnails directory
if library_dir:
    cache_thumbs = os.path.join(library_dir, "_PlayarrCache", "thumbnails")
    if os.path.isdir(cache_thumbs):
        subdirs = os.listdir(cache_thumbs)
        print(f"\nCache thumbnail dirs: {len(subdirs)}")
        for sd in sorted(subdirs)[:20]:
            sd_path = os.path.join(cache_thumbs, sd)
            files = os.listdir(sd_path) if os.path.isdir(sd_path) else []
            # Check if this video_id exists
            c.execute("SELECT id, artist, title FROM video_items WHERE id = ?", (sd,))
            vi = c.fetchone()
            status = f"-> {vi[1]} - {vi[2]}" if vi else "ORPHANED"
            print(f"  {sd}/ ({len(files)} files) {status}")
    else:
        print(f"\nNo cache thumbs dir at {cache_thumbs}")

# Check for thumb_*.jpg files in video folders
print("\nVideo folders with thumb_*.jpg files on disk:")
c.execute("SELECT id, folder_path, artist, title FROM video_items WHERE folder_path IS NOT NULL")
count_with = 0
count_without = 0
for row in c.fetchall():
    vid, folder, artist, title = row
    if folder and os.path.isdir(folder):
        thumbs = [f for f in os.listdir(folder) if f.startswith("thumb_") and f.endswith(".jpg")]
        if thumbs:
            # Check if this video has AI records
            c.execute("SELECT COUNT(*) FROM ai_thumbnails WHERE video_id = ?", (vid,))
            db_count = c.fetchone()[0]
            print(f"  [{vid}] {artist} - {title}: {len(thumbs)} on disk, {db_count} in DB")
            count_with += 1
        else:
            count_without += 1
print(f"\nFolders with thumb files: {count_with}")
print(f"Folders without thumb files: {count_without}")

conn.close()
