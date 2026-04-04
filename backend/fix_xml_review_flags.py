"""
Batch-fix Playarr XML sidecar files to clear stale review flags.

For each .playarr.xml in the target directory:
  - Parse processing_state
  - If fully processed (metadata_scraped + imported + ai_enriched/metadata_resolved):
    - Set review_status to "none"
    - Remove review_reason and review_category
    - Add a review_history entry recording the batch clear

Usage:
    python fix_xml_review_flags.py [LIBRARY_DIR]

If LIBRARY_DIR is not given, defaults to the first argument or exits.
"""
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def is_fully_processed(root: ET.Element) -> bool:
    """Check if the XML processing_state indicates a fully-processed item."""
    state_el = root.find("processing_state")
    if state_el is None:
        return False

    steps = {}
    for step in state_el.findall("step"):
        name = step.get("name")
        if not name:
            continue
        completed_el = step.find("completed")
        steps[name] = completed_el is not None and (completed_el.text or "").strip().lower() == "true"

    return (
        steps.get("imported", False)
        and (steps.get("metadata_scraped", False) or steps.get("metadata_resolved", False))
        and (steps.get("ai_enriched", False) or steps.get("metadata_resolved", False))
    )


def fix_xml(filepath: str) -> bool:
    """Fix review flags in a single XML file. Returns True if modified."""
    try:
        tree = ET.parse(filepath)
    except ET.ParseError:
        print(f"  SKIP (parse error): {filepath}")
        return False

    root = tree.getroot()

    if not is_fully_processed(root):
        return False

    flags_el = root.find("flags")
    if flags_el is None:
        return False

    status_el = flags_el.find("review_status")
    current_status = status_el.text.strip() if status_el is not None and status_el.text else "none"

    if current_status not in ("needs_human_review", "needs_ai_review"):
        return False  # Already clear

    # Clear review flags
    if status_el is not None:
        status_el.text = "none"
    reason_el = flags_el.find("review_reason")
    if reason_el is not None:
        flags_el.remove(reason_el)
    cat_el = flags_el.find("review_category")
    category_text = cat_el.text.strip() if cat_el is not None and cat_el.text else "scanned"
    if cat_el is not None:
        flags_el.remove(cat_el)

    # Add review_history entry
    rh_el = flags_el.find("review_history")
    if rh_el is None:
        rh_el = ET.SubElement(flags_el, "review_history")

    entry = ET.SubElement(rh_el, "entry")
    ET.SubElement(entry, "action").text = "dismissed"
    ET.SubElement(entry, "category").text = category_text
    ET.SubElement(entry, "reason").text = "Batch-cleared: fully processed item"
    ET.SubElement(entry, "timestamp").text = datetime.now(timezone.utc).isoformat()

    # Write back
    tree.write(filepath, encoding="utf-8", xml_declaration=True)
    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: python fix_xml_review_flags.py <LIBRARY_DIR>")
        sys.exit(1)

    library_dir = sys.argv[1]
    if not os.path.isdir(library_dir):
        print(f"Error: {library_dir} is not a directory")
        sys.exit(1)

    xml_files = []
    for dirpath, dirnames, filenames in os.walk(library_dir):
        # Skip critical subdirs
        dirnames[:] = [d for d in dirnames if d not in ("_albums", "_artists", "_archive", "_PlayarrCache")]
        for fn in filenames:
            if fn.endswith(".playarr.xml"):
                xml_files.append(os.path.join(dirpath, fn))

    print(f"Found {len(xml_files)} .playarr.xml files in {library_dir}")

    fixed = 0
    for fp in xml_files:
        if fix_xml(fp):
            fixed += 1
            print(f"  FIXED: {os.path.basename(fp)}")

    print(f"\nDone. {fixed} of {len(xml_files)} files updated.")


if __name__ == "__main__":
    main()
