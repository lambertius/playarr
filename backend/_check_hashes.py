import sqlite3
from app.runtime_dirs import get_runtime_dirs
conn = sqlite3.connect(str(get_runtime_dirs().db_path))
cur = conn.execute(
    "SELECT id, video_id, asset_type, file_hash FROM media_assets "
    "WHERE asset_type IN ('video_thumb','poster') LIMIT 10"
)
for r in cur.fetchall():
    print(r)
conn.close()
