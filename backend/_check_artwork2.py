import sqlite3
from app.runtime_dirs import get_runtime_dirs
conn = sqlite3.connect(str(get_runtime_dirs().db_path))

# Check all Paramore videos
vids = conn.execute(
    "SELECT id, artist, title FROM video_items WHERE artist LIKE '%Paramore%'"
).fetchall()
print(f"=== Paramore videos ({len(vids)}) ===")
for v in vids:
    assets = conn.execute(
        "SELECT asset_type, status FROM media_assets WHERE video_id=?", (v[0],)
    ).fetchall()
    types = [f"{a[0]}({a[1]})" for a in assets]
    print(f"  id={v[0]} {v[1]} - {v[2]}: {', '.join(types) or 'NO ASSETS'}")

# How many videos total have artist_thumb or album_thumb?
total = conn.execute("SELECT COUNT(*) FROM video_items").fetchone()[0]
with_artist = conn.execute(
    "SELECT COUNT(DISTINCT video_id) FROM media_assets WHERE asset_type IN ('artist_thumb','artist_image') AND status='valid'"
).fetchone()[0]
with_album = conn.execute(
    "SELECT COUNT(DISTINCT video_id) FROM media_assets WHERE asset_type='album_thumb' AND status='valid'"
).fetchone()[0]
print(f"\n=== Coverage ===")
print(f"Total videos: {total}")
print(f"With artist_thumb/image: {with_artist} ({100*with_artist/total:.0f}%)")
print(f"With album_thumb: {with_album} ({100*with_album/total:.0f}%)")

# Sample some that DO have artist art
samples = conn.execute(
    "SELECT m.video_id, v.artist, v.title, m.asset_type, m.status, m.file_path "
    "FROM media_assets m JOIN video_items v ON m.video_id=v.id "
    "WHERE m.asset_type='artist_thumb' AND m.status='valid' LIMIT 5"
).fetchall()
print(f"\n=== Sample tracks WITH artist_thumb ===")
for s in samples:
    print(f"  vid={s[0]} {s[1]} - {s[2]}: {s[3]}({s[4]}) path={s[5]}")

conn.close()
