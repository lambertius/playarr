"""Debug: check sidecar XMLs for screenshot videos."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal
from app.models import VideoItem
from app.services.playarr_xml import find_playarr_xml, parse_playarr_xml

db = SessionLocal()
targets = [
    ("3 Doors Down", "Here Without You"),
    ("Aerosmith", "Dude (Looks Like a Lady)"),
    ("Aerosmith", "Livin' on the Edge"),
    ("Afroman", "Because I Got High"),
    ("Alice Deejay", "Better Off Alone"),
    ("Alien Ant Farm", "Smooth Criminal"),
    ("Amiel", "Lovesong"),
    ("Aqua", "Barbie Girl"),
]
for artist, title in targets:
    v = db.query(VideoItem).filter(
        VideoItem.artist == artist,
        VideoItem.title.like(f"%{title}%"),
    ).first()
    if not v or not v.file_path:
        print(f"NOT FOUND: {artist} - {title}")
        continue
    folder = os.path.dirname(v.file_path)
    xml = find_playarr_xml(folder, video_file=v.file_path)
    if xml:
        xd = parse_playarr_xml(xml)
        print(f"{v.artist} - {v.title}")
        print(f"  xml_exclude={xd.get('exclude_from_editor_scan')} xml_edit_type={xd.get('editor_edit_type')}")
    else:
        print(f"{v.artist} - {v.title} | NO SIDECAR XML found in {folder}")

db.close()
