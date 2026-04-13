import sqlite3, os
from app.runtime_dirs import get_runtime_dirs
conn = sqlite3.connect(str(get_runtime_dirs().db_path))
vid = conn.execute(
    "SELECT id FROM video_items WHERE artist LIKE '%Paramore%' AND title LIKE '%Still Into You%'"
).fetchone()
print("video_id:", vid)
if vid:
    assets = conn.execute(
        "SELECT id, asset_type, status, file_path, source_url, file_hash FROM media_assets WHERE video_id=?",
        (vid[0],)
    ).fetchall()
    for a in assets:
        fpath = a[3]
        exists = os.path.isfile(fpath) if fpath else False
        print(f"  id={a[0]} type={a[1]} status={a[2]} hash={a[5][:12] if a[5] else None} exists={exists}")
        print(f"    path={fpath}")
        if a[4]:
            print(f"    source_url={a[4]}")
conn.close()
