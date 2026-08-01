"""BASE-001: reproduce the backend verification gate with one command.

By default this script creates/reuses ``.verify-venv``, installs the fully
resolved hash-locked dependency set, then re-enters itself inside that
environment.  ``--use-current`` is available for CI or a developer who has
already installed ``backend/requirements.lock.txt``.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
LOCKFILE = BACKEND / "requirements.lock.txt"
VERIFY_VENV = ROOT / ".verify-venv"


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def venv_python() -> Path:
    return VERIFY_VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_locked_environment() -> Path:
    python = venv_python()
    if not python.exists():
        print(f"Creating verification environment at {VERIFY_VENV}", flush=True)
        venv.EnvBuilder(with_pip=True).create(VERIFY_VENV)
    run([
        str(python), "-m", "pip", "install", "--disable-pip-version-check",
        "--require-hashes", "-r", str(LOCKFILE),
    ])
    return python


def verify(python: Path) -> None:
    run([str(python), str(ROOT / "scripts" / "check_module_growth.py")])
    run([str(python), str(ROOT / "scripts" / "export_openapi.py")])
    with tempfile.TemporaryDirectory(prefix="playarr-verify-") as temp_dir:
        db_path = Path(temp_dir) / "migration.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
        env["PLAYARR_DEV"] = "1"

        run([str(python), "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], cwd=BACKEND, env=env)
        schema_check = (
            "import sqlite3; "
            "from app.database import Base; "
            "import app.models, app.new_videos.models; "
            f"c=sqlite3.connect(r'{db_path}'); "
            "actual={r[0] for r in c.execute(\"select name from sqlite_master where type='table'\")}; "
            "expected=set(Base.metadata.tables); missing=expected-actual; "
            "assert not missing, f'missing migrated tables: {sorted(missing)}'; "
            "assert c.execute('pragma integrity_check').fetchone()[0]=='ok'; c.close()"
        )
        run([str(python), "-c", schema_check], cwd=BACKEND, env=env)
        run([str(python), "-m", "pytest", "-q"], cwd=BACKEND, env=env)
        run([str(python), "-m", "compileall", "-q", "app", "tests"], cwd=BACKEND, env=env)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--use-current", action="store_true",
        help="Use the current Python environment instead of creating .verify-venv",
    )
    args = parser.parse_args()
    if not LOCKFILE.is_file():
        raise SystemExit(f"Missing lockfile: {LOCKFILE}")
    python = Path(sys.executable) if args.use_current else ensure_locked_environment()
    verify(python)


if __name__ == "__main__":
    main()
