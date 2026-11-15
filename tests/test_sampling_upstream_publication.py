"""Recompute the prospective sampling trial's claims from retained receipts."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FINDING = ROOT / "docs/findings/2026-10-25-sampling-upstream-tolerance.md"
BUNDLE = FINDING.with_name(FINDING.stem + "-evidence")


def read(name):
    return json.loads((BUNDLE / name).read_text())
