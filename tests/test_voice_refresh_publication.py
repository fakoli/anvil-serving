"""Keep public voice headlines bound to the retained September 8 evidence."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FINDING = ROOT / "docs/findings/2026-09-08-m4-max-voice-refresh.md"
BUNDLE = FINDING.with_name(FINDING.stem + "-evidence")


def read(name):
    return json.loads((BUNDLE / name).read_text())
