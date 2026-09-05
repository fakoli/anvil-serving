"""Shared, owner-checked service lifecycle used by CLI and typed tools."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import math
import numbers
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from ..guard import evaluate_capacity_policy
from ..operator_output import redact
from ..paths import config_path, resolve_topology_path
from ..topology import Topology, load_topology, resolve_command_identity
from .contracts import READ_ACTIONS, MUTATING_ACTIONS, MODEL_ENGINES, ServiceError, capabilities, identifier, validate_platform
from .manifest import digest, load_manifest, save_manifest, validate
from . import engine


def _safe(value):
    secrets = [value for key, value in os.environ.items()
               if any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and len(value) >= 6]
    return redact(value, secrets=secrets)


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
            raise ServiceError("unsafe_lock", "service lock has unsafe ownership")
        if os.name == "nt":
            import msvcrt
            if info.st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, 0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise ServiceError("operation_in_progress", "another service operation holds the operator lock") from exc
    finally:
        os.close(fd)


def _owner(binding, topo, identity, target, host_os, action):
    try:
        resource = topo.resource(binding["resource"])
        runtime = topo.runtime(resource.runtime)
        host = topo.host(resource.host)
    except KeyError as exc:
        raise ServiceError("owner_missing", "service resource is not declared in topology") from exc
    if identity.host.id != host.id or (target and target != "host:" + host.id):
        raise ServiceError("owner_mismatch", "service must execute on its declared topology host")
    wsl = host.os == "windows" and host_os == "linux" and runtime.role == "wsl" and binding["manager"] == "docker"
    docker_guest = host.os in {"windows", "macos"} and host_os == "linux" and runtime.role == "docker" and binding["manager"] == "docker"
    if host.os and host.os != host_os and not (wsl or docker_guest):
        raise ServiceError("owner_mismatch", "declared host OS differs from execution OS")
    if identity.runtime.id != runtime.id:
        raise ServiceError("owner_mismatch", "service requires its exact supervisor execution runtime")
    if binding["manager"] == "launchd" and runtime.role != "native":
        raise ServiceError("owner_mismatch", "launchd requires its native runtime")
    validate_platform(binding, host_os)
    model = binding["engine"] in MODEL_ENGINES
    if model and resource.workload not in {"model", "llm", "stt", "tts", "media", "experimental-model"}:
        raise ServiceError("workload_mismatch", "model engine requires an explicit model workload resource")
    if action in {"up", "restart", "enable", "install", "adopt"}:
        policy = topo.capacity_policy(host.capacity_policy) if host.capacity_policy else None
        decision = evaluate_capacity_policy(host_id=host.id, workload=resource.workload,
            capacity_policy=host.capacity_policy, allow_model_workloads=policy.allow_model_workloads if policy else True,
            allow_experimental_model_workloads=policy.allow_experimental_model_workloads if policy else False)
        if not decision.allowed:
            raise ServiceError("capacity_refused", decision.reason)
    return resource


def _known(state, action):
    if type(state.get("registered")) is not bool or type(state.get("running")) is not bool:
        raise ServiceError("unknown_state", "supervisor state is unknown; refusing mutation")
    if action in {"enable", "disable"} and type(state.get("enabled")) is not bool:
        raise ServiceError("unknown_state", "startup policy state is unknown; refusing mutation")


def _same(left, right):
    return all(left.get(k) == right.get(k) for k in ("identity", "pid", "registered", "running", "enabled"))


def _order(bindings, name):
    result = []
    def visit(current):
        if current in result:
            return
        for dep in bindings[current]["dependencies"]:
            visit(dep)
        result.append(current)
    visit(name)
    return result


def _run_plan(commands, run, deadline):
    for argv in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ServiceError("operation_timeout", "service operation deadline expired")
        try:
            result = run(argv, capture_output=True, text=True, timeout=remaining, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ServiceError("supervisor_failed", "supervisor action failed or timed out; inspect service status and logs") from exc
        if result.returncode:
            # Raw subprocess errors can include environments or arbitrary program output.
            raise ServiceError("supervisor_failed", "supervisor action failed; inspect service status and logs")


def _verify(adapter, binding, action, deadline, sleep, before=None):
    stable = 0
    while True:
        after = adapter.inspect(binding)
        valid = (after.get("running") is True if action in {"up", "restart"} else
                 after.get("running") is False and (binding["manager"] != "launchd" or after.get("registered") is False)
                 if action == "down" else after.get("enabled") is (action == "enable"))
        if action == "restart" and before and before.get("running") is True:
            valid = valid and (isinstance(after.get("pid"), int) and after["pid"] != before.get("pid"))
        stable = stable + 1 if valid else 0
        if stable >= 2:
            return after
        if time.monotonic() >= deadline:
            raise ServiceError("postcondition_failed", "service postcondition did not remain satisfied before timeout")
        sleep(min(.1, max(0, deadline - time.monotonic())))


def _ready(row, probe, deadline, sleep):
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ServiceError("readiness_failed", "service endpoint readiness did not pass before timeout; inspect logs")
        metadata = probe(row, timeout=min(3, remaining))
        if metadata.get("ready") is True or not row.get("endpoint"):
            return metadata
        sleep(min(.1, max(0, deadline - time.monotonic())))


def _bind_check(name, selected, bindings, descriptions, observations):
    def ports(current):
        values = set(descriptions[current].get("ports", []))
        endpoint = bindings[current].get("endpoint")
        if endpoint:
            url = urlsplit(endpoint)
            values.add(url.port or (443 if url.scheme == "https" else 80))
        return values
    wanted = ports(name)
    for other in observations:
        if other != name and (other in selected or observations[other].get("running") is not False) and wanted & ports(other):
            raise ServiceError("port_conflict", f"declared bind conflict between {name} and {other}")
    if observations[name].get("running") is False:
        for port in wanted:
            with socket.socket() as sock:
                sock.settimeout(.2)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    raise ServiceError("port_conflict", "declared endpoint bind is occupied by another process")


def _admission(binding, bindings, observations, resource, topo, run):
    if resource.workload not in {"model", "llm", "stt", "tts", "media", "experimental-model"}:
        return
    if binding["manager"] == "docker":
        # Reuse the authoritative reservation ledger; generic host operations cannot bypass it.
        from .. import serves, reservations
        if not binding.get("serve") or not binding.get("serve_manifest"):
            raise ServiceError("admission_required", "Docker model binding requires its owning serve and serve_manifest")
        declarations = serves.load_manifest(binding["serve_manifest"])
        declarations = [s for s in declarations if s.get("runtime", "docker") == "docker"]
        selected = [s for s in declarations if s["name"] == binding["serve"]]
        if len(selected) != 1 or selected[0]["container"] != binding["container"]:
            raise ServiceError("owner_mismatch", "Docker binding differs from its owning serve")
        states = serves.docker_states([s["container"] for s in declarations], _run=run)
        if any(value in {"error", "unknown", "restarting", "removing"} for value in states.values()):
            raise ServiceError("unknown_state", "reservation ledger contains unknown container state")
        def state_of(name):
            return states.get(name, "absent")
        denial = reservations.deny_exclusive_conflict(declarations, selected, state_of) or reservations.deny_over_budget(declarations, selected, state_of)
        if denial:
            raise ServiceError("capacity_refused", "owning serve reservation admission refused the service start")
    else:
        if not binding.get("memory_mib"):
            raise ServiceError("admission_required", "native model binding requires an explicit memory_mib budget")
        try:
            total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024)
        except (ValueError, OSError):
            result = run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5, check=False)
            if result.returncode or not result.stdout.strip().isdigit():
                raise ServiceError("capacity_unknown", "cannot establish native memory capacity")
            total = int(result.stdout.strip()) // (1024 * 1024)
        committed = binding["memory_mib"]
        for name, other in bindings.items():
            if name == binding["id"] or other["manager"] != "launchd" or other["engine"] not in MODEL_ENGINES:
                continue
            if topo.resource(other["resource"]).host != resource.host:
                continue
            if observations.get(name, {}).get("running") is not False:
                if not other.get("memory_mib"):
                    raise ServiceError("capacity_unknown", "resident native model has no declared memory budget")
                committed += other["memory_mib"]
        if committed > total - 4096:
            raise ServiceError("capacity_refused", "native model budgets exceed memory capacity with 4 GiB reserved for host services")


def _install(row, adapter, path, revision, dry_run, confirm):
    if row["manager"] == "docker":
        state = adapter.inspect(row)
        _known(state, "install")
        if not state["registered"]:
            raise ServiceError("recipe_required", "create Docker containers with their owning serve recipe before binding them")
        return {"action": "install", "applied": False, "already_installed": True, "services": [{"id": row["id"], "before": state}]}
    destination = Path(row["definition"])
    if destination.exists() or destination.is_symlink():
        adapter.describe(row)
        return {"action": "install", "applied": False, "already_installed": True, "services": [{"id": row["id"]}]}
    if not row.get("source_definition"):
        raise ServiceError("definition_required", "install requires a pinned source_definition")
    source = Path(row["source_definition"])
    staged = {**row, "definition": str(source)}
    adapter.describe(staged)
    if digest(source) != row["definition_sha256"]:
        raise ServiceError("definition_changed", "staged definition no longer matches its pinned hash")
    state = adapter.inspect(staged)
    _known(state, "install")
    if state["registered"]:
        raise ServiceError("already_registered", "installation refuses to replace an existing supervisor registration")
    receipt = {"action": "install", "applied": False, "services": [{"id": row["id"], "definition_sha256": row["definition_sha256"]}]}
    if dry_run or not confirm:
        return receipt
    with _lock(path):
        if digest(path) != revision or digest(source) != row["definition_sha256"]:
            raise ServiceError("state_changed", "installation declaration changed since inspection")
        if not _same(state, adapter.inspect(staged)):
            raise ServiceError("state_changed", "supervisor registration changed since inspection")
        adapter.describe(staged)
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["definition_sha256"]:
            raise ServiceError("definition_changed", "staged definition changed during installation")
        # The operator creates the destination directory. Never create a new
        # directory hierarchy or overwrite a file on behalf of a remote caller.
        parent_info = destination.parent.stat()
        if destination.parent.is_symlink() or (hasattr(os, "getuid") and parent_info.st_uid != os.getuid()) or parent_info.st_mode & 0o022:
            raise ServiceError("unsafe_definition", "definition directory has unsafe ownership or permissions")
        fd, temporary = tempfile.mkstemp(prefix=".anvil-service-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Link is an atomic create-if-absent operation; rename would overwrite.
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise ServiceError("state_changed", "definition appeared during installation") from exc
        finally:
            os.unlink(temporary)
        adapter.describe(row)
    return {**receipt, "applied": True}


def execute(action, service=None, *, manifest=None, topology=None, topology_overlay=None, command_host=None,
            command_runtime=None, target=None, transport="local", dry_run=True, confirm=False,
            tail=100, timeout_seconds=30, binding=None, remote=False,
            expected_model=None, expected_engine=None,
            _adapters=None, _run=subprocess.run, _host_os=None, _sleep=time.sleep, _engine=engine.inspect):
    """Plan or perform one bounded operation on an explicitly declared owner.

    Remote transport is dispatched by the existing CLI/controller layer. This
    executor always runs locally on that selected owner; it never runs a shell.
    """
    if action not in READ_ACTIONS + MUTATING_ACTIONS:
        raise ServiceError("bad_action", "unknown service action")
    if (type(dry_run) is not bool or type(confirm) is not bool or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, numbers.Real) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 7200 or type(tail) is not int or not 1 <= tail <= 1000):
        raise ServiceError("bad_argument", "invalid service operation bounds or confirmation")
    if transport not in {"auto", "local"}:
        raise ServiceError("transport_required", "remote service operations must use the owner-selected CLI/controller dispatch")
    if remote and manifest is not None:
        raise ServiceError("bad_argument", "remote service operations use the configured operator home")
    bounded_until = [time.monotonic() + timeout_seconds]
    runner = _run
    def bounded_run(argv, **kwargs):
        remaining = bounded_until[0] - time.monotonic()
        if remaining <= 0:
            raise ServiceError("operation_timeout", "service operation deadline expired")
        kwargs["timeout"] = min(kwargs.get("timeout", remaining), remaining)
        return runner(argv, **kwargs)
    _run = bounded_run
    host_os = _host_os or {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)
    if action == "capabilities":
        return capabilities(host_os)
    from .launchd import Adapter as Launchd
    from .docker import Adapter as Docker
    adapters = _adapters or {"launchd": Launchd(run=_run), "docker": Docker(run=_run)}
    if action == "discover":
        found, errors = [], []
        for manager in capabilities(host_os)["managers"]:
            try:
                if manager == "docker":
                    adapters[manager].verify_context()
                found.extend(adapters[manager].discover())
            except ServiceError as exc:
                errors.append({"manager": manager, "code": exc.code})
        return _safe({"services": found, "errors": errors, "applied": False})
    path = Path(manifest or config_path("services.toml")).expanduser().absolute()
    revision = digest(path)
    bindings = load_manifest(path) if revision else {}
    if action == "status" and not bindings and service is None:
        return {"services": [], "applied": False}
    if service is not None:
        identifier(service, "service")
    elif action != "status":
        raise ServiceError("bad_argument", "one declared service is required")
    try:
        topo = topology if isinstance(topology, Topology) else load_topology(resolve_topology_path(topology), overlay_path=topology_overlay)
        identity = resolve_command_identity(topo, command_host=command_host, command_runtime=command_runtime)
    except (OSError, ValueError, KeyError) as exc:
        raise ServiceError("topology_required", "a valid topology and command identity are required") from exc
    if action == "adopt":
        if not isinstance(binding, dict) or binding.get("id") != service:
            raise ServiceError("bad_argument", "adoption requires one exact service binding")
        candidate = dict(binding)
        identifier(candidate.get("label") if candidate.get("manager") == "launchd" else candidate.get("container"), "supervisor identity")
        if candidate.get("manager") == "launchd":
            candidate["definition_sha256"] = digest(candidate["definition"])
        elif candidate.get("manager") == "docker":
            adapters["docker"].verify_context()
            matches = [item for item in adapters["docker"].discover() if item["container"] == candidate["container"]]
            if len(matches) != 1:
                raise ServiceError("owner_missing", "adoption requires one discovered Anvil-owned container")
            candidate.update({key: matches[0][key] for key in ("image_id", "identity_labels")})
        candidate = validate({"schema": "anvil-services/v1", "service": [candidate]}, path.parent)[service]
        if service in bindings and bindings[service] != candidate:
            raise ServiceError("already_bound", "service id is already bound; refusing replacement")
        combined = {**bindings, service: candidate}
        bindings = validate({"schema": "anvil-services/v1", "service": list(combined.values())}, path.parent)
    elif service is not None and service not in bindings:
        raise ServiceError("unknown_service", "service is not declared")
    for row in bindings.values():
        try:
            owner = topo.resource(row["resource"])
            for dependency in row["dependencies"]:
                dependency_owner = topo.resource(bindings[dependency]["resource"])
                if (owner.host, owner.runtime) != (dependency_owner.host, dependency_owner.runtime):
                    raise ServiceError("cross_owner_dependency", "service dependencies must share one supervisor execution owner")
        except KeyError as exc:
            raise ServiceError("owner_missing", "service resource is not declared in topology") from exc
    if service:
        for field, expected in (("model", expected_model), ("engine", expected_engine)):
            if expected is not None and bindings[service].get(field) != expected:
                raise ServiceError("identity_mismatch", f"service {field} differs from the owning serve or recipe")
    if action == "install":
        row = bindings[service]
        _owner(row, topo, identity, target, host_os, action)
        if row["manager"] == "docker":
            adapters["docker"].verify_context()
        return _safe(_install(row, adapters[row["manager"]], path, revision, dry_run, confirm))
    deferred = []
    selected = [service] if service else list(bindings)
    if action == "status" and service is None:
        selected = []
        for name, row in bindings.items():
            resource = topo.resource(row["resource"])
            if (resource.host, resource.runtime) == (identity.host.id, identity.runtime.id):
                selected.append(name)
            else:
                deferred.append({"id": name, "owner": {"host": resource.host, "runtime": resource.runtime},
                    "support": row["support"], "supervisor": {"registered": None, "running": None,
                    "enabled": None, "state": "requires_owner_runtime"}})
    if action == "up":
        selected = _order(bindings, service)
    owners, observations, descriptions = {}, {}, {}
    # Inspect other local bindings for dependencies and admission, never remote ones.
    relevant = set(selected)
    if action in MUTATING_ACTIONS:
        relevant.update(name for name, row in bindings.items()
            if (topo.resource(row["resource"]).host, topo.resource(row["resource"]).runtime)
            == (identity.host.id, identity.runtime.id))
    for name in sorted(relevant):
        row = bindings[name]
        owners[name] = _owner(row, topo, identity, target, host_os, action if name in selected else "status")
        adapter = adapters[row["manager"]]
        if row["manager"] == "docker":
            adapter.verify_context()
        descriptions[name] = adapter.describe(row)
        hint = descriptions[name].get("engine_hint", "unknown").replace("_", "-")
        if hint in MODEL_ENGINES and row["engine"] != hint:
            raise ServiceError("engine_mismatch", "declared engine differs from the verified launchd engine")
        observations[name] = adapter.inspect(row)
    if action == "status":
        return _safe({"applied": False, "services": [{"id": name, "supervisor": observations[name],
            "engine": _engine(bindings[name]), "support": bindings[name]["support"]} for name in selected] + deferred})
    if action == "logs":
        return _safe({"applied": False, "service": service, "lines": adapters[bindings[service]["manager"]].logs(bindings[service], tail)})
    for name in selected:
        row = bindings[name]
        if remote and action in {"down", "restart"} and (row.get("feature") == "controller" or owners[name].role == "controller" or descriptions[name].get("engine_hint") == "anvil_controller"):
            raise ServiceError("recovery_required", "controller self-shutdown requires a separate recovery transport")
        _known(observations[name], action)
    if action in {"down", "restart"}:
        for name, row in bindings.items():
            if name not in selected and service in _order(bindings, name) and observations.get(name, {}).get("running") is not False:
                raise ServiceError("dependent_running", "stop running dependent services before their dependency")
    plans = []
    projected = {name: {**state, "running": True} if name in selected else state
                 for name, state in observations.items()}
    for name in selected:
        row, before = bindings[name], observations[name]
        if action in {"up", "restart"}:
            _admission(row, bindings, projected, owners[name], topo, _run)
            _bind_check(name, selected, bindings, descriptions, observations)
        commands = [] if action == "adopt" else adapters[row["manager"]].plan(row, action, before)
        plans.append({"id": name, "before": before, "steps": commands})
    receipt = {"action": action, "applied": False, "services": plans}
    if dry_run or not confirm:
        return _safe(receipt)
    with _lock(path):
        if digest(path) != revision:
            raise ServiceError("state_changed", "service manifest changed since inspection")
        for name in relevant:
            fresh = adapters[bindings[name]["manager"]].inspect(bindings[name])
            if name in selected and not _same(observations[name], fresh):
                raise ServiceError("state_changed", "service identity or state changed since inspection")
            observations[name] = fresh
        if action == "adopt":
            save_manifest(path, bindings, expected_digest=revision)
            return _safe({**receipt, "applied": True})
        deadline = bounded_until[0]
        started = []
        try:
            for step in plans:
                row = bindings[step["id"]]
                adapter = adapters[row["manager"]]
                if action in {"up", "restart"}:
                    _admission(row, bindings, observations, owners[step["id"]], topo, _run)
                    _bind_check(row["id"], selected, bindings, descriptions, observations)
                if action == "up" and step["before"]["running"] is False and step["steps"]:
                    started.append((row, step["before"]))
                _run_plan(step["steps"], _run, deadline)
                step["after"] = _verify(adapter, row, action, deadline, _sleep, step["before"])
                observations[row["id"]] = step["after"]
                if action in {"up", "restart"}:
                    step["engine"] = _ready(row, _engine, deadline, _sleep)
        except ServiceError as exc:
            rollback = []
            for row, previous in reversed(started):
                adapter = adapters[row["manager"]]
                try:
                    state = adapter.inspect(row)
                    _known(state, "down")
                    end = time.monotonic() + min(timeout_seconds, 10)
                    bounded_until[0] = end
                    _run_plan(adapter.plan(row, "down", state), _run, end)
                    restored = _verify(adapter, row, "down", end, _sleep)
                    rollback.append({"id": row["id"], "stopped": True,
                                     "registration_restored": restored.get("registered") == previous.get("registered")})
                except ServiceError:
                    rollback.append({"id": row["id"], "stopped": False})
            exc.details = _safe({"services": plans, "rollback": rollback})
            exc.may_have_executed = True
            raise
    return _safe({**receipt, "applied": True})
