"""Inspect and prune only exact BuildKit cache IDs with recorded old last use."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import re
import subprocess

from .docker_images import DockerImageCleanupError, _docker_run
from .models import _docker_size_bytes


def _timestamp(value):
    text = str(value or "")
    match = re.fullmatch(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?) ([+-]\d{4}) [A-Z]+", text)
    if match:
        text = match[1] + match[2]
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return value if value.tzinfo and value.year > 1 else None
    except ValueError:
        return None


def _run(context, args, runner, timeout=30):
    result = _docker_run(["--context", context, *args], runner=runner, timeout=timeout)
    if result is None:
        raise DockerImageCleanupError("Docker command returned no result")
    if result.returncode:
        raise DockerImageCleanupError((result.stderr or "Docker command failed").strip())
    return result.stdout


def inventory(context, builder, before, *, runner=subprocess.run):
    """Keep records without a trustworthy last-use timestamp or explicit idle state."""
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name)
           for name in (context, builder)):
        raise DockerImageCleanupError("context and builder must be explicit names")
    cutoff = _timestamp(before)
    if cutoff is None or cutoff > datetime.now(timezone.utc) - timedelta(days=14):
        raise DockerImageCleanupError("before must be timezone-qualified and at least 14 days old")
    try:
        builders = [json.loads(line) for line in _run(
            context, ["buildx", "ls", "--format", "{{json .}}"], runner
        ).splitlines() if line.strip()]
        matching = [row for row in builders if row.get("Name") == builder]
        # Some Buildx versions repeat identical context builders in `ls`.
        if len({json.dumps(row, sort_keys=True) for row in matching}) > 1:
            raise DockerImageCleanupError("Docker returned conflicting builder identities")
        if not matching or any(
            row.get("Driver") != "docker" or len(row.get("Nodes") or []) != 1
            or row["Nodes"][0].get("Endpoint") != context
            or row["Nodes"][0].get("Status") != "running" for row in matching
        ):
            raise DockerImageCleanupError("requires a running single-node docker builder on the named context")
        engine = json.loads(_run(context, ["info", "--format", "{{json .ID}}"], runner))
        if not isinstance(engine, str) or not engine:
            raise DockerImageCleanupError("Docker engine identity is unavailable")
        payload = json.loads(_run(context, ["system", "df", "-v", "--format", "{{json .}}"], runner))
        rows = payload["BuildCache"]
        if not isinstance(rows, list):
            raise DockerImageCleanupError("Docker build-cache inventory is incomplete")
        candidates, retained, seen = [], [], set()
        for row in rows:
            cache_id = row.get("ID")
            if not isinstance(cache_id, str) or not re.fullmatch(r"[a-z0-9]{25}", cache_id) or cache_id in seen:
                raise DockerImageCleanupError("Docker returned an invalid or duplicate full cache ID")
            seen.add(cache_id)
            used = _timestamp(row.get("LastUsedAt"))
            idle = row.get("InUse") is False or row.get("InUse") == "false"
            item = {"id": cache_id, "last_used_at": used.isoformat() if used else None,
                    "in_use": row.get("InUse"), "type": row.get("CacheType"),
                    "reported_size_bytes": _docker_size_bytes(row.get("Size"))}
            if idle and used and used < cutoff and row.get("CacheType") == "regular":
                candidates.append(item)
            else:
                retained.append(item)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DockerImageCleanupError("invalid Docker build-cache inventory: %s" % exc) from exc
    return {"schema": "docker-build-cache/v1", "context": context, "builder": builder,
            "engine_id": engine, "before": cutoff.isoformat(),
            "candidates": sorted(candidates, key=lambda row: row["id"]),
            "retained": sorted(retained, key=lambda row: row["id"]),
            "reported_candidate_bytes": sum(row["reported_size_bytes"] or 0 for row in candidates),
            "size_caveat": "Rounded cache sizes may share image layers; they are not reclaimed bytes."}


def prune(context, builder, before, *, confirm=False, dry_run=False, runner=subprocess.run):
    first = inventory(context, builder, before, runner=runner)
    result = {"outcome": "preview", "removal_attempted": False, "inspection": first}
    if dry_run or not confirm or not first["candidates"]:
        return result
    second = inventory(context, builder, before, runner=runner)
    if first != second:
        result["outcome"] = "inventory-drift"
        return result
    ids = [row["id"] for row in first["candidates"]]
    # BuildKit prune checks zero live refs under record locks; it also excludes
    # image-shared records without --all. Its `inuse` filter is a presence field,
    # not a string boolean, so `inuse=false` would incorrectly match nothing.
    # The server-side age filter covers reuse after the second inspection.
    seconds = math.ceil((datetime.now(timezone.utc) - _timestamp(before)).total_seconds())
    args = ["buildx", "prune", "--builder", builder, "--force", "--verbose",
            "--filter", "id~=^(" + "|".join(ids) + ")$",
            "--filter", "type=regular",
            "--filter", "until=%ds" % seconds]
    result["removal_attempted"] = True
    try:
        output = _run(context, args, runner, timeout=300)
        after = inventory(context, builder, before, runner=runner)
        if after["engine_id"] != first["engine_id"]:
            raise DockerImageCleanupError("Docker engine changed during prune")
        remaining = {row["id"] for row in after["candidates"] + after["retained"]}
        result.update(outcome="completed", removed_ids=sorted(set(ids) - remaining),
                      retained_candidate_ids=sorted(set(ids) & remaining),
                      daemon_report=output.strip(),
                      reclaimed_bytes=None,
                      reclaim_caveat="Daemon totals are human-rounded; physical VHDX shrink was not requested.")
    except DockerImageCleanupError as exc:
        result.update(outcome="failed", error=str(exc))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "prune"))
    parser.add_argument("--context", required=True, help="Exact Docker context name")
    parser.add_argument("--builder", required=True, help="Single-node docker-driver builder on that context")
    parser.add_argument("--before", required=True, help="Timezone-qualified last-use cutoff, at least 14 days old")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "inventory":
            result = inventory(args.context, args.builder, args.before)
        else:
            result = prune(args.context, args.builder, args.before,
                           confirm=args.confirm, dry_run=args.dry_run)
    except DockerImageCleanupError as exc:
        result = {"outcome": "failed", "error": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result.get("outcome") in {"failed", "inventory-drift"} else 0
