"""
Import simulation: verify corrected tracks round-trip correctly through XML sidecars.

For each track that was corrected by _fix_feat_artists.py:
  1. Re-parse the .playarr.xml sidecar
  2. Verify artist and artist_ids match the DB values
  3. Verify normalize_feat_to_semicolons is idempotent (no further splitting)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal
from app.models import VideoItem
from app.services.source_validation import normalize_feat_to_semicolons, parse_multi_artist
from app.services.playarr_xml import parse_playarr_xml
from sqlalchemy.orm import joinedload


def main():
    db = SessionLocal()
    try:
        # Find all tracks with semicolons in artist (the corrected ones)
        items = (
            db.query(VideoItem)
            .filter(VideoItem.artist.contains("; "))
            .options(joinedload(VideoItem.media_assets))
            .all()
        )

        print(f"Verifying {len(items)} tracks with multi-artist format...\n")

        pass_count = 0
        fail_count = 0
        errors = []

        for vi in items:
            track_label = f"#{vi.id} {vi.artist} — {vi.title}"
            track_errors = []

            # 1. Idempotency check — re-normalizing shouldn't change anything
            re_normalized = normalize_feat_to_semicolons(vi.artist)
            if re_normalized != vi.artist:
                track_errors.append(
                    f"  IDEMPOTENCY FAIL: '{vi.artist}' → '{re_normalized}'"
                )

            # 2. Verify artist_ids entries match the semicolon-split artist names
            expected_names = [n.strip() for n in vi.artist.split("; ") if n.strip()]
            if vi.artist_ids:
                db_names = [e.get("name", "") for e in vi.artist_ids]
                if db_names != expected_names:
                    track_errors.append(
                        f"  ARTIST_IDS MISMATCH: DB={db_names} vs expected={expected_names}"
                    )
                # Also check no entry has semicolons (improperly merged)
                for e in vi.artist_ids:
                    if "; " in e.get("name", ""):
                        track_errors.append(
                            f"  MERGED ENTRY: '{e['name']}'"
                        )

            # 3. XML sidecar round-trip
            if vi.folder_path and os.path.isdir(vi.folder_path):
                # Find the .playarr.xml file
                xml_files = [f for f in os.listdir(vi.folder_path) if f.endswith(".playarr.xml")]
                if xml_files:
                    xml_path = os.path.join(vi.folder_path, xml_files[0])
                    xml_data = parse_playarr_xml(xml_path)

                    xml_artist = xml_data.get("artist", "")
                    if xml_artist != vi.artist:
                        track_errors.append(
                            f"  XML ARTIST MISMATCH: DB='{vi.artist}' vs XML='{xml_artist}'"
                        )

                    xml_ids = xml_data.get("artist_ids", [])
                    if vi.artist_ids and xml_ids:
                        db_id_names = [e.get("name") for e in vi.artist_ids]
                        xml_id_names = [e.get("name") for e in xml_ids]
                        if db_id_names != xml_id_names:
                            track_errors.append(
                                f"  XML ARTIST_IDS MISMATCH: DB={db_id_names} vs XML={xml_id_names}"
                            )
                else:
                    track_errors.append("  NO XML SIDECAR FOUND")
            else:
                track_errors.append("  FOLDER MISSING — cannot verify XML")

            if track_errors:
                fail_count += 1
                errors.append((track_label, track_errors))
            else:
                pass_count += 1

        # Report
        print(f"{'='*70}")
        print(f"  Import Simulation Results")
        print(f"  Passed: {pass_count}")
        print(f"  Failed: {fail_count}")
        print(f"{'='*70}\n")

        if errors:
            for label, errs in errors:
                print(f"  FAIL: {label}")
                for e in errs:
                    print(f"    {e}")
                print()
        else:
            print("  All tracks verified successfully.")
            print("  - Artist strings are idempotent (no further splitting)")
            print("  - artist_ids match parsed structure")
            print("  - XML sidecars round-trip correctly")
            print()

    finally:
        db.close()


if __name__ == "__main__":
    main()
