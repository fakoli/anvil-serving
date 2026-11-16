"""Recompute the public Ignis 0.8 tool-choice qualification claims."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FINDING = ROOT / "docs/findings/2026-10-26-ignis-080-preflight.md"
BUNDLE = FINDING.with_name(FINDING.stem + "-evidence")


def read_json(name: str) -> dict:
    return json.loads((BUNDLE / name).read_text())
