#!/usr/bin/env python3
"""Fail-closed validator for retained hot-route journey receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from pathlib import Path

EXPECTED_JOURNEYS = frozenset({
    "alias_remap", "endpoint_relocation", "managed_promotion", "context_boundary",
    "output_enforcement", "prepared_cancel", "router_crash_recovery",
    "transport_loss_recovery", "failed_promotion_recovery", "route_cli",
    "pi_across_swap", "openclaw",
})

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded_json(path: Path) -> dict:
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 2 * 1024 * 1024:
        raise ValueError("receipt must be a bounded regular JSON file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("receipt must be an object")
    return value


def validate(config_path: Path) -> tuple[bool, str]:
    try:
        config = bounded_json(config_path)
        if config.get("schema") != "hot-routes-e2e-campaign/v1":
            return False, "campaign schema is invalid"
        journeys = config["journeys"]
        if not isinstance(journeys, list) or len(journeys) != len(EXPECTED_JOURNEYS):
            return False, "campaign must declare the exact twelve journeys"
        names: set[str] = set()
        for item in journeys:
            name, receipt, expected = item["name"], item["receipt"], item["sha256"]
            if not isinstance(name, str) or name not in EXPECTED_JOURNEYS or name in names:
                return False, "journey names must be the exact declared set"
            if not isinstance(receipt, str) or Path(receipt).name != receipt or not re.fullmatch(r"[0-9a-f]{64}", expected):
                return False, f"invalid receipt declaration: {name}"
            names.add(name)
            receipt_path = (config_path.parent / receipt).resolve()
            if not receipt_path.is_file() or digest(receipt_path) != expected:
                return False, f"invalid receipt binding: {name}"
            value = bounded_json(receipt_path)
            if value.get("schema") != "hot-route-journey/v1" or value.get("journey") != name:
                return False, f"invalid receipt content: {name}"
            if value.get("result") != "passed" or value.get("cleanup_passed") is not True:
                return False, f"journey did not pass: {name}"
        if names != EXPECTED_JOURNEYS:
            return False, "campaign journey set is incomplete"
        cleanup = config["cleanup"]
        if not isinstance(cleanup, dict) or cleanup.get("schema") != "hot-route-cleanup/v1" or cleanup.get("result") != "passed" or cleanup.get("assertions") is not True or not re.fullmatch(r"[0-9a-f]{64}", cleanup.get("sha256", "")):
            return False, "cleanup contract is invalid"
        cleanup_path = (config_path.parent / cleanup["receipt"]).resolve()
        if Path(cleanup["receipt"]).name != cleanup["receipt"] or not cleanup_path.is_file() or digest(cleanup_path) != cleanup["sha256"]:
            return False, "invalid cleanup receipt binding"
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
        return False, f"invalid campaign: {error}"
    return True, "12 journeys and cleanup receipts verified"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    ok, message = validate(args.config)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
