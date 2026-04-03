"""
Playarr Installer Build Script

Orchestrates the full build process:
  1. Builds the frontend (npm run build)
  2. Generates a .ico from the SVG favicon
  3. Runs PyInstaller to create the standalone bundle
  4. Validates the output

Usage:
    python build_installer.py           # full build
    python build_installer.py --skip-frontend   # skip npm build (already done)

Prerequisites:
    pip install pyinstaller Pillow
    Node.js 18+ with npm (for frontend build)
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
FRONTEND_DIST = FRONTEND / "dist"
DIST_DIR = ROOT / "dist" / "Playarr"
ICO_PATH = ROOT / "playarr.ico"


def banner(msg: str):
    print(f"\n{'=' * 50}")
    print(f"  {msg}")
    print(f"{'=' * 50}\n")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True):
    """Run a subprocess and stream output."""
    print(f"  > {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if check and result.returncode != 0:
        print(f"\n  ERROR: Command failed with exit code {result.returncode}")
        sys.exit(1)
    return result


def build_frontend():
    """Install deps and build the React frontend."""
    banner("Building Frontend")
    if not (FRONTEND / "package.json").is_file():
        print("  ERROR: frontend/package.json not found")
        sys.exit(1)
    run(["npm", "install"], cwd=FRONTEND)
    run(["npm", "run", "build"], cwd=FRONTEND)
    if not (FRONTEND_DIST / "index.html").is_file():
        print("  ERROR: Frontend build did not produce dist/index.html")
        sys.exit(1)
    print(f"  Frontend built to {FRONTEND_DIST}")


def generate_ico():
    """Generate playarr.ico from the tray icon code."""
    banner("Generating Application Icon")
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  WARNING: Pillow not installed — skipping .ico generation")
        return False

    sizes = [16, 32, 48, 64, 128, 256]
    images = []

    for size in sizes:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Background rounded rectangle
        bg_color = (28, 34, 48, 255)
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 5, fill=bg_color)

        # Border
        border_color = (43, 50, 69, 255)
        draw.rounded_rectangle(
            [1, 1, size - 2, size - 2],
            radius=size // 5 - 1,
            fill=None,
            outline=border_color,
            width=max(1, size // 64),
        )

        # Play triangle
        play_color = (225, 29, 46, 255)
        left = int(size * 0.39)
        top = int(size * 0.28)
        right = int(size * 0.73)
        mid_y = size // 2
        bottom = int(size * 0.72)
        draw.polygon([(left, top), (right, mid_y), (left, bottom)], fill=play_color)

        images.append(img)

    # Save as .ico with all sizes
    images[0].save(
        str(ICO_PATH),
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:],
    )
    print(f"  Icon saved to {ICO_PATH}")
    return True


def run_pyinstaller():
    """Run PyInstaller with the spec file."""
    banner("Running PyInstaller")

    spec_file = ROOT / "playarr.spec"
    if not spec_file.is_file():
        print("  ERROR: playarr.spec not found")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(spec_file),
        "--noconfirm",
        "--clean",
    ]

    run(cmd, cwd=ROOT)


def validate_output():
    """Check the build output makes sense."""
    banner("Validating Build")

    exe = DIST_DIR / "Playarr.exe"
    fe_index = DIST_DIR / "_internal" / "frontend" / "dist" / "index.html"

    ok = True
    for path, label in [(exe, "Playarr.exe"), (fe_index, "frontend/dist/index.html")]:
        if path.is_file():
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"  OK  {label} ({size_mb:.1f} MB)")
        else:
            print(f"  MISSING  {label} — expected at {path}")
            ok = False

    # Total size
    if DIST_DIR.is_dir():
        total = sum(f.stat().st_size for f in DIST_DIR.rglob("*") if f.is_file())
        print(f"\n  Total bundle size: {total / (1024 * 1024):.0f} MB")

    return ok


def main():
    parser = argparse.ArgumentParser(description="Build Playarr standalone installer")
    parser.add_argument("--skip-frontend", action="store_true",
                        help="Skip frontend build (use existing dist/)")
    args = parser.parse_args()

    banner("Playarr Installer Build")
    print(f"  Root:     {ROOT}")
    print(f"  Python:   {sys.version}")
    print(f"  Platform: {sys.platform}")

    # Check PyInstaller
    try:
        import PyInstaller
        print(f"  PyInstaller: {PyInstaller.__version__}")
    except ImportError:
        print("  ERROR: PyInstaller not installed. Run: pip install pyinstaller")
        sys.exit(1)

    # Step 1: Frontend
    if args.skip_frontend:
        if not (FRONTEND_DIST / "index.html").is_file():
            print("  ERROR: --skip-frontend but frontend/dist/index.html doesn't exist")
            sys.exit(1)
        print("  Skipping frontend build (--skip-frontend)")
    else:
        build_frontend()

    # Step 2: Icon
    has_ico = generate_ico()

    # Step 3: PyInstaller
    run_pyinstaller()

    # Step 4: Validate
    success = validate_output()

    if success:
        banner("BUILD SUCCESSFUL")
        print(f"  Output: {DIST_DIR}")
        print(f"  Run:    {DIST_DIR / 'Playarr.exe'}")
        print()
        print("  To test the build:")
        print(f"    cd \"{DIST_DIR}\"")
        print(f"    .\\Playarr.exe")
        print()
    else:
        banner("BUILD COMPLETED WITH WARNINGS")
        print("  Some expected files are missing — check the output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
