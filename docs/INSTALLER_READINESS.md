# Playarr — Installer Readiness

This document tracks the current state of packaging readiness and what remains before building a final Windows installer.

---

## Current State (Installer-Ready)

### Architecture
- [x] **Single-port production mode** — FastAPI serves built frontend + API on one port (6969)
- [x] **Single entry point** — `run_playarr.py` / `start_playarr.bat` launch everything
- [x] **In-process background tasks** — no Redis/Celery required for desktop use
- [x] **SPA routing** — deep links work (middleware falls back to index.html)
- [x] **Startup validation** — checks Python, ffmpeg, ffprobe, yt-dlp, port conflicts
- [x] **First-run setup** — auto-creates directories, initializes DB, runs migrations
- [x] **Platform-appropriate paths** — AppData on Windows, XDG on Linux

### Runtime Directories
- [x] Config: `%APPDATA%\Playarr\config\`
- [x] Database: `%APPDATA%\Playarr\data\playarr.db`
- [x] Logs: `%APPDATA%\Playarr\logs\`
- [x] Cache: `%LOCALAPPDATA%\Playarr\cache\`
- [x] Library: User-configurable (default: `~/Music/Playarr/`)
- [x] Archive: User-configurable (default: `~/Music/Playarr/archive/`)

### Entry Points
- [x] `run_playarr.py` — production launcher with validation
- [x] `start_playarr.bat` — Windows batch launcher
- [x] `build_playarr.bat` — one-time build script
- [x] `start_dev.bat` — development mode launcher
- [x] System tray icon with Open/Quit

### Configuration
- [x] No `.env` file required for normal use
- [x] All settings have sensible defaults
- [x] User settings stored in SQLite DB via Settings UI
- [x] Environment variables override defaults when needed
- [x] Config file fallback (`%APPDATA%\Playarr\config\playarr.conf`)

---

## What Remains Before Final Installer

### Must Have

1. **Bundled Python runtime**
   - Include an embedded Python 3.12 distribution in the installer
   - Use `python-3.12.x-embed-amd64.zip` from python.org
   - Pre-install all pip dependencies into a `Lib/site-packages/` folder
   - Launcher references `python/python.exe` (already handled in `start_playarr.bat`)

2. **Bundled ffmpeg + ffprobe**
   - Include ffmpeg/ffprobe binaries in the installer
   - Add install dir to PATH or set `FFMPEG_PATH`/`FFPROBE_PATH` explicitly
   - Estimated size: ~80 MB compressed

3. **Bundled yt-dlp**
   - Include `yt-dlp.exe` standalone binary
   - Or install via pip into the bundled Python runtime
   - Add auto-update logic or rely on pip-based updates

4. **Pre-built frontend**
   - Run `npm run build` during installer build process
   - Include `frontend/dist/` in the installer package
   - No Node.js needed at install time

5. **Installer script (Inno Setup recommended)**
   - Package all of the above into a single `.exe` installer
   - Create Start Menu shortcut → `start_playarr.bat`
   - Create Desktop shortcut (optional)
   - Register uninstaller
   - Set file associations if desired (`.nfo`, `.strm`)

### Nice to Have

6. **Single-instance enforcement**
   - Named mutex to prevent multiple Playarr instances
   - If already running, bring existing window to focus
   - Can be implemented with `win32api` or a PID file

7. **Windows service option**
   - Optional: run as a Windows service instead of tray app
   - Use `nssm` or `pywin32` service wrapper
   - Suitable for headless / always-on server mode

8. **Auto-updater**
   - Check GitHub releases API for new versions
   - Download + apply update (or alert user)
   - Consider Squirrel.Windows for managed updates

9. **Custom icon**
   - Convert Playarr icon to `.ico` format for installer/shortcuts
   - Already generated in `tray.py` as PIL image

10. **EULA / license display**
    - Show MIT license during install

---

## Recommended Installer Stack

**Inno Setup** is recommended for the Windows installer:
- Free, open-source, well-documented
- Single-file `.exe` output
- Pascal scripting for custom logic
- Supports Start Menu, Desktop shortcuts, uninstaller
- Can run post-install steps (e.g., create initial config)

### Installer Build Process

```
1. npm install + npm run build          → frontend/dist/
2. pip install to embedded Python       → python/Lib/site-packages/
3. Download ffmpeg + yt-dlp binaries    → tools/
4. Compile Inno Setup script            → PlayarrSetup.exe
```

### Estimated Installer Size

| Component | Size (approx) |
|-----------|--------------|
| Python 3.12 embedded | ~15 MB |
| pip dependencies | ~80 MB |
| Frontend dist | ~5 MB |
| ffmpeg + ffprobe | ~80 MB |
| yt-dlp | ~10 MB |
| Backend source | ~5 MB |
| **Total (compressed)** | **~100-120 MB** |

---

## Settings Classification

### Install-Time Defaults (set by installer, rarely changed)

| Setting | Default | Notes |
|---------|---------|-------|
| Install directory | `C:\Program Files\Playarr` | Standard Windows install |
| Data directory | `%APPDATA%\Playarr` | Auto-detected |
| Port | 6969 | Changeable via Settings UI |

### User-Editable Runtime Settings (changed in Settings UI)

| Setting | Default | Notes |
|---------|---------|-------|
| Library directory | `~/Music/Playarr` | First-run wizard or Settings |
| Archive directory | `~/Music/Playarr/archive` | Settings > Library |
| AI provider/keys | none | Settings > AI |
| Normalization target | -14.0 LUFS | Settings > Audio |
| Auto-open browser | true | Settings > System |
| Minimize to tray | true | Settings > System |
| Library naming pattern | `{artist} - {title} [{quality}]` | Settings > Library |

---

## Dependency Matrix

| Dependency | Installer Strategy | Notes |
|-----------|-------------------|-------|
| Python 3.12 | Bundled (embedded) | No system Python required |
| pip packages | Pre-installed | Into bundled Python's site-packages |
| Node.js | NOT bundled | Only needed for building; dist is pre-built |
| ffmpeg | Bundled binary | ~80 MB, essential |
| ffprobe | Bundled with ffmpeg | Same download |
| yt-dlp | Bundled binary | ~10 MB, or pip-installed |
| Redis | NOT bundled | Not needed for desktop mode |
| Celery | pip-installed but optional | Only activates if Redis present |
| SQLite | Included with Python | No separate install |
