"""Diagnose production queue state."""
import sqlite3, json, os

db = os.path.join(os.environ["APPDATA"], "Playarr", "data", "playarr.db")
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

print("=== BATCH PARENT JOBS (recent) ===")
for r in conn.execute("""SELECT id, job_type, status, current_step, progress_percent, 
    display_name, input_params FROM processing_jobs 
    WHERE job_type LIKE 'batch%' ORDER BY id DESC LIMIT 3"""):
    d = dict(r)
    params = d.pop('input_params', None)
    if params:
        try:
            p = json.loads(params)
            d['sub_job_count'] = len(p.get('sub_job_ids', []))
        except: pass
    print(d)

print("\n=== COMPLETE JOBS WITH progress < 100 (recent 20) ===")
for r in conn.execute("""SELECT id, job_type, status, current_step, progress_percent, 
    display_name, completed_at, updated_at FROM processing_jobs 
    WHERE status = 'complete' AND progress_percent < 100 
    ORDER BY id DESC LIMIT 20"""):
    print(dict(r))

print("\n=== COMPLETE JOBS WITH step != 'Import complete' (recent 20) ===")
for r in conn.execute("""SELECT id, job_type, status, current_step, progress_percent, 
    display_name, completed_at, updated_at FROM processing_jobs 
    WHERE status = 'complete' AND current_step != 'Import complete' 
    AND current_step IS NOT NULL
    ORDER BY id DESC LIMIT 20"""):
    print(dict(r))

print("\n=== NON-TERMINAL JOBS ===")
for r in conn.execute("""SELECT id, job_type, status, current_step, progress_percent, 
    display_name FROM processing_jobs 
    WHERE status NOT IN ('complete', 'failed', 'cancelled', 'skipped') 
    ORDER BY id DESC LIMIT 10"""):
    print(dict(r))

print("\n=== JOBS 263-265 (from screenshot) ===")
for r in conn.execute("""SELECT id, job_type, status, current_step, progress_percent, 
    display_name, completed_at, updated_at, video_id FROM processing_jobs 
    WHERE id >= 263 ORDER BY id"""):
    print(dict(r))

conn.close()
