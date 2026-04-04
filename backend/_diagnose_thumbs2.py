"""Deeper diagnosis of thumbnail paths and video item state."""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "playarr.db")
conn = sqlite3.connect(db_path)
c = conn.cursor()

# Check all settings
print("=== Settings ===")
c.execute("SELECT key, value FROM settings ORDER BY key")
for row in c.fetchall():
    print(f"  {row[0]} = {row[1]}")

# Check video item paths
print("\n=== Video Item Path Patterns ===")
c.execute("SELECT file_path, folder_path FROM video_items LIMIT 5")
for row in c.fetchall():
    print(f"  file_path={row[0]}")
    print(f"  folder_path={row[1]}")
    print()

# Count non-null paths 
c.execute("SELECT COUNT(*) FROM video_items WHERE file_path IS NOT NULL AND file_path != ''")
print(f"Videos with file_path: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM video_items WHERE folder_path IS NOT NULL AND folder_path != ''")
print(f"Videos with folder_path: {c.fetchone()[0]}")

# Distinct thumbnail path prefixes
print("\n=== Thumbnail Path Prefixes ===")
c.execute("SELECT DISTINCT substr(file_path, 1, 50) FROM ai_thumbnails LIMIT 10")
for row in c.fetchall():
    print(f"  {row[0]}")

# Check if D:\MusicVideos exists
for check_path in [
    r"D:\MusicVideos",
    r"D:\MusicVideos\PlayarrCache",
    r"D:\MusicVideos\PlayarrCache\assets",
    r"D:\MusicVideos\PlayarrCache\assets\thumbnails",
    r"D:\MusicVideos\_PlayarrCache",
    r"D:\MusicVideos\_PlayarrCache\thumbnails",
]:
    exists = os.path.isdir(check_path)
    print(f"  {check_path}: {'EXISTS' if exists else 'MISSING'}")

# Check .env file
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(env_path):
    print(f"\n=== .env contents ===")
    with open(env_path) as f:
        print(f.read())
else:
    print(f"\n.env not found at {env_path}")

# Check if any thumbnail files exist anywhere on D:\MusicVideos
if os.path.isdir(r"D:\MusicVideos"):
    # Look for ANY thumb_*.jpg files
    import glob
    thumb_files = glob.glob(r"D:\MusicVideos\**\thumb_*.jpg", recursive=True)
    print(f"\n=== thumb_*.jpg files found under D:\\MusicVideos: {len(thumb_files)} ===")
    for f in thumb_files[:20]:
        print(f"  {f}")
    if len(thumb_files) > 20:
        print(f"  ... and {len(thumb_files) - 20} more")

conn.close()
