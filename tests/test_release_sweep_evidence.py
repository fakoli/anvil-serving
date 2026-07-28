"""Historical release-sweep evidence must remain internally consistent."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWEEP = (
    ROOT
    / "docs"
    / "findings"
    / "2026-07-27-release-readiness-evidence"
    / "cli-surface-sweep.json"
)
EXPECTED_IDENTITY = {
    "repository_revision": "58f7809588a603f2c7b41a7684c40fc02f77315c",
    "command_manifest_sha256": "5b4e7bf06989344a1d4b1cd53ea621ce5f10b5b098fd0fe4cf362bcecf8bfe72",
    "manifest_command_paths": 137,
    "help_parse_passed": 137,
    "help_parse_failed": 0,
    "handler_resolution_passed": 109,
    "group_nodes_validated": 28,
    "operation_classes": {"read": 80, "mutate": 51, "process": 6},
}


def test_release_sweep_is_a_self_consistent_historical_record():
    sweep = json.loads(SWEEP.read_text(encoding="utf-8"))

    assert {key: sweep[key] for key in EXPECTED_IDENTITY} == EXPECTED_IDENTITY
    assert sweep["help_parse_passed"] == sweep["manifest_command_paths"]
    assert sweep["help_parse_failed"] == 0
    assert (
        sweep["handler_resolution_passed"] + sweep["group_nodes_validated"]
        == sweep["manifest_command_paths"]
    )
    assert sum(sweep["operation_classes"].values()) == sweep["manifest_command_paths"]
    assert set(sweep["operation_classes"]) == {"read", "mutate", "process"}
