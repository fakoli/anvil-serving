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
import ssl
import urllib.error
import urllib.parse
import urllib.request

from . import desktop, envfile, paths, serve_recipes, serves


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _profile():
    path = Path(paths.config_path("desktop-modes.toml"))
    if path.stat().st_size > 65536:
        raise ValueError("Desktop mode configuration is too large.")
    value = tomllib.loads(path.read_text(encoding="utf-8"))
    required = {"schema", "media_manifest", "model_manifests", "worker_containers", "guard_cli", "guard_config"}
    dynamic = {"secondary_router_url", "secondary_router_alias", "secondary_router_token_file",
               "secondary_router_token_env", "secondary_manifest_dir"}
    optional = {"secondary_manifest", "idle_teardown", "secondary_router_ca_file"} | dynamic
    if not required <= set(value) <= required | optional or value["schema"] != "anvil-serving.desktop-modes/v1":
        raise ValueError("Invalid Desktop mode configuration.")
    dynamic_present = dynamic & set(value)
    if dynamic_present and dynamic_present != dynamic:
        raise ValueError("Dynamic secondary assignment requires router URL, alias, token file, token name, and manifest directory.")
    if not dynamic_present and "secondary_manifest" not in value:
        raise ValueError("Desktop mode configuration requires a secondary manifest or dynamic secondary assignment.")
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

    for key in ("media_manifest", "guard_cli", "guard_config"):
        value[key] = resolve(value[key])
    if "secondary_manifest" in value:
        value["secondary_manifest"] = resolve(value["secondary_manifest"])
        value["secondary"] = serves.load_manifest(value["secondary_manifest"])
    else:
        value["secondary"] = []
    value["media"] = serves.load_manifest(value["media_manifest"])
    value["models"] = [s for item in value["model_manifests"] for s in serves.load_manifest(resolve(item))]
    if (not dynamic_present and len(value["secondary"]) != 1) or not value["media"] or not any(s.get("up") for s in value["media"]):
        raise ValueError("Declare one secondary serve and an activatable media control plane.")
    value["dynamic_secondary"] = bool(dynamic_present)
    if dynamic_present:
        router_url = value["secondary_router_url"]
        if not isinstance(router_url, str):
            raise ValueError("Invalid dynamic secondary router URL.")
        parsed_url = urllib.parse.urlsplit(router_url)
        if (parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc or parsed_url.username or parsed_url.password
                or parsed_url.path not in {"", "/"} or parsed_url.query or parsed_url.fragment
                or parsed_url.hostname == "localhost"
                or (parsed_url.scheme == "http" and parsed_url.hostname != "127.0.0.1")):
            raise ValueError("Invalid dynamic secondary router URL.")
        if (not isinstance(value["secondary_router_alias"], str)
                or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", value["secondary_router_alias"])):
            raise ValueError("Invalid dynamic secondary router alias.")
        if (not isinstance(value["secondary_router_token_env"], str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", value["secondary_router_token_env"])):
            raise ValueError("Invalid dynamic secondary router token name.")
        value["secondary_router_token_file"] = resolve(value["secondary_router_token_file"])
        if "secondary_router_ca_file" in value:
            value["secondary_router_ca_file"] = resolve(value["secondary_router_ca_file"])
        manifest_dir = value["secondary_manifest_dir"]
        if (not isinstance(manifest_dir, str) or not manifest_dir or "\x00" in manifest_dir):
            raise ValueError("Dynamic secondary manifest directory is missing.")
        directory = (path.parent / manifest_dir).resolve()
        if not directory.is_dir():
            raise ValueError("Dynamic secondary manifest directory is missing.")
        dynamic_models = []
        for manifest in sorted(directory.glob("serves.*.toml")):
            dynamic_models.extend(serve for serve in serves.load_manifest(str(manifest)) if serve.get("router_tier"))
        if not dynamic_models:
            raise ValueError("Dynamic secondary manifest directory declares no routed Docker backers.")
        value["dynamic_models"] = dynamic_models
    else:
        value["dynamic_models"] = []
    model_names = {s["container"] for s in value["models"] + value["secondary"] + value["dynamic_models"]}
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
    for owner in value["secondary"] + value["media"] + value["models"] + value["dynamic_models"]:
        if owner.get("runtime", "docker") != "docker":
            raise ValueError("Desktop modes require local Docker serves.")
        if serves._is_compose_up(owner.get("up")):
            desktop._local_command(owner["up"])
    return value


def _dynamic_secondary(profile):
    token = envfile.read_dotenv(profile["secondary_router_token_file"]).get(profile["secondary_router_token_env"], "").strip()
    if not token:
        raise RuntimeError("Dynamic secondary router credential is unavailable.")
    alias = profile["secondary_router_alias"]
    url = profile["secondary_router_url"].rstrip("/") + "/v1/models/capabilities?model=" + urllib.parse.quote(alias, safe="")
    request = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": "Bearer " + token})
    try:
        handlers = [_NoRedirect()]
        if profile.get("secondary_router_ca_file"):
            handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(
                cafile=profile["secondary_router_ca_file"])))
        with urllib.request.build_opener(*handlers).open(request, timeout=5) as response:
            raw = response.read(65537)
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RuntimeError("Dynamic secondary router assignment is unavailable.") from exc
    if len(raw) > 65536:
        raise RuntimeError("Dynamic secondary router assignment is invalid.")
    try:
        rows = json.loads(raw)["data"]
        if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict) and rows[0].get("model") is None:
            raise RuntimeError("Desktop startup needs a configured model identity that the router can report while parked.")
        if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                or not isinstance(rows[0].get("aliases"), list) or alias not in rows[0]["aliases"]
                or "members" in rows[0] or not isinstance(rows[0].get("id"), str)
                or not isinstance(rows[0].get("model"), str) or not rows[0]["model"]):
            raise ValueError
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise RuntimeError("Dynamic secondary router assignment is invalid.") from None
    matches = [serve for serve in profile["dynamic_models"]
               if serve.get("runtime") == "docker" and serve.get("router_tier") == rows[0]["id"]
               and serve.get("served_name") == rows[0]["model"]]
    if len(matches) != 1:
        raise RuntimeError("Dynamic secondary router assignment has no unambiguous local managed backer.")
    return matches[0]


