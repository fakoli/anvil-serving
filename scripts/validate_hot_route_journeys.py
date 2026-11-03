#!/usr/bin/env python3
"""Fail-closed validator for retained hot-route journey receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(config_path: Path) -> tuple[bool, str]:
    try:
        config = json.loads(config_path.read_text())
        journeys = config["journeys"]
        if not isinstance(journeys, list) or len(journeys) != 12:
            return False, "campaign must declare exactly twelve journeys"
        names: set[str] = set()
        for item in journeys:
            name, receipt, expected = item["name"], item["receipt"], item["sha256"]
            if not isinstance(name, str) or name in names:
                return False, "journey names must be unique strings"
            names.add(name)
            receipt_path = (config_path.parent / receipt).resolve()
            if not receipt_path.is_file() or digest(receipt_path) != expected:
                return False, f"invalid receipt binding: {name}"
            value = json.loads(receipt_path.read_text())
            if value.get("schema") != "hot-route-journey/v1" or value.get("journey") != name:
                return False, f"invalid receipt content: {name}"
            if value.get("status") not in {"passed", "pass"}:
                return False, f"journey did not pass: {name}"
        cleanup = config["cleanup"]
        cleanup_path = (config_path.parent / cleanup["receipt"]).resolve()
        if not cleanup_path.is_file() or digest(cleanup_path) != cleanup["sha256"]:
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
