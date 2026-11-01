"""Reconcile the bounded RTX 5090 decision with retained native evidence."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs/findings/2026-09-12-qwen38-efficient-variants-rtx5090-evidence"


def read(name):
    return json.loads((BUNDLE / name).read_text(encoding="utf-8"))