def _secondary(profile):
    return _dynamic_secondary(profile) if profile["dynamic_secondary"] else profile["secondary"][0]


def _recipe_start_binding(target):
    argv = target.get("up") or []
    if argv[:3] == ["python", "-m", "anvil_serving.cli"]:
        argv = ["anvil-serving", *argv[3:]]
    prefix = ["anvil-serving", "models", "recipes", "load"]
    if len(argv) < 6 or argv[:4] != prefix or not isinstance(argv[4], str):
        raise ValueError("Dynamic secondary requires its exact managed recipe-load command.")
    selector = argv[4]
    prefix += [selector, "--registry"]
    if argv[:len(prefix)] != prefix or len(argv) not in {10, 12}:
        raise ValueError("Dynamic secondary requires its exact managed recipe-load command.")
    registry, tail = argv[len(prefix)], argv[len(prefix) + 1:]
    if tail[:2] != ["--container", target["container"]] or tail[-1:] != ["--confirm"]:
        raise ValueError("Dynamic secondary recipe-load command has an invalid container binding.")
    gpu_device = None
    if len(tail) == 3:
        if tail[2:] != ["--confirm"]:
            raise ValueError("Dynamic secondary recipe-load command is invalid.")
    else:
        if tail[2] != "--gpu-device" or not isinstance(tail[3], str) or tail[4:] != ["--confirm"]:
            raise ValueError("Dynamic secondary recipe-load command is invalid.")
        gpu_device = tail[3]
    if not os.path.isabs(registry) or not os.path.isfile(registry):
        raise ValueError("Dynamic secondary recipe registry is missing.")
    try:
        recipe = serve_recipes.find_recipe(serve_recipes.load_registry(registry), selector)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("Dynamic secondary recipe registry is invalid.") from exc
    serve = recipe.get("serve") if recipe else {}
    if (recipe is None or recipe.get("model") != selector
            or serve.get("served_model_name") != target["served_name"]
            or serve.get("port") != target["port"]
            or (recipe.get("expected_container") is not None and recipe["expected_container"] != target["container"])):
        raise ValueError("Dynamic secondary recipe does not match its declared model and port.")
    return {"model": selector, "registry": registry, "gpu_device": gpu_device}


def _load_recipe(binding, target, timeout):
    argv = [sys.executable, "-m", "anvil_serving.cli", "models", "recipes", "load", binding["model"],
            "--registry", binding["registry"], "--container", target["container"]]
    if binding["gpu_device"]:
        argv += ["--gpu-device", binding["gpu_device"]]
    argv += ["--confirm"]
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.upper().startswith("DOCKER_"):
            del environment[name]
    environment["DOCKER_CONTEXT"] = desktop.CONTEXT
    environment["ANVIL_SERVING_NO_HOME_ENV"] = "1"
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 30, env=environment,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("Managed secondary recipe load failed.")


def _container_for(target, run):
    return next((row for row in desktop._containers(run)
                 if row["name"] == "/" + target["container"]), None)


