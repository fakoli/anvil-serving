#!/usr/bin/env python3
"""Verify retained receipt integrity; this does not execute or grade journeys."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path

EXPECTED_JOURNEYS = frozenset({
    "alias_remap", "endpoint_relocation", "managed_promotion", "context_boundary",
    "output_enforcement", "prepared_cancel", "router_crash_recovery",
    "transport_loss_recovery", "failed_promotion_recovery", "route_cli",
    "pi_across_swap", "openclaw",
})
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
CLEANUP_ASSERTIONS = frozenset({
    "test_router_stopped", "crash_router_stopped", "test_models_stopped",
    "production_router_unchanged", "production_models_unchanged",
    "temporary_client_settings_restored",
})

def bounded_json(path: Path, expected_sha256: str | None = None) -> dict:
    # Inspect and read the same open file; hash exactly the bytes being parsed.
    if path.is_symlink():
        raise ValueError("receipt symlinks are not allowed")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_RECEIPT_BYTES:
            raise ValueError("receipt must be a bounded regular JSON file")
        raw = source.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt exceeds byte limit")
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("receipt hash mismatch")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("receipt must be an object")
    return value


def bound_receipt(root: Path, declaration: dict) -> dict:
    if not isinstance(declaration, dict):
        raise ValueError("receipt declaration must be an object")
    name, expected = declaration["receipt"], declaration["sha256"]
    if (not isinstance(name, str) or not name or "\\" in name
            or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise ValueError("invalid receipt declaration")
    relative = Path(name)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError("receipt path must be normalized and relative")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("receipt path contains a symlink")
    path.resolve(strict=True).relative_to(root)
    return bounded_json(path, expected)


def validate(config_path: Path) -> tuple[bool, str]:
    try:
        config = bounded_json(config_path)
        if config.get("schema") != "hot-routes-e2e-campaign/v1":
            return False, "campaign schema is invalid"
        root = config_path.parent.resolve(strict=True)
        journeys = config["journeys"]
        if not isinstance(journeys, list) or len(journeys) != len(EXPECTED_JOURNEYS):
            return False, "campaign must declare the exact twelve journeys"
        names: set[str] = set()
        for item in journeys:
            name = item["name"]
            if not isinstance(name, str) or name not in EXPECTED_JOURNEYS or name in names:
                return False, "journey names must be the exact declared set"
            names.add(name)
            value = bound_receipt(root, item)
            if value.get("schema") != "hot-route-journey/v1" or value.get("journey") != name:
                return False, f"invalid receipt content: {name}"
            if value.get("result") != "passed" or value.get("cleanup_passed") is not True:
                return False, f"journey did not pass: {name}"
        if names != EXPECTED_JOURNEYS:
            return False, "campaign journey set is incomplete"
        cleanup = bound_receipt(root, config["cleanup"])
        assertions = cleanup.get("assertions")
        if (cleanup.get("schema") != "hot-route-cleanup/v1" or cleanup.get("result") != "passed"
                or not isinstance(assertions, dict) or set(assertions) != CLEANUP_ASSERTIONS
                or any(value is not True for value in assertions.values())):
            return False, "cleanup contract is invalid"
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
        return False, f"invalid campaign: {error}"
    return True, "Receipt integrity verified for 12 journeys and cleanup; live semantics require independent review"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    ok, message = validate(args.config)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
