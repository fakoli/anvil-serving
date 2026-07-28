"""Release-sweep evidence must stay bound to the complete public CLI."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "CLI-COMMAND-MANIFEST.json"
SWEEP = (
    ROOT
    / "docs"
    / "findings"
    / "2026-07-27-release-readiness-evidence"
    / "cli-surface-sweep.json"
)


def test_release_sweep_covers_exact_checked_in_command_manifest():
    raw_manifest = MANIFEST.read_bytes()
    manifest = json.loads(raw_manifest)
    sweep = json.loads(SWEEP.read_text(encoding="utf-8"))
    commands = manifest["commands"]

    assert sweep["command_manifest_sha256"] == hashlib.sha256(raw_manifest).hexdigest()
    assert sweep["manifest_command_paths"] == len(commands)
    assert sweep["help_parse_passed"] == len(commands)
    assert sweep["help_parse_failed"] == 0
    assert sweep["handler_resolution_passed"] == sum(
        bool(command["handler"]) for command in commands
    )
    assert sweep["group_nodes_validated"] == sum(
        not command["handler"] for command in commands
    )
    assert sweep["operation_classes"] == dict(
        Counter(command["mutation_class"] for command in commands)
    )
