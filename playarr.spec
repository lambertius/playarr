# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Playarr — Music Video Manager.

Usage:
    pyinstaller playarr.spec

Prerequisites:
    - Frontend already built (frontend/dist/ must exist)
    - All pip dependencies installed
    - pip install pyinstaller
"""
import os
import sys
from pathlib import Path

block_cipher = None

ROOT = Path(SPECPATH)
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"

# Validate frontend build exists
if not (FRONTEND_DIST / "index.html").is_file():
    raise FileNotFoundError(
        "Frontend dist not found — run 'npm run build' in frontend/ first.\n"
        f"Expected: {FRONTEND_DIST / 'index.html'}"
    )

# ---------------------------------------------------------------------------
# Data files to bundle
# ---------------------------------------------------------------------------
datas = [
    # Frontend SPA (served by FastAPI)
    (str(FRONTEND_DIST), "frontend/dist"),
    # Alembic migrations (for future schema upgrades)
    (str(BACKEND / "alembic"), "alembic"),
    (str(BACKEND / "alembic.ini"), "."),
    # .env.example as reference
    (str(BACKEND / ".env.example"), "."),
]

# ---------------------------------------------------------------------------
# Hidden imports — packages PyInstaller can miss
# ---------------------------------------------------------------------------
hiddenimports = [
    # FastAPI / Uvicorn
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # SQLAlchemy
    "sqlalchemy.dialects.sqlite",
    # Celery + Redis (optional at runtime, but must be importable)
    "celery",
    "celery.app",
    "celery.app.task",
    "celery.backends",
    "celery.backends.redis",
    "celery.fixups",
    "celery.fixups.django",
    "celery.loaders",
    "celery.loaders.app",
    "celery.loaders.default",
    "celery.concurrency",
    "celery.concurrency.prefork",
    "celery.concurrency.thread",
    "celery.app.amqp",
    "celery.app.control",
    "celery.app.events",
    "celery.app.log",
    "celery.worker",
    "celery.worker.consumer",
    "celery.events",
    "celery.events.state",
    "celery.utils.dispatch",
    "kombu",
    "kombu.transport",
    "kombu.transport.redis",
    "kombu.transport.memory",
    "redis",
    # Scrapers / metadata
    "bs4",
    "musicbrainzngs",
    "fake_useragent",
    # Media processing
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    # Tray icon
    "pystray",
    "pystray._win32",
    # Other deps
    "natsort",
    "httpx",
    "aiofiles",
    "multipart",
    "python_multipart",
    "dotenv",
    "send2trash",
    # App modules — ensure all routers are collected
    "app",
    "app.main",
    "app.config",
    "app.database",
    "app.models",
    "app.schemas",
    "app.tasks",
    "app.worker",
    "app.version",
    "app.runtime_dirs",
    "app.safe_delete",
    "app.routers.library",
    "app.routers.jobs",
    "app.routers.playback",
    "app.routers.settings",
    "app.routers.metadata",
    "app.routers.resolve",
    "app.routers.ai",
    "app.routers.artwork",
    "app.routers.library_import",
    "app.routers.playlists",
    "app.routers.video_editor",
    "app.routers.scraper_test",
    "app.routers.tmvdb",
    "app.new_videos",
    "app.new_videos.router",
    "app.new_videos.service",
    "app.ai",
    "app.matching",
    "app.metadata",
    "app.scraper",
    "app.services",
    "app.pipeline",
    "app.pipeline_lib",
    "app.pipeline_url",
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(ROOT / "run_playarr.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",       # Not needed at runtime (directory picker fallback only)
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "pytest",
        "IPython",
        "jupyter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Playarr",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon will be set by build script if .ico exists
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Playarr",
)
