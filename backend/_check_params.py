"""Check input_params of recent rescan jobs."""
import sqlite3, json, os

db = os.path.join(os.environ["APPDATA"], "Playarr", "data", "playarr.db")
conn = sqlite3.connect(db)
cur = conn.cursor()

cur.execute(
    "SELECT id, display_name, input_params FROM processing_jobs "
    "WHERE job_type='rescan' ORDER BY id DESC LIMIT 5"
)
for r in cur.fetchall():
    print(f"Job #{r[0]}: {r[1]}")
    if r[2]:
        try:
            params = json.loads(r[2])
            for k, v in params.items():
                print(f"  {k}: {v}")
        except Exception:
            print(f"  raw: {r[2]}")
    else:
        print("  (no params)")
    print()

conn.close()
