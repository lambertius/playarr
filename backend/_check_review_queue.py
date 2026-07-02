"""Quick diagnostic: check production review queue state."""
import sqlite3
import json
import os

DB = os.path.join(os.environ["APPDATA"], "Playarr", "data", "playarr.db")
db = sqlite3.connect(DB)
cur = db.cursor()

print("=== Review Status Counts ===")
for row in cur.execute(
    "SELECT review_status, review_category, COUNT(*) "
    "FROM video_items "
    "WHERE review_status IS NOT NULL AND review_status != 'none' "
    "GROUP BY review_status, review_category "
    "ORDER BY review_status, review_category"
):
    print(f"  status={row[0]!r}  category={row[1]!r}  count={row[2]}")

print("\n=== AI Partial Items (first 5) ===")
for row in cur.execute(
    "SELECT id, title, review_status, review_reason, review_category, processing_state "
    "FROM video_items WHERE review_category = 'ai_partial' LIMIT 5"
):
    ps = row[5]
    try:
        ps = json.loads(ps) if ps else {}
    except Exception:
        pass
    ai = ps.get("ai_enriched", {}) if isinstance(ps, dict) else "?"
    sc = ps.get("scenes_analyzed", {}) if isinstance(ps, dict) else "?"
    print(f"  id={row[0]} title={row[1]!r}")
    print(f"    status={row[2]!r} reason={row[3]!r} category={row[4]!r}")
    print(f"    ai_enriched={ai}  scenes_analyzed={sc}")

print("\n=== Review History for AI Partial (first 3 with history) ===")
for row in cur.execute(
    "SELECT id, title, review_history FROM video_items "
    "WHERE review_category = 'ai_partial' AND review_history IS NOT NULL LIMIT 3"
):
    hist = row[2]
    try:
        hist = json.loads(hist) if hist else []
    except Exception:
        pass
    print(f"  id={row[0]} title={row[1]!r} history={hist}")

print("\n=== Items With review_status='reviewed' (sample 5) ===")
for row in cur.execute(
    "SELECT id, title, review_status, review_category FROM video_items "
    "WHERE review_status = 'reviewed' LIMIT 5"
):
    print(f"  id={row[0]} title={row[1]!r} status={row[2]!r} category={row[3]!r}")

print("\n=== Total reviewed vs needs_human_review ===")
for row in cur.execute(
    "SELECT review_status, COUNT(*) FROM video_items GROUP BY review_status"
):
    print(f"  {row[0]}: {row[1]}")

db.close()
