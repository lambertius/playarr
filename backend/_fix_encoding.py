"""Fix double-encoded UTF-8 mojibake in tasks.py.

The file was corrupted by an external tool that re-saved UTF-8 as CP1252.
This script applies targeted fixes for known mojibake sequences.

CP1252 double-encoding: UTF-8 bytes were read as CP1252, each byte mapped
to its CP1252 Unicode codepoint, then re-saved as UTF-8.
"""
import sys

path = "app/tasks.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Mojibake sequences (as they appear in the double-encoded file)
# mapped to the correct Unicode character.
# Format: CP1252 interpretation of UTF-8 bytes → original char
MOJIBAKE_MAP = {
    "\u00e2\u20ac\u201d": "\u2014",   # — em dash    (e2 80 94)
    "\u00e2\u20ac\u201c": "\u2013",   # – en dash    (e2 80 93)
    "\u00e2\u2020\u2019": "\u2192",   # → right arrow (e2 86 92)
    "\u00e2\u0153\u201c": "\u2713",   # ✓ checkmark  (e2 9c 93)
    "\u00e2\u0153\u201d": "\u2714",   # ✔ heavy check (e2 9c 94)
    "\u00e2\u0153\u2014": "\u2717",   # ✗ cross mark (e2 9c 97)
    "\u00e2\u201d\u20ac": "\u2500",   # ─ box horiz  (e2 94 80)
    "\u00e2\u2022\u0090": "\u2550",   # ═ double horiz (e2 95 90)
    "\u00e2\u20ac\u00a2": "\u2022",   # • bullet     (e2 80 a2)
    "\u00e2\u0161\u00a0": "\u26a0",   # ⚠ warning    (e2 9a a0)
    "\u00c2\u00b7": "\u00b7",         # · middle dot (c2 b7)
}

original = content
for bad, good in MOJIBAKE_MAP.items():
    content = content.replace(bad, good)

# Count changed lines
orig_lines = original.split("\n")
new_lines = content.split("\n")
changed = sum(1 for a, b in zip(orig_lines, new_lines) if a != b)

if changed == 0:
    print("No mojibake found - file looks clean")
    sys.exit(0)

shown = 0
for i, (a, b) in enumerate(zip(orig_lines, new_lines)):
    if a != b and shown < 20:
        print(f"L{i+1}:")
        print(f"  OLD: {a.strip()[:100]}")
        print(f"  NEW: {b.strip()[:100]}")
        shown += 1

print(f"\nTotal lines changed: {changed}")

if "--apply" in sys.argv:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Applied fixes!")
else:
    print("\nDry run. Use --apply to write changes.")
