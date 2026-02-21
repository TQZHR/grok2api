#!/usr/bin/env python3
"""
Lightweight guard for model catalog consistency across runtimes.

Checks:
1) app/services/grok/model.py and src/grok/models.ts expose the exact same model ids.
2) src/**/*.ts must not contain removed legacy model identifiers.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PY_MODEL_FILE = ROOT / "app" / "services" / "grok" / "model.py"
TS_MODEL_FILE = ROOT / "src" / "grok" / "models.ts"
TS_SRC_DIR = ROOT / "src"

PY_MODEL_ID_RE = re.compile(r'model_id\s*=\s*"([^"]+)"')
TS_MODEL_ID_RE = re.compile(r'^\s*"(?P<id>grok-[^"]+)"\s*:\s*{', re.MULTILINE)

REMOVED_IDENTIFIERS = (
    "grok-3-fast",
    "grok-4-fast",
    "grok-4-mini-thinking-tahoe",
    "grok-4.1",
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _collect_python_model_ids(text: str) -> set[str]:
    return {v for v in PY_MODEL_ID_RE.findall(text) if v.startswith("grok-")}


def _collect_workers_model_ids(text: str) -> set[str]:
    return {m.group("id") for m in TS_MODEL_ID_RE.finditer(text)}


def _build_token_pattern(token: str) -> re.Pattern[str]:
    # Exact token match (avoid matching prefixes like `grok-4.1-mini` for `grok-4.1`).
    escaped = re.escape(token)
    return re.compile(rf"(?<![A-Za-z0-9_.-]){escaped}(?![A-Za-z0-9_.-])")


def _scan_removed_identifiers() -> list[tuple[str, int, str, str]]:
    patterns = [(token, _build_token_pattern(token)) for token in REMOVED_IDENTIFIERS]
    findings: list[tuple[str, int, str, str]] = []

    for ts_file in sorted(TS_SRC_DIR.rglob("*.ts")):
        text = _read_text(ts_file)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for token, pat in patterns:
                if pat.search(line):
                    findings.append(
                        (
                            ts_file.relative_to(ROOT).as_posix(),
                            line_no,
                            token,
                            line.strip(),
                        ),
                    )
    return findings


def main() -> int:
    errors: list[str] = []

    py_ids = _collect_python_model_ids(_read_text(PY_MODEL_FILE))
    ts_ids = _collect_workers_model_ids(_read_text(TS_MODEL_FILE))

    if py_ids != ts_ids:
        only_python = sorted(py_ids - ts_ids)
        only_workers = sorted(ts_ids - py_ids)
        lines = ["Model catalog mismatch between python and workers."]
        if only_python:
            lines.append("Only in app/services/grok/model.py:")
            lines.extend(f"  - {mid}" for mid in only_python)
        if only_workers:
            lines.append("Only in src/grok/models.ts:")
            lines.extend(f"  - {mid}" for mid in only_workers)
        errors.append("\n".join(lines))

    removed_hits = _scan_removed_identifiers()
    if removed_hits:
        lines = ["Removed model identifiers found under src/**/*.ts:"]
        for rel_path, line_no, token, snippet in removed_hits:
            lines.append(f"  - {rel_path}:{line_no} -> {token}")
            lines.append(f"    {snippet}")
        errors.append("\n".join(lines))

    if errors:
        print("[check_model_catalog_sync] FAILED", file=sys.stderr)
        for idx, msg in enumerate(errors, start=1):
            print(f"\n[{idx}] {msg}", file=sys.stderr)
        return 1

    print("[check_model_catalog_sync] OK: model catalogs are synchronized and legacy identifiers are absent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
