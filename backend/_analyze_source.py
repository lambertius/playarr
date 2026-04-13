"""Analyze V:\ source folder and cross-reference with existing library for duplicate detection testing."""
import os
import sys
import re
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SOURCE = r"V:\\"
DB_PATH = r"C:\Users\haydn\AppData\Roaming\Playarr\data\playarr.db"
if not os.path.exists(DB_PATH):
    DB_PATH = "playarr.db"

# ── Parse folder names from V:\ ──
FOLDER_RE = re.compile(r'^(.+?)\s*-\s*(.+?)\s*\[(\d+p)\]$')
VIDEO_EXTS = {'.mp4', '.mkv', '.webm', '.avi', '.mpg', '.mov', '.wmv', '.flv'}

def normalize_text(t):
    """Normalize text for fuzzy comparison."""
    t = t.lower().strip()
    t = re.sub(r'[^\w\s]', '', t)  # remove punctuation
    t = re.sub(r'\s+', ' ', t)     # collapse whitespace
    return t

print("Scanning V:\\ folder names...")
source_items = []
for name in os.listdir(SOURCE):
    path = os.path.join(SOURCE, name)
    if not os.path.isdir(path):
        continue
    m = FOLDER_RE.match(name)
    if m:
        artist, title, res = m.group(1), m.group(2), m.group(3)
    else:
        artist, title, res = None, None, None
    
    # Find video file
    video_file = None
    nfo_file = None
    has_artwork = False
    try:
        for f in os.listdir(path):
            ext = os.path.splitext(f)[1].lower()
            if ext in VIDEO_EXTS:
                video_file = os.path.join(path, f)
            elif ext == '.nfo':
                nfo_file = os.path.join(path, f)
            elif ext in ('.jpg', '.png'):
                has_artwork = True
    except:
        pass
    
    source_items.append({
        'folder': name,
        'path': path,
        'artist': artist,
        'title': title,
        'resolution': res,
        'video_file': video_file,
        'nfo_file': nfo_file,
        'has_artwork': has_artwork,
    })

print(f"Total folders in V:\\: {len(source_items)}")
parsed = [s for s in source_items if s['artist']]
print(f"Parsed artist-title: {len(parsed)}")
has_video = [s for s in source_items if s['video_file']]
print(f"Has video file: {len(has_video)}")
no_video = [s for s in source_items if not s['video_file']]
print(f"Empty (no video): {len(no_video)}")
has_nfo = [s for s in source_items if s['nfo_file']]
print(f"Has NFO: {len(has_nfo)}")

# ── Load existing library ──
db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row
library = db.execute("""
    SELECT id, artist, title, file_path, folder_path, 
           resolution_label, mb_recording_id, mb_release_id,
           version_type
    FROM video_items
    WHERE folder_path IS NOT NULL
""").fetchall()
print(f"\nExisting library: {len(library)} items")

# Build lookup sets
lib_artist_title = {}
lib_norm = {}
for v in library:
    key = (v['artist'].lower().strip(), v['title'].lower().strip())
    lib_artist_title.setdefault(key, []).append(v)
    nk = (normalize_text(v['artist']), normalize_text(v['title']))
    lib_norm.setdefault(nk, []).append(v)

# ── Cross-reference ──
exact_dupes = []
fuzzy_dupes = []
new_items = []
unparseable = []

for s in source_items:
    if not s['artist']:
        unparseable.append(s)
        continue
    
    key = (s['artist'].lower().strip(), s['title'].lower().strip())
    nk = (normalize_text(s['artist']), normalize_text(s['title']))
    
    if key in lib_artist_title:
        matches = lib_artist_title[key]
        exact_dupes.append((s, matches))
    elif nk in lib_norm:
        matches = lib_norm[nk]
        fuzzy_dupes.append((s, matches))
    else:
        new_items.append(s)

print(f"\n{'='*60}")
print(f"CROSS-REFERENCE RESULTS")
print(f"{'='*60}")
print(f"Exact artist+title matches: {len(exact_dupes)}")
print(f"Fuzzy matches (normalized): {len(fuzzy_dupes)}")
print(f"New items (not in library): {len(new_items)}")
print(f"Unparseable folder names:   {len(unparseable)}")

# Show resolution comparison for exact dupes
print(f"\n{'='*60}")
print(f"EXACT DUPLICATES — RESOLUTION COMPARISON")
print(f"{'='*60}")
upgrade_count = 0
downgrade_count = 0
same_count = 0

for s, matches in exact_dupes:
    if not s['video_file']:
        continue
    src_res = s['resolution'] or '?'
    for m in matches:
        lib_res = m['resolution_label'] or '?'
        src_h = int(re.search(r'(\d+)', src_res).group(1)) if re.search(r'(\d+)', src_res) else 0
        lib_h = int(re.search(r'(\d+)', lib_res).group(1)) if re.search(r'(\d+)', lib_res) else 0
        
        if src_h > lib_h:
            status = "UPGRADE"
            upgrade_count += 1
        elif src_h < lib_h:
            status = "DOWNGRADE"
            downgrade_count += 1
        else:
            status = "SAME"
            same_count += 1
        
        if status != "SAME":
            print(f"  {status}: {s['artist']} - {s['title']}  V:\\={src_res} vs lib={lib_res} (v{m['id']})")

print(f"\nUpgrades: {upgrade_count}  |  Same: {same_count}  |  Downgrades: {downgrade_count}")

# Show fuzzy matches
if fuzzy_dupes:
    print(f"\n{'='*60}")
    print(f"FUZZY MATCHES (punctuation/case differences)")
    print(f"{'='*60}")
    for s, matches in fuzzy_dupes:
        for m in matches:
            print(f"  V:\\  {s['artist']} - {s['title']}")
            print(f"  LIB: {m['artist']} - {m['title']} (v{m['id']})")
            print()

# Show a sample of new items
if new_items:
    with_video = [n for n in new_items if n['video_file']]
    without_video = [n for n in new_items if not n['video_file']]
    print(f"\n{'='*60}")
    print(f"NEW ITEMS (with video file): {len(with_video)}")
    print(f"NEW ITEMS (empty folders):   {len(without_video)}")
    print(f"{'='*60}")
    for n in with_video[:20]:
        print(f"  {n['artist']} - {n['title']} [{n['resolution']}]")
    if len(with_video) > 20:
        print(f"  ... and {len(with_video)-20} more")

# Check for NFO content patterns
if has_nfo:
    print(f"\n{'='*60}")
    print(f"SAMPLE NFO CONTENT")
    print(f"{'='*60}")
    sample_nfos = [s for s in source_items if s['nfo_file']][:3]
    for s in sample_nfos:
        print(f"\n--- {s['folder']} ---")
        try:
            with open(s['nfo_file'], 'r', encoding='utf-8', errors='replace') as f:
                content = f.read(500)
            print(content)
        except Exception as e:
            print(f"  Error reading: {e}")

db.close()
