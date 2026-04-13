"""Debug: check scan job params and excluded video state."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal
from app.models import ProcessingJob, VideoItem

db = SessionLocal()

# Check scan jobs
jobs = db.query(ProcessingJob).filter(
    ProcessingJob.job_type == "video_editor_scan"
).order_by(ProcessingJob.id.desc()).limit(5).all()

if not jobs:
    print("No video_editor_scan jobs found in DB")
else:
    for j in jobs:
        p = j.input_params or {}
        print(f"Job {j.id} | status={j.status}")
        print(f"  include_excluded={p.get('include_excluded')}")
        print(f"  skip_cropped={p.get('skip_cropped')}")
        print(f"  skip_trimmed={p.get('skip_trimmed')}")
        r = p.get("results")
        if r:
            print(f"  results_count={len(r)}")

# Check excluded state
exc_count = db.query(VideoItem).filter(VideoItem.exclude_from_editor_scan == True).count()
edit_count = db.query(VideoItem).filter(VideoItem.editor_edit_type.isnot(None)).count()
print(f"\nDB state: excluded={exc_count}, has_edit_type={edit_count}")

# List the excluded ones
excluded = db.query(VideoItem).filter(VideoItem.exclude_from_editor_scan == True).all()
for v in excluded:
    print(f"  EXCLUDED: {v.artist} - {v.title} (edit_type={v.editor_edit_type})")

db.close()
