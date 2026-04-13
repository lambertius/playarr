import sqlite3
db = r"C:\Users\haydn\AppData\Roaming\Playarr\data\playarr.db"
conn = sqlite3.connect(db)

# List tables
cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("Tables:", tables)

# Check settings
for t in ["app_settings", "settings"]:
    if t in tables:
        cur = conn.execute(f"SELECT * FROM {t}")
        cols = [d[0] for d in cur.description]
        print(f"\n{t} columns: {cols}")
        for row in cur.fetchall():
            print(f"  {row}")

conn.close()
