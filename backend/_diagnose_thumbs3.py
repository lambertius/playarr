"""Check whether video folder paths actually exist on disk."""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "playarr.db")
conn = sqlite3.connect(db_path)
c = conn.cursor()

c.execute("SELECT id, folder_path, artist, title FROM video_items WHERE folder_path IS NOT NULL")
exists_count = 0
missing_count = 0
missing_examples = []

for row in c.fetchall():
    vid, folder, artist, title = row
    if folder and os.path.isdir(folder):
        exists_count += 1
    else:
        missing_count += 1
        if len(missing_examples) < 5:
            missing_examples.append((vid, folder, artist, title))

print(f"Folder paths that exist on disk: {exists_count}")
print(f"Folder paths MISSING on disk: {missing_count}")
if missing_examples:
    print("\nMissing examples:")
    for vid, f, a, t in missing_examples:
        print(f"  [{vid}] {a} - {t}")
        print(f"      path: {f}")

# Check what's actually in D:\MusicVideos\Library
lib_dir = r"D:\MusicVideos\Library"
if os.path.isdir(lib_dir):
    items = os.listdir(lib_dir)
    print(f"\nContents of {lib_dir}: {len(items)} items")
    for item in sorted(items)[:20]:
        full = os.path.join(lib_dir, item)
        if os.path.isdir(full):
            subcount = len(os.listdir(full))
            print(f"  {item}/ ({subcount} items)")
        else:
            print(f"  {item}")
else:
    print(f"\n{lib_dir} does not exist!")

# Also check D:\MusicVideos top-level to understand the structure
print(f"\nTop-level in D:\\MusicVideos:")
for item in sorted(os.listdir(r"D:\MusicVideos"))[:30]:
    full = os.path.join(r"D:\MusicVideos", item)
    if os.path.isdir(full):
        subcount = len(os.listdir(full))
        print(f"  {item}/ ({subcount} items)")

# Now check the actual state of the 72 fixed thumbnails
print("\n=== Verification: sample of fixed thumbnails ===")
c.execute("""
    SELECT t.id, t.video_id, t.file_path, v.artist, v.title
    FROM ai_thumbnails t
    JOIN video_items v ON v.id = t.video_id
    WHERE t.file_path LIKE '%_PlayarrCache%'
    LIMIT 10
""")
for row in c.fetchall():
    exists = os.path.isfile(row[2]) if row[2] else False
    print(f"  Thumb {row[0]}: [{row[1]}] {row[3]} - {row[4]} exists={exists}")

# And sample of unfixed thumbnails
print("\n=== Sample unfixed thumbnails ===")
c.execute("""
    SELECT t.id, t.video_id, t.file_path, v.artist, v.title
    FROM ai_thumbnails t
    JOIN video_items v ON v.id = t.video_id
    WHERE t.file_path LIKE '%PlayarrCache\assets%'
    LIMIT 10
""")
for row in c.fetchall():
    print(f"  Thumb {row[0]}: [{row[1]}] {row[3]} - {row[4]} path={row[2]}")

# Count fixed vs unfixed
c.execute("SELECT COUNT(*) FROM ai_thumbnails WHERE file_path LIKE '%_PlayarrCache\\thumbnails%'")
fixed = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM ai_thumbnails WHERE file_path LIKE '%PlayarrCache\\assets%'")
unfixed = c.fetchone()[0]
print(f"\nFixed thumbnails: {fixed}")
print(f"Unfixed thumbnails: {unfixed}")
print(f"Total: {fixed + unfixed}")

conn.close()
