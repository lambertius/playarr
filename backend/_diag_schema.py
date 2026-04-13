import sqlite3

c = sqlite3.connect('playarr.db', timeout=10)
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)

# Check for asset-related tables
for t in tables:
    if 'asset' in t.lower() or 'art' in t.lower() or 'thumb' in t.lower() or 'source' in t.lower() or 'poster' in t.lower():
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})").fetchall()]
        cnt = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"\n{t} ({cnt} rows): {cols}")

# Check video_items columns too
cols = [r[1] for r in c.execute("PRAGMA table_info(video_items)").fetchall()]
print(f"\nvideo_items columns: {cols}")

c.close()
