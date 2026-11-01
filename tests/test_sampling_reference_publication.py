"""Bind sampling-reference publication claims to retained native receipts."""

from collections import defaultdict
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FINDING = ROOT / "docs/findings/2026-10-25-sampling-reference.md"
BUNDLE = FINDING.with_name(FINDING.stem + "-evidence")


def read(name):
    return json.loads((BUNDLE / name).read_text())


def receipts():
    grouped = defaultdict(list)
    for line in (BUNDLE / "kernel-receipts.log").read_text().splitlines():
        kind, payload = line.split(" ", 1)
        assert kind.startswith("ANVIL_SAMPLING_")
        grouped[kind.removeprefix("ANVIL_SAMPLING_")].append(json.loads(payload))
    return grouped