def _compose_owner(target, container_id, run):
    if not container_id or not serves._is_compose_up(target.get("up")):
        return None
    format_string = '{{index .Config.Labels "com.docker.compose.project"}}/{{index .Config.Labels "com.docker.compose.service"}}'
    labels = desktop._call(["docker", "inspect", "--format", format_string, container_id], run=run)
    project, _, service = labels.partition("/")
    if project == serves._expected_compose_project(target) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", service):
        return project, service
    return None


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


def _preflight(profile, mode, target, recipe_start, run):
    rows = desktop._containers(run)
    if (mode == "secondary" and not any(r["name"] == "/" + target["container"] for r in rows)
            and (not profile["dynamic_secondary"] or (recipe_start is None and not serves._is_compose_up(target.get("up"))))):
        raise RuntimeError("Secondary container is missing; declare its managed startup first.")
    models = {s["container"]: s for s in profile["models"] + profile["secondary"] + profile["dynamic_models"]}
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
        if state == "stopped" and mode != "gaming" and not dry_run:
            desktop._call(["docker", "desktop", "start", "--timeout", str(timeout)],
                          run=run, timeout=timeout + 10)
            if desktop._state(run) != "running":
                raise RuntimeError("Docker Desktop did not start; retry the selected mode.")
            state = "running"
            result["changed"] = True
        defer_assignment = dry_run and state == "stopped" and mode == "secondary" and profile["dynamic_secondary"]
        target = _secondary(profile) if mode == "secondary" and not defer_assignment else None
        recipe_start = None
        if target and profile["dynamic_secondary"] and target.get("up") and not serves._is_compose_up(target["up"]):
            recipe_start = _recipe_start_binding(target)
        if target is not None:
            result["target"] = {key: target[key] for key in ("name", "container", "served_name", "router_tier") if key in target}
        if dry_run:
            if state == "running":
                _, _, _, idle_workers = _preflight(profile, mode, target, recipe_start, run)
                if idle_workers:
                    result["idle_workers_to_teardown"] = idle_workers
            else:
                result["deferred_checks"] = ["container ownership and target existence after Docker starts"]
                if defer_assignment:
                    result["deferred_checks"].append("router alias assignment after Docker starts")
            if mode == "media":
                _guard_safe(profile)
            return result
        if state == "running":
            rows, models, controls, idle_workers = _preflight(profile, mode, target, recipe_start, run)
            controls_were_running = any(r["name"].lstrip("/") in controls and r["status"] == "running" for r in rows)
            controls_stopped = False
            secondary_start_attempted = False
            started_container_id = None
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
                    live = _container_for(target, run)
                    if live is None or live["status"] != "running":
                        secondary_start_attempted = True
                        started_container_id = live["id"] if live else None
                        prior_compose_owner = _compose_owner(target, started_container_id, run)
                        if live is None and not recipe_start and not (profile["dynamic_secondary"] and serves._is_compose_up(target.get("up"))):
                            raise RuntimeError("Secondary container is missing; declare its managed startup first.")
                        if live is None and recipe_start:
                            _load_recipe(recipe_start, target, timeout)
                        else:
                            _lifecycle("start", [target], [target["name"]], run, timeout)
                        live = _container_for(target, run)
                        if live is None:
                            raise RuntimeError("Secondary managed startup did not create its declared container.")
                        if started_container_id and live["id"] != started_container_id:
                            if not prior_compose_owner or _compose_owner(target, live["id"], run) != prior_compose_owner:
                                raise RuntimeError("Secondary container was replaced by an unverified owner during startup.")
                        started_container_id = live["id"]
                        result["changed"] = True
                    if not serves._await_healthy(target, timeout, 2):
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
            except BaseException as failure:
                recovery_errors = []
                if secondary_start_attempted:
                    try:
                        live = _container_for(target, run)
                        if live and live["status"] not in {"exited", "dead"}:
                            if live["id"] != started_container_id:
                                raise RuntimeError("Secondary startup ownership is uncertain. Media remains parked.")
                            # Stop only the observed incarnation. Preserve its container for a retry.
                            desktop._call(["docker", "stop", started_container_id], run=run, timeout=timeout)
                    except Exception as exc:
                        recovery_errors.append(str(exc))
                if controls_stopped and controls_were_running and not recovery_errors and desktop._state(run) == "running":
                    try:
                        _start_media(profile, run, timeout)
                    except Exception as exc:
                        recovery_errors.append(str(exc))
                if recovery_errors:
                    raise RuntimeError(f"{failure}. Recovery incomplete: " + "; ".join(recovery_errors)) from failure
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
