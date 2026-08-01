"""Generate or verify the committed backend contract consumed by the UI."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
OUTPUT = ROOT / "frontend" / "openapi.json"


def rendered_contract() -> str:
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("PLAYARR_DEV", "1")
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    rendered = rendered_contract()
    if args.update:
        OUTPUT.write_text(rendered, encoding="utf-8")
        print(f"Updated {OUTPUT.relative_to(ROOT)}")
        return
    if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != rendered:
        raise SystemExit(
            "OpenAPI contract is stale. Run `python scripts/export_openapi.py --update`, "
            "then `npm run api:generate` in frontend and commit both outputs."
        )
    print("API-003 OpenAPI contract is current.")


if __name__ == "__main__":
    main()
