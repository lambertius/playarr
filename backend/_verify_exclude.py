"""Verify: sidecar XML has exclude flag, and update-existing restores it."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal
from app.models import VideoItem
from app.services.playarr_xml import find_playarr_xml, parse_playarr_xml

db = SessionLocal()

# Step 1: Check DB state and sidecar for videos 6 and 22
for vid in [6, 22]:
    v = db.query(VideoItem).get(vid)
    print(f"=== Video {vid}: {v.artist} - {v.title} ===")
    print(f"  DB exclude={v.exclude_from_editor_scan}  edit_type={v.editor_edit_type}")
    
    folder = os.path.dirname(v.file_path)
    xml = find_playarr_xml(folder, video_file=v.file_path)
    if xml:
        xd = parse_playarr_xml(xml)
        print(f"  XML exclude={xd.get('exclude_from_editor_scan')}  edit_type={xd.get('editor_edit_type')}")
    else:
        print(f"  NO SIDECAR XML")

# Step 2: Reset DB flags to simulate fresh DB
print("\n--- Resetting DB flags to simulate fresh DB ---")
for vid in [6, 22]:
    v = db.query(VideoItem).get(vid)
    v.exclude_from_editor_scan = False
    v.editor_edit_type = None
db.commit()

for vid in [6, 22]:
    v = db.query(VideoItem).get(vid)
    print(f"  After reset: Video {vid} exclude={v.exclude_from_editor_scan}")

# Step 3: Run _apply_sidecar_to_existing to restore from XML
print("\n--- Running _apply_sidecar_to_existing ---")
from app.tasks import _apply_sidecar_to_existing
for vid in [6, 22]:
    v = db.query(VideoItem).get(vid)
    folder = os.path.dirname(v.file_path)
    xml = find_playarr_xml(folder, video_file=v.file_path)
    if xml:
        xd = parse_playarr_xml(xml)
        _apply_sidecar_to_existing(db, v, xd, job_id=0)

# Step 4: Verify restored
print("\n--- After restore ---")
for vid in [6, 22]:
    v = db.query(VideoItem).get(vid)
    print(f"  Video {vid}: {v.artist} - {v.title}")
    print(f"    DB exclude={v.exclude_from_editor_scan}  edit_type={v.editor_edit_type}")

db.close()
