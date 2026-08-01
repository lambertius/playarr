"""BASE-004: prevent further growth of known oversized source modules.

The existing budgets are ceilings, not targets. New feature work must extract
cohesive modules instead of increasing these files. Lowering a budget after an
extraction is encouraged; raising one requires an explicit architecture review.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "module_size_budget.json"
SOURCE_ROOTS = (ROOT / "backend" / "app", ROOT / "frontend" / "src")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx"}


def line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as source:
        return sum(1 for _ in source)


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    unlisted_limit = int(config["maximum_unlisted_lines"])
    budgets = {name: int(limit) for name, limit in config["budgets"].items()}
    failures: list[str] = []

    for relative_name, limit in budgets.items():
        path = ROOT / relative_name
        if not path.is_file():
            failures.append(f"budget references a missing module: {relative_name}")
            continue
        actual = line_count(path)
        if actual > limit:
            failures.append(f"{relative_name}: {actual} lines exceeds frozen budget {limit}")

    for source_root in SOURCE_ROOTS:
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            relative_name = path.relative_to(ROOT).as_posix()
            if "/generated/" in f"/{relative_name}":
                continue
            if relative_name in budgets:
                continue
            actual = line_count(path)
            if actual > unlisted_limit:
                failures.append(
                    f"{relative_name}: new/unlisted module has {actual} lines "
                    f"(limit {unlisted_limit})"
                )

    if failures:
        detail = "\n - ".join(failures)
        raise SystemExit(f"BASE-004 module-growth gate failed:\n - {detail}")
    print(f"BASE-004 module-growth gate passed ({len(budgets)} frozen modules).")


if __name__ == "__main__":
    main()
