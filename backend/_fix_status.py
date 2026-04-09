"""One-time fix: change rescan_metadata_task from status=complete to status=finalizing."""
import re

with open("app/tasks.py", "r", encoding="utf-8") as f:
    content = f.read()

# Find the exact pattern: _update_job(...status=JobStatus.complete...Finalizing...completed_at...)
old = '_update_job(job_id, status=JobStatus.complete, progress_percent=90,\n                    current_step="Finalizing", completed_at=datetime.now(timezone.utc))'
new = '_update_job(job_id, status=JobStatus.finalizing, progress_percent=90,\n                    current_step="Finalizing")'

count = content.count(old)
print(f"Found {count} match(es)")
assert count == 1, f"Expected 1, found {count}"

content = content.replace(old, new, 1)

with open("app/tasks.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Done - replaced status=complete with status=finalizing in rescan_metadata_task")
