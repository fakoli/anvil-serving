"""Select explicit local desktop modes using private, manifest-owned assignments."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

from . import desktop, paths, serves


def _profile():
    path = Path(paths.config_path("desktop-modes.toml"))
    if path.stat().st_size > 65536:
        raise ValueError("Desktop mode configuration is too large.")
    value = tomllib.loads(path.read_text(encoding="utf-8"))
    required = {"schema", "secondary_manifest", "media_manifest", "model_manifests",
                "worker_containers", "guard_cli", "guard_config"}
    if not required <= set(value) <= required | {"idle_teardown"} or value["schema"] != "anvil-serving.desktop-modes/v1":
        raise ValueError("Invalid Desktop mode configuration.")
    for key in ("model_manifests", "worker_containers"):
        if (not isinstance(value[key], list) or len(value[key]) > 16
                or not all(isinstance(item, str) and item for item in value[key])):
            raise ValueError("Invalid Desktop mode " + key)
    if not value["worker_containers"] or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", item)
            for item in value["worker_containers"]):
        raise ValueError("Invalid media worker container name.")

    def resolve(item):
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ValueError("Invalid Desktop mode file path.")
        target = (path.parent / item).resolve()
        if not target.is_file():
            raise ValueError("Desktop mode file is missing: " + str(target))
        return str(target)

    for key in ("secondary_manifest", "media_manifest", "guard_cli", "guard_config"):
        value[key] = resolve(value[key])
    value["secondary"] = serves.load_manifest(value["secondary_manifest"])
    value["media"] = serves.load_manifest(value["media_manifest"])
    value["models"] = [s for item in value["model_manifests"] for s in serves.load_manifest(resolve(item))]
    if len(value["secondary"]) != 1 or not value["media"] or not any(s.get("up") for s in value["media"]):
        raise ValueError("Declare one secondary serve and an activatable media control plane.")
    model_names = {s["container"] for s in value["models"] + value["secondary"]}
    control_names = {s["container"] for s in value["media"]}
    if model_names & control_names or set(value["worker_containers"]) & (model_names | control_names):
        raise ValueError("Worker, model and media control assignments must not overlap.")
    binding = value.get("idle_teardown")
    if binding is not None:
        if (not isinstance(binding, dict) or set(binding) != {"worker", "controller", "service", "manifest", "principal"}
                or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", v) for v in binding.values())
                or binding["worker"] not in value["worker_containers"]
                or not any(s["container"] == binding["controller"] and not s.get("up") for s in value["media"])):
            raise ValueError("Invalid idle worker lifecycle binding.")
    for owner in value["secondary"] + value["media"] + value["models"]:
        if owner.get("runtime", "docker") != "docker":
            raise ValueError("Desktop modes require local Docker serves.")
        if serves._is_compose_up(owner.get("up")):
            desktop._local_command(owner["up"])
    return value


def _guard_safe(profile):
    deadline = time.monotonic() + 10
    while True:
        reason = "publisher unavailable"
        try:
            result = subprocess.run([profile["guard_cli"], "media", "memory-guard", "status",
                                     "--config", profile["guard_config"], "--json"],
                                    capture_output=True, text=True, timeout=5,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            reply = json.loads(result.stdout)
            status = json.loads(reply["data"]) if isinstance(reply.get("data"), str) else reply["data"]
            if result.returncode == 0 and reply.get("ok") is True and status.get("safe") is True:
                return
            if isinstance(status.get("reason"), str):
                reason = status["reason"].replace("\n", " ")[:240]
        except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.TimeoutExpired):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Media memory guard is not ready: " + reason + "; recover its managed publisher if this persists.")
        time.sleep(1)


def _lifecycle(action, owners, names, run, timeout):
    output = io.StringIO()
    with redirect_stdout(output):
        if action == "start":
            rc = serves.cmd_up(owners, names, _run=run, wait_for_readiness=True, readiness_timeout=timeout)
        else:
            rc = serves.cmd_down(owners, names, _run=run, keep_container=True, confirm=True)
    if rc:
        raise RuntimeError("Managed " + action + " failed: " + output.getvalue())


def _active_workers(profile, rows):
    return [r for r in rows if r["name"].lstrip("/") in profile["worker_containers"]
            and r["status"] not in {"exited", "dead"}]


def _workers_idle(profile, rows):
    if _active_workers(profile, rows):
        raise RuntimeError("Media worker is active or starting; finish its jobs and use managed worker teardown first.")


def _release_idle_worker(profile, *, apply, run):
    binding = profile["idle_teardown"]
    # Use the controller's existing lifecycle authority and store; never stop the worker directly.
    script = Path(__file__).with_name("media").joinpath("idle_teardown.py").read_text(encoding="utf-8")
    reply = run(["docker", "exec", "-i", binding["controller"], "python", "-",
                 json.dumps(binding), "apply" if apply else "preview"],
                input=script, capture_output=True, text=True, timeout=120)
    try:
        if reply.returncode or len(reply.stdout) > 65536:
            raise ValueError("idle release unavailable")
        result = json.loads(reply.stdout)
        if not isinstance(result, dict) or not isinstance(result.get("reason"), str):
            raise ValueError("idle release invalid")
        return result
    except (ValueError, TypeError):
        return {"idle": False, "applied": False, "reason": "worker_state_unavailable"}


def _prepare_workers(profile, rows, *, apply, run):
    active = _active_workers(profile, rows)
    if not active:
        return 0
    binding = profile.get("idle_teardown")
    if (binding is None or len(active) != 1 or active[0]["status"] != "running"
            or active[0]["name"] != "/" + binding["worker"]):
        _workers_idle(profile, rows)
    if not any(r["name"] == "/" + binding["controller"] and r["status"] == "running" for r in rows):
        raise RuntimeError("Media worker lifecycle controller is unavailable; cannot prove the worker idle.")
    result = _release_idle_worker(profile, apply=apply, run=run)
    if result.get("idle") is not True or (apply and result.get("applied") is not True):
        raise RuntimeError("Media worker blocks this mode change: " + result.get("reason", "idle state unknown"))
    return 1


def _start_media(profile, run, timeout):
    names = [s["name"] for s in profile["media"] if s.get("up")]
    _lifecycle("start", profile["media"], names, run, timeout)
    deadline = time.monotonic() + timeout
    while True:
        rows = desktop._containers(run)
        if _media_running(profile, rows):
            return
        current = {r["name"].lstrip("/"): r for r in rows}
        if (any(current.get(s["container"], {}).get("status") != "running" for s in profile["media"])
                or time.monotonic() >= deadline):
            raise RuntimeError("A declared media control is not running and healthy.")
        time.sleep(2)


def _media_running(profile, rows):
    current = {r["name"].lstrip("/"): r for r in rows}
    return all(current.get(s["container"], {}).get("status") == "running"
               and current[s["container"]]["health"] in {None, "healthy"} for s in profile["media"])


def _preflight(profile, mode, run):
    rows = desktop._containers(run)
    target = profile["secondary"][0]
    if mode == "secondary" and not any(r["name"] == "/" + target["container"] for r in rows):
        raise RuntimeError("Promoted secondary container is missing; load its declared managed recipe first.")
    models = {s["container"]: s for s in profile["models"] + profile["secondary"]}
    controls = {s["container"]: s for s in profile["media"]}
    known = set(models) | set(controls) | set(profile["worker_containers"])
    if any(r["name"].lstrip("/") not in known and r["status"] not in {"exited", "dead"} for r in rows):
        raise RuntimeError("Undeclared active container; declare its mode ownership or stop it before switching.")
    idle_workers = 0
    if mode != "media" or not _media_running(profile, rows) or any(
            r["name"].lstrip("/") in models and r["status"] not in {"exited", "dead"} for r in rows):
        idle_workers = _prepare_workers(profile, rows, apply=False, run=run)
    if mode == "gaming" and not idle_workers:
        desktop._snapshot(run, list(models.values()) + list(controls.values()))
    return rows, models, controls, idle_workers


def select_mode(mode, *, confirm=False, dry_run=False, timeout=600, run=desktop._run):
    if sys.platform != "win32":
        raise ValueError("Desktop modes currently support Windows only.")
    if mode not in {"gaming", "secondary", "media"}:
        raise ValueError("Unknown Desktop mode.")
    if type(timeout) is not int or not 1 <= timeout <= 1800:
        raise ValueError("timeout must be between 1 and 1800 seconds.")
    if not (confirm or dry_run):
        raise ValueError("Use --dry-run or --confirm.")
    profile = _profile()
    mode_path = Path(paths.config_path("state", "desktop-mode.json"))
    cooldown_path = mode_path.with_name("desktop-switch-cooldown.json")
    with ExitStack() as locks:
        if not dry_run:
            ignored = desktop._enter_operation(locks, cooldown_path)
            if ignored:
                return ignored
        endpoint = desktop._call(["docker", "context", "inspect", desktop.CONTEXT, "--format",
                                  "{{.Endpoints.docker.Host}}"], run=run)
        if endpoint != desktop.PIPE:
            raise ValueError("Refusing a non-local Docker Desktop Linux context.")
        state = desktop._state(run)
        result = {"mode": mode, "state": state, "dry_run": dry_run, "changed": False}
        if dry_run:
            if state == "running":
                _, _, _, idle_workers = _preflight(profile, mode, run)
                if idle_workers:
                    result["idle_workers_to_teardown"] = idle_workers
            else:
                result["deferred_checks"] = ["container ownership and target existence after Docker starts"]
            if mode == "media":
                _guard_safe(profile)
            return result
        if state == "stopped" and mode != "gaming":
            desktop._call(["docker", "desktop", "start", "--timeout", str(timeout)],
                          run=run, timeout=timeout + 10)
            if desktop._state(run) != "running":
                raise RuntimeError("Docker Desktop did not start; retry the selected mode.")
            state = "running"
            result["changed"] = True
        if state == "running":
            rows, models, controls, idle_workers = _preflight(profile, mode, run)
            target = profile["secondary"][0]
            controls_were_running = any(r["name"].lstrip("/") in controls and r["status"] == "running" for r in rows)
            controls_stopped = False
            secondary_started = False
            try:
                if mode != "media" or idle_workers:
                    # Stop submissions first; keep lifecycle authority alive for owned teardown.
                    for owner in [s for s in profile["media"] if s.get("up")]:
                        current = desktop._containers(run)
                        if any(r["name"] == "/" + owner["container"] and r["status"] not in {"exited", "dead"} for r in current):
                            controls_were_running = True
                            controls_stopped = True
                            _lifecycle("stop", [owner], [owner["name"]], run, timeout)
                            result["changed"] = True
                    frozen = desktop._containers(run)
                    gateways = {s["container"] for s in profile["media"] if s.get("up")}
                    if any(r["name"].lstrip("/") in gateways and r["status"] not in {"exited", "dead"} for r in frozen):
                        raise RuntimeError("Media submission gateway did not stop; worker teardown refused.")
                    released = _prepare_workers(profile, frozen, apply=True, run=run)
                    result["changed"] = result["changed"] or bool(released)
                    if mode != "media":
                        for owner in [s for s in profile["media"] if not s.get("up")]:
                            if any(r["name"] == "/" + owner["container"] and r["status"] not in {"exited", "dead"} for r in desktop._containers(run)):
                                controls_stopped = True
                                _lifecycle("stop", [owner], [owner["name"]], run, timeout)
                                result["changed"] = True
                    _workers_idle(profile, desktop._containers(run))
                    if mode == "gaming":
                        desktop._snapshot(run, list(models.values()) + list(controls.values()))
                for owner in models.values():
                    if mode == "secondary" and owner["container"] == target["container"]:
                        continue
                    if any(r["name"] == "/" + owner["container"] and r["status"] not in {"exited", "dead"} for r in desktop._containers(run)):
                        _lifecycle("stop", [owner], [owner["name"]], run, timeout)
                        result["changed"] = True
                if mode == "gaming":
                    _workers_idle(profile, desktop._containers(run))
                    desktop._call(["docker", "desktop", "stop", "--timeout", str(timeout)],
                                  run=run, timeout=timeout + 10)
                    if desktop._state(run) != "stopped":
                        raise RuntimeError("Docker Desktop did not stop; retry gaming mode.")
                    result["changed"] = True
                elif mode == "secondary":
                    live = next(r for r in desktop._containers(run) if r["name"] == "/" + target["container"])
                    if live["status"] != "running":
                        secondary_started = True
                        _lifecycle("start", profile["secondary"], [target["name"]], run, timeout)
                        result["changed"] = True
                    elif not serves._await_healthy(target, timeout, 2):
                        raise RuntimeError("Secondary readiness timed out.")
                    if not serves._serve_identity_ready(target):
                        raise RuntimeError("Secondary served-model identity differs from its declared assignment.")
                else:
                    if not _media_running(profile, desktop._containers(run)):
                        _start_media(profile, run, timeout)
                        result["changed"] = True
                    for owner in profile["media"]:
                        if owner.get("up") and not serves._await_healthy(owner, timeout, 2):
                            raise RuntimeError("Media gateway readiness timed out.")
                    _guard_safe(profile)
            except BaseException:
                if secondary_started:
                    _lifecycle("stop", profile["secondary"], [target["name"]], run, timeout)
                if controls_stopped and controls_were_running and desktop._state(run) == "running":
                    _start_media(profile, run, timeout)
                raise
        legacy = mode_path.with_name("desktop-switch.json")
        if legacy.exists():
            legacy.rename(legacy.with_name("desktop-switch.superseded-" + str(time.time_ns()) + ".json"))
        serves._write_switch_journal(str(mode_path), {"mode": mode, "phase": "ready"})
        serves._write_switch_journal(str(cooldown_path), {
            "completed_monotonic": time.monotonic(), "completed_wall": time.time()})
        return {**result, "state": "stopped" if mode == "gaming" else "running"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("gaming", "secondary", "media"))
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        result = select_mode(args.mode, confirm=args.confirm, dry_run=args.dry_run, timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("Desktop mode failed: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0
