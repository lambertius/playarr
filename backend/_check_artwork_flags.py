"""Check false positive rate for artwork_incomplete review flags."""
import sqlite3
import os
import re

DB_PATH = r"C:\Users\haydn\AppData\Roaming\Playarr\data\playarr.db"
if not os.path.exists(DB_PATH):
    DB_PATH = "playarr.db"  # fallback to dev DB

db = sqlite3.connect(DB_PATH, timeout=5)
cur = db.cursor()
print(f"Using DB: {DB_PATH}")

# Get library dir
try:
    cur.execute("SELECT value FROM app_settings WHERE key = 'library_dir' AND user_id IS NULL")
    row = cur.fetchone()
    lib_dir = row[0] if row else r"D:\MusicVideos"
except Exception:
    lib_dir = r"D:\MusicVideos"
artists_dir = os.path.join(lib_dir, "_artists")
albums_dir = os.path.join(lib_dir, "_albums")
print(f"Library: {lib_dir}")
print(f"Artists dir exists: {os.path.exists(artists_dir)}")
print(f"Albums dir exists: {os.path.exists(albums_dir)}")

def _safe_name(name):
    s = re.sub(r'[<>:"/\\|?*]', '', name or '').strip()
    return re.sub(r'\s+', ' ', s)

# Check truly missing artist_thumb items
cur.execute("""
    SELECT vi.id, vi.artist, vi.artist_entity_id
    FROM video_items vi
    WHERE vi.review_category = 'artwork_incomplete'
    AND vi.review_reason LIKE '%artist_thumb%'
""")
rows = cur.fetchall()

found_in_cache = 0
found_on_disk = 0
no_entity_art = 0
checked = 0

for vid_id, artist, ae_id in rows:
    cur2 = db.cursor()
    cur2.execute("SELECT 1 FROM media_assets WHERE video_id = ? AND asset_type = 'artist_thumb'", (vid_id,))
    if cur2.fetchone():
        continue  # already has it (false positive)
    checked += 1

    # Check CachedAsset
    cur3 = db.cursor()
    cur3.execute("SELECT local_cache_path FROM cached_assets WHERE entity_type = 'artist' AND entity_id = ? AND kind = 'poster'", (ae_id,))
    ca = cur3.fetchone()
    if ca and ca[0] and os.path.isfile(ca[0]):
        found_in_cache += 1
        if found_in_cache <= 3:
            print(f"  #{vid_id} {artist}: found in CachedAsset at {ca[0]}")
        continue

    # Check on disk
    safe = _safe_name(artist.split(";")[0].strip() if artist else "")
    candidate = os.path.join(artists_dir, safe, "poster.jpg")
    if os.path.isfile(candidate):
        found_on_disk += 1
        if found_on_disk <= 3:
            print(f"  #{vid_id} {artist}: found on disk at {candidate}")
        continue

    # Check sibling
    cur4 = db.cursor()
    cur4.execute("""
        SELECT ma.file_path FROM media_assets ma
        JOIN video_items vi2 ON ma.video_id = vi2.id
        WHERE ma.asset_type = 'artist_thumb' AND ma.status = 'valid'
        AND vi2.artist_entity_id = ? AND ma.video_id != ?
        LIMIT 1
    """, (ae_id, vid_id))
    sib = cur4.fetchone()
    if sib and sib[0] and os.path.isfile(sib[0]):
        found_on_disk += 1
        if found_on_disk <= 3:
            print(f"  #{vid_id} {artist}: found via sibling at {sib[0]}")
        continue

    no_entity_art += 1
    if no_entity_art <= 10:
        print(f"  #{vid_id} {artist}: NO artwork anywhere (entity={ae_id})")

print(f"\nTruly missing artist_thumb (no existing asset): {checked}")
print(f"  Found in CachedAsset: {found_in_cache}")
print(f"  Found on disk/sibling: {found_on_disk}")
print(f"  No artwork anywhere: {no_entity_art}")
db.close()
