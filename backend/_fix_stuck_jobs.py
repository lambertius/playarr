"""One-time script to fix stuck production jobs 6 & 7."""
import sqlite3
import datetime
import os

db_path = os.path.join(os.path.dirname(__file__), "..", "data", "library", "playarr.db")
db_path = os.path.abspath(db_path)

if not os.path.isfile(db_path):
    print(f"DB not found at {db_path}")
    exit(1)

print(f"Using DB: {db_path}")
db = sqlite3.connect(db_path)

# Check what tables exist
tables = [t[0] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(f"Tables: {tables}")

# Find the jobs table
job_table = None
for t in tables:
    if 'job' in t.lower():
        job_table = t
        break

if not job_table:
    print("No jobs table found!")
    db.close()
    exit(1)

print(f"\nUsing table: {job_table}")

# Check current state of jobs 6 & 7
for r in db.execute(f"SELECT id, status, current_step, progress_percent, started_at, completed_at FROM {job_table} WHERE id IN (6, 7)"):
    print(f"  Job {r[0]}: status={r[1]}, step={r[2]}, progress={r[3]}, started={r[4]}, completed={r[5]}")

# Fix them
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
cur = db.execute(
    f"UPDATE {job_table} SET status='complete', completed_at=?, started_at=COALESCE(started_at, created_at, ?) WHERE id IN (6, 7) AND status='queued'",
    (now, now)
)
db.commit()
print(f"\nUpdated {cur.rowcount} rows")

# Verify
for r in db.execute(f"SELECT id, status, current_step, completed_at FROM {job_table} WHERE id IN (6, 7)"):
    print(f"  Job {r[0]}: status={r[1]}, step={r[2]}, completed={r[3]}")

# Also check parent batch job
for r in db.execute(f"SELECT id, status, current_step, display_name FROM {job_table} WHERE id = 5"):
    print(f"\n  Parent Job {r[0]}: status={r[1]}, step={r[2]}, name={r[3]}")

db.close()
print("\nDone!")
