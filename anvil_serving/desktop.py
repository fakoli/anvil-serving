"""Local Windows Docker Desktop power switch, with managed serve restoration."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
import csv
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from . import paths, serves

CONTEXT = "desktop-linux"
COOLDOWN_SECONDS = 10
PIPE = "npipe:////./pipe/dockerDesktopLinuxEngine"
INSPECT = (
    '{"id":{{json .Id}},"name":{{json .Name}},'
    '"status":{{json .State.Status}},'
    '"restart":{{json .HostConfig.RestartPolicy.Name}},'
    '"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}'
)


def _local_command(argv):
    if not argv or argv[0].lower() not in {"docker", "docker.exe", "tasklist.exe"}:
        raise ValueError("Desktop restoration requires local docker commands, without wrappers.")
    if any(token.startswith(("-H", "-c")) or any(token == flag or token.startswith(flag + "=")
           for flag in ("--host", "--context", "--config")) for token in argv[1:]):
        raise ValueError("Explicit Docker host/context/config selectors are forbidden for this switch.")


def _run(argv, **kwargs):
    # Pin every Docker call, including managed restoration, to this PC.
    _local_command(argv)
    env = dict(kwargs.pop("env", None) or os.environ)
    for key in tuple(env):
        if key.upper().startswith("DOCKER_"):
            del env[key]
    env["DOCKER_CONTEXT"] = CONTEXT
    kwargs.setdefault("timeout", 120)
    kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return subprocess.run(argv, env=env, **kwargs)


def _call(argv, *, run, timeout=20):
    result = run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Command failed").strip())
    return result.stdout.strip()


def _state(run):
    # Desktop's status command hangs when the app is closed. Absence of both
    # processes proves it is stopped; a live process plus a failed API is unknown.
    rows = csv.reader(io.StringIO(_call(["tasklist.exe", "/FO", "CSV", "/NH"], run=run)))
    active = any(row and row[0].lower() in {"docker desktop.exe", "com.docker.backend.exe"}
                 for row in rows)
    if not active:
        return "stopped"
    _call(["docker", "info", "--format", "{{.ID}}"], run=run)
    return "running"


def _containers(run):
    ids = _call(["docker", "ps", "-aq", "--no-trunc"], run=run).split()
    if not ids:
        return []
    lines = _call(["docker", "inspect", "--format", INSPECT, *ids], run=run)
    return [json.loads(line) for line in lines.splitlines()]


def _read_journal(path):
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("version") != 1
            or value.get("phase") not in {"stopping", "stopped", "starting"}
            or not isinstance(value.get("containers"), list)):
        raise ValueError("Invalid Desktop restore journal; preserve it for diagnosis.")
    for row in value["containers"]:
        if (not isinstance(row, dict)
                or not all(isinstance(row.get(k), str) and row[k] for k in ("id", "name"))
                or "serve" not in row
                or (row.get("serve") is not None and not isinstance(row["serve"], str))):
            raise ValueError("Invalid container in Desktop restore journal.")
    return value


def _snapshot(run, managed, prior=()):
    by_container = {s["container"]: s for s in managed}
    previously_running = {(r["name"], r["id"]) for r in prior}
    rows = []
    for row in _containers(run):
        if row["status"] not in {"running", "paused", "restarting"}:
            if row["restart"] == "always" and (row["name"], row["id"]) not in previously_running:
                raise RuntimeError("Parked container would auto-start on reboot: " + row["name"]
                                   + "; use unless-stopped in its managed configuration first.")
            continue
        if row["status"] != "running":
            raise RuntimeError("Resolve transitional/paused container before switching: " + row["name"])
        owner = by_container.get(row["name"].lstrip("/"))
        if owner and serves._is_compose_up(owner.get("up")):
            _local_command(owner["up"])
        if owner is None and row["restart"] not in {"always", "unless-stopped"}:
            raise RuntimeError(
                "Cannot restore unmanaged container " + row["name"]
                + "; declare it in a serve manifest or stop it before switching."
            )
        rows.append({"id": row["id"], "name": row["name"],
                     "serve": owner["name"] if owner else None})
    return {"version": 1, "phase": "stopping", "containers": rows}


def _restore(journal, managed, run, timeout, sleep, journal_path):
    observed = {r["name"]: r for r in _containers(run)}
    by_name = {s["name"]: s for s in managed}
    targets = []
    for saved in journal["containers"]:
        live = observed.get(saved["name"])
        if live is None or live["id"] != saved["id"]:
            raise RuntimeError("Saved container was removed/replaced: " + saved["name"])
        if saved["serve"] is not None:
            owner = by_name.get(saved["serve"])
            if owner is None or "/" + owner["container"] != saved["name"]:
                raise RuntimeError("Saved serve declaration changed: " + saved["name"])
            if serves._is_compose_up(owner.get("up")):
                _local_command(owner["up"])
            targets.append(owner)
    for owner in targets:
        live = observed["/" + owner["container"]]
        if live["status"] != "running":
            output = io.StringIO()
            with redirect_stdout(output):
                rc = serves.cmd_up(managed, [owner["name"]], _run=run,
                                   wait_for_readiness=True, readiness_timeout=timeout)
            # Compose may recreate during managed restoration. Retain that new
            # identity even if a later health check fails, so retries can resume.
            updated = next((r for r in _containers(run)
                            if r["name"] == "/" + owner["container"]), None)
            if updated:
                for saved in journal["containers"]:
                    if saved["serve"] == owner["name"]:
                        saved["id"] = updated["id"]
                serves._write_switch_journal(str(journal_path), journal)
            if rc:
                raise RuntimeError("Managed restore failed for " + owner["name"] + ": " + output.getvalue())
        elif not serves._await_healthy(owner, timeout, 2):
            raise RuntimeError("Serve readiness timed out: " + owner["name"])
    deadline = time.monotonic() + timeout
    while True:
        live = {r["name"]: r for r in _containers(run)}
        pending = [r["name"] for r in journal["containers"]
                   if r["name"] not in live or live[r["name"]]["status"] != "running"
                   or live[r["name"]]["health"] not in {None, "healthy"}]
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Containers not ready: " + ", ".join(pending))
        sleep(2)


def _enter_operation(locks, cooldown_path):
    """Share the nonblocking button guard across toggles and explicit modes."""
    try:
        locks.enter_context(serves._switch_role_lock("desktop"))
    except RuntimeError as exc:
        if str(exc) != "another switch is already active for role 'desktop'":
            raise
        return {"action": "ignored", "reason": "already-switching",
                "message": "Already switching. Extra press ignored."}
    if cooldown_path.exists():
        cooldown = json.loads(cooldown_path.read_text(encoding="utf-8"))
        completed = cooldown["completed_monotonic"]
        wall = cooldown.get("completed_wall", 0)
        if any(not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in (completed, wall)):
            raise ValueError("Invalid Desktop cooldown timestamp.")
        # Corroborate monotonic recency with wall time across reboots.
        elapsed = time.monotonic() - completed
        if 0 <= elapsed < COOLDOWN_SECONDS and abs(time.time() - wall) < COOLDOWN_SECONDS:
            remaining = math.ceil(COOLDOWN_SECONDS - elapsed)
            return {"action": "ignored", "reason": "cooldown", "retry_after_seconds": remaining,
                    "message": f"Switch just finished. Wait {remaining} seconds; extra press ignored."}
    locks.enter_context(serves._switch_role_lock("promotion"))
    return None


def switch(action, *, confirm=False, dry_run=False, timeout=180, run=_run, sleep=time.sleep):
    """Return an operation result; never equate an unreachable engine with stopped."""
    if sys.platform != "win32":
        raise ValueError("Desktop switching currently supports Windows only.")
    if action not in {"status", "start", "stop", "toggle"}:
        raise ValueError("Unknown Desktop action.")
    if not 1 <= timeout <= 1800:
        raise ValueError("timeout must be between 1 and 1800 seconds.")
    if action != "status" and not (confirm or dry_run):
        raise ValueError("Use --dry-run or --confirm.")
    journal_path = Path(paths.config_path("state", "desktop-switch.json"))
    cooldown_path = journal_path.with_name("desktop-switch-cooldown.json")
    with ExitStack() as locks:
        if not dry_run and action != "status":
            ignored = _enter_operation(locks, cooldown_path)
            if ignored:
                return ignored
        endpoint = _call(["docker", "context", "inspect", CONTEXT, "--format",
                          "{{.Endpoints.docker.Host}}"], run=run)
        if endpoint != PIPE:
            raise ValueError("Refusing a non-local Docker Desktop Linux context.")
        journal = _read_journal(journal_path)
        state = _state(run)
        if action == "status":
            return {"state": state, "pending": journal["phase"] if journal else None}
        if action == "toggle":
            action = ("stop" if journal["phase"] == "stopping" else "start") if journal else (
                "stop" if state == "running" else "start")
        managed = serves.load_manifest_set(paths.config_path("serves.toml"))
        startup = [s for s in managed if "desktop-start" in s.get("groups", [])] if action == "start" else []
        for owner in startup:
            if owner.get("runtime", "docker") != "docker":
                raise ValueError("desktop-start requires a local Docker serve: " + owner["name"])
            if serves._is_compose_up(owner.get("up")):
                raise ValueError("desktop-start requires an existing recipe-backed container, not Compose: "
                                 + owner["name"])
        if action == "stop" and state == "running":
            current = _snapshot(run, managed, journal["containers"] if journal else ())
            saved = {row["name"]: row for row in journal["containers"]} if journal else {}
            for row in current["containers"]:
                if row["name"] in saved and saved[row["name"]] != row:
                    raise RuntimeError("Container ownership changed since the previous stop: " + row["name"])
                saved[row["name"]] = row
            journal = {**current, "containers": list(saved.values())}
        result = {"action": action, "state": state, "dry_run": dry_run,
                  "restore": [r["name"] for r in journal["containers"]] if journal else [],
                  "startup": [s["name"] for s in startup]}
        if dry_run:
            return result
        if journal is None:
            journal = {"version": 1, "phase": "starting", "containers": []}
        journal["phase"] = "stopping" if action == "stop" else "starting"
        serves._write_switch_journal(str(journal_path), journal)
        if (action == "stop" and state == "running") or (action == "start" and state == "stopped"):
            _call(["docker", "desktop", action, "--timeout", str(timeout)],
                  run=run, timeout=timeout + 10)
        expected = "stopped" if action == "stop" else "running"
        if _state(run) != expected:
            raise RuntimeError("Docker Desktop did not reach " + expected + "; retry the same action.")
        if action == "stop":
            journal["phase"] = "stopped"
            serves._write_switch_journal(str(journal_path), journal)
        else:
            # Explicit AI-mode defaults also start when no model was running
            # before gaming. Existing containers only; never recreate a missing model.
            observed = {r["name"]: r for r in _containers(run)} if startup else {}
            saved = {r["name"]: r for r in journal["containers"]}
            for owner in startup:
                name = "/" + owner["container"]
                if name in saved:
                    if saved[name]["serve"] != owner["name"]:
                        raise RuntimeError("Configured startup ownership changed: " + name)
                    continue
                if name not in observed:
                    raise RuntimeError("Configured startup container is missing: " + name
                                       + "; load its managed recipe before using the switch.")
                journal["containers"].append({"id": observed[name]["id"], "name": name,
                                              "serve": owner["name"]})
            serves._write_switch_journal(str(journal_path), journal)
            _restore(journal, managed, run, timeout, sleep, journal_path)
        serves._write_switch_journal(str(cooldown_path), {
            "completed_monotonic": time.monotonic(), "completed_wall": time.time()})
        if action == "start":
            journal_path.unlink()
        return {**result, "state": expected}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "start", "stop", "toggle"))
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    try:
        result = switch(args.action, confirm=args.confirm, dry_run=args.dry_run, timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("Desktop switch failed: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
