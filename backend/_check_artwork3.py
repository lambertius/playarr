import sqlite3, json
from app.runtime_dirs import get_runtime_dirs
conn = sqlite3.connect(str(get_runtime_dirs().db_path))

# Check processing_state for video 840
ps = conn.execute(
    "SELECT processing_state FROM video_items WHERE id=840"
).fetchone()
if ps and ps[0]:
    state = json.loads(ps[0]) if isinstance(ps[0], str) else ps[0]
    for k, v in sorted(state.items()):
        print(f"  {k}: {v}")

# How many videos are missing artist_thumb but have poster?
missing = conn.execute("""
    SELECT v.id, v.artist, v.title
    FROM video_items v
    WHERE EXISTS (
        SELECT 1 FROM media_assets m WHERE m.video_id=v.id AND m.asset_type='poster' AND m.status='valid'
    )
    AND NOT EXISTS (
        SELECT 1 FROM media_assets m WHERE m.video_id=v.id AND m.asset_type IN ('artist_thumb','artist_image') AND m.status='valid'
    )
    LIMIT 20
""").fetchall()
total_missing = conn.execute("""
    SELECT COUNT(*)
    FROM video_items v
    WHERE EXISTS (
        SELECT 1 FROM media_assets m WHERE m.video_id=v.id AND m.asset_type='poster' AND m.status='valid'
    )
    AND NOT EXISTS (
        SELECT 1 FROM media_assets m WHERE m.video_id=v.id AND m.asset_type IN ('artist_thumb','artist_image') AND m.status='valid'
    )
""").fetchone()[0]
print(f"\n=== Videos with poster but NO artist art ({total_missing} total) ===")
for v in missing:
    print(f"  id={v[0]} {v[1]} - {v[2]}")

# Same for album_thumb
total_missing_album = conn.execute("""
    SELECT COUNT(*)
    FROM video_items v
    WHERE EXISTS (
        SELECT 1 FROM media_assets m WHERE m.video_id=v.id AND m.asset_type='poster' AND m.status='valid'
    )
    AND NOT EXISTS (
        SELECT 1 FROM media_assets m WHERE m.video_id=v.id AND m.asset_type='album_thumb' AND m.status='valid'
    )
""").fetchone()[0]
print(f"\n=== Videos with poster but NO album art: {total_missing_album} total ===")

conn.close()
