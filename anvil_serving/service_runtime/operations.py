"""Shared, owner-checked service lifecycle used by CLI and typed tools."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import numbers
import os
from pathlib import Path
import re
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
from .contracts import READ_ACTIONS, MUTATING_ACTIONS, MODEL_ENGINES, MAX_BYTES, ServiceError, capabilities, identifier, validate_platform
from .manifest import digest, load_manifest, save_manifest, validate
from . import engine


def _safe(value, *, inspect_process_environment=True):
    secrets = [] if not inspect_process_environment else [
        value for key, value in os.environ.items()
        if any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and len(value) >= 6
    ]
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


def _binding_matches_identity(binding, resource, runtime, identity):
    """Return whether this host identity may operate the declared binding."""
    return resource.host == identity.host.id and (
        identity.runtime.id == runtime.id
        or (
            binding["manager"] == "docker"
            and identity.runtime.role == "native"
            and runtime.role == "docker"
        )
    )


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
    if not _binding_matches_identity(binding, resource, runtime, identity):
        raise ServiceError("owner_mismatch", "service requires its exact supervisor execution runtime")
    if binding["manager"] == "launchd" and runtime.role != "native":
        raise ServiceError("owner_mismatch", "launchd requires its native runtime")
    if binding.get("retained_container") is True and (host_os != "linux" or identity.runtime.role != "native"):
        raise ServiceError("owner_mismatch", "retained-container lifecycle requires the owning native Linux runtime")
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
    return all(left.get(k) == right.get(k) for k in (
        "identity", "pid", "registered", "running", "enabled", "custody_sha256",
        "started_at", "finished_at",
    ))


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
        if binding.get("retained_container") is True:
            valid = valid and after.get("registered") is True and before is not None and all(
                after.get(key) == before.get(key) for key in (
                    "identity", "custody_sha256", "restart_count",
                    "writable_mounts_sha256", "security_projection_sha256",
                )
            )
            if action == "up":
                valid = valid and after.get("health_status") == "healthy"
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


def _retained_provenance(row):
    if row.get("retained_container") is not True:
        return
    source = Path(row["definition"])
    try:
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ServiceError("definition_changed", "retained-container definition provenance is unavailable") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ServiceError("definition_changed", "retained-container definition provenance is not regular")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES:
        raise ServiceError("definition_changed", "retained-container definition provenance exceeds size limit")
    if hashlib.sha256(raw).hexdigest() != row["definition_sha256"]:
        raise ServiceError("definition_changed", "retained-container definition provenance changed")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ServiceError("definition_changed", "retained-container definition provenance is invalid JSON") from exc
    fields = {
        "schema", "service", "container_id", "image_id", "source_kind", "source_sha256",
        "review_sha256", "healthcheck_sha256", "contains_secrets",
    }
    if (not isinstance(value, dict) or set(value) != fields
            or value.get("schema") != "anvil-retained-container-definition/v1"
            or value.get("service") != row["id"]
            or value.get("container_id") != row["container_id"]
            or value.get("image_id") != row["image_id"]
            or value.get("source_kind") != "reviewed-nonsecret-container-definition"
            or value.get("healthcheck_sha256") != row["healthcheck_sha256"]
            or value.get("contains_secrets") is not False):
        raise ServiceError("definition_changed", "retained-container definition provenance does not match its binding")
    for key in ("source_sha256", "review_sha256", "healthcheck_sha256"):
        if not isinstance(value.get(key), str) or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None:
            raise ServiceError("definition_changed", "retained-container definition provenance has invalid digests")


def _retained_preview_sha256(receipt, revision, row, owner, timeout_seconds):
    before = receipt["services"][0]["before"]
    rollback_timeout_seconds = (
        row["shutdown_grace_seconds"] + 5 if receipt["action"] == "up" else 0
    )
    payload = {
        "schema": "anvil-retained-container-preview/v1",
        "action": receipt["action"],
        "service": row["id"],
        "owner": {"resource": owner.id, "host": owner.host, "runtime": owner.runtime},
        "manifest_sha256": revision,
        "definition_sha256": row["definition_sha256"],
        "container_id": row["container_id"],
        "timeout_seconds": timeout_seconds,
        "rollback_timeout_seconds": rollback_timeout_seconds,
        "shutdown_grace_seconds": row["shutdown_grace_seconds"],
        "before": {key: before.get(key) for key in (
            "registered", "running", "enabled", "pid", "state", "identity",
            "custody_sha256", "restart_count", "writable_mounts_sha256",
            "security_projection_sha256", "started_at", "finished_at",
        )},
        "steps": receipt["services"][0]["steps"],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _retained_authorization(path, *, action, row, revision, preview_sha256, before):
    if (not isinstance(path, (str, os.PathLike)) or not str(path) or len(str(path)) > 4096
            or any(ord(character) < 32 for character in str(path))):
        raise ServiceError("authorization_required", "retained-container apply requires protected operator authorization")
    source = Path(path).expanduser().absolute()
    try:
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ServiceError("unsafe_authorization", "retained-container authorization must be a regular non-symlink file") from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())
                or info.st_mode & 0o077):
            raise ServiceError("unsafe_authorization", "retained-container authorization has unsafe ownership or permissions")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES:
        raise ServiceError("unsafe_authorization", "retained-container authorization exceeds size limit")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ServiceError("unsafe_authorization", "retained-container authorization is invalid JSON") from exc
    common = {
        "schema", "scope", "service", "action", "manifest_sha256", "preview_sha256",
        "container_id", "custody_sha256", "definition_sha256", "human_approval_sha256",
        "expires_at",
    }
    required = common
    if action == "down":
        required = (common | {
            "storage_consumer_state", "storage_consumer_evidence_sha256",
            "ingress_idle_state", "ingress_evidence_sha256", "drain_state",
            "drain_evidence_sha256", "uncertain_interruption_risk_accepted",
        })
    if not isinstance(data, dict) or set(data) != required:
        raise ServiceError("authorization_mismatch", "retained-container authorization fields do not match the action")
    expected = {
        "schema": "anvil-retained-container-authorization/v1",
        "scope": "retained-container-lifecycle",
        "service": row["id"],
        "action": action,
        "manifest_sha256": revision,
        "preview_sha256": preview_sha256,
        "container_id": row["container_id"],
        "custody_sha256": before["custody_sha256"],
        "definition_sha256": row["definition_sha256"],
    }
    if any(data.get(key) != value for key, value in expected.items()):
        raise ServiceError("authorization_mismatch", "retained-container authorization does not bind the current preview")
    digest_fields = {"human_approval_sha256"}
    if action == "down":
        digest_fields |= {
            "storage_consumer_evidence_sha256", "ingress_evidence_sha256", "drain_evidence_sha256",
        }
    if any(not isinstance(data.get(key), str) or re.fullmatch(r"[a-f0-9]{64}", data[key]) is None for key in digest_fields):
        raise ServiceError("authorization_mismatch", "retained-container authorization evidence is not digest-bound")
    if action == "down":
        if data["storage_consumer_state"] != "clear":
            raise ServiceError("storage_busy", "retained-container stop requires clear durable-storage custody")
        if data["ingress_idle_state"] not in {"quiesced", "unknown"} or data["drain_state"] not in {"complete", "bounded"}:
            raise ServiceError("authorization_mismatch", "retained-container interruption state is invalid")
        uncertain = data["ingress_idle_state"] == "unknown" or data["drain_state"] == "bounded"
        if (type(data["uncertain_interruption_risk_accepted"]) is not bool
                or uncertain and data["uncertain_interruption_risk_accepted"] is not True):
            raise ServiceError("authorization_mismatch", "unknown or bounded interruption requires explicit authorization")
    expires = data.get("expires_at")
    try:
        expiry = datetime.fromisoformat(expires.replace("Z", "+00:00")) if isinstance(expires, str) else None
    except ValueError as exc:
        raise ServiceError("authorization_mismatch", "retained-container authorization expiry is invalid") from exc
    now = datetime.now(timezone.utc)
    if expiry is None or expiry.tzinfo is None or expiry <= now or expiry > now + timedelta(minutes=5):
        raise ServiceError("authorization_expired", "retained-container authorization expired")
    return hashlib.sha256(raw).hexdigest()


def _consume_retained_authorization(path, *, authorization_sha256, preview_sha256):
    source = Path(path).expanduser().absolute()
    parent = source.parent
    parent_info = parent.stat()
    if (parent.is_symlink() or not stat.S_ISDIR(parent_info.st_mode)
            or (hasattr(os, "getuid") and parent_info.st_uid != os.getuid())
            or parent_info.st_mode & 0o077):
        raise ServiceError("unsafe_authorization", "retained-container authorization directory is unsafe")
    marker = source.with_name(source.name + ".consumed")
    payload = json.dumps({
        "schema": "anvil-retained-container-authorization-consumed/v1",
        "authorization_sha256": authorization_sha256,
        "preview_sha256": preview_sha256,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(marker, flags, 0o600)
    except FileExistsError as exc:
        raise ServiceError("authorization_consumed", "retained-container authorization was already consumed") from exc
    except OSError as exc:
        raise ServiceError("unsafe_authorization", "retained-container authorization could not be consumed safely") from exc
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)
    return hashlib.sha256(payload).hexdigest()


def execute(action, service=None, *, manifest=None, topology=None, topology_overlay=None, command_host=None,
            command_runtime=None, target=None, transport="local", dry_run=True, confirm=False,
            tail=100, timeout_seconds=30, binding=None, remote=False,
            expected_preview_sha256=None, operator_authorization_file=None,
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
            if candidate.get("external_compose") is True:
                candidate.update(adapters["docker"].adopt_external_compose(candidate))
            else:
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
    if service is not None and service in bindings and bindings[service].get("retained_container") is True:
        if action not in {"status", "up", "down"}:
            raise ServiceError("unsupported_action", "retained containers support only status and exact start or stop")
        if action == "down" and timeout_seconds < bindings[service]["shutdown_grace_seconds"] + 5:
            raise ServiceError("bad_argument", "retained-container stop timeout must reserve five seconds after its shutdown grace")
        if action == "status" and (expected_preview_sha256 is not None or operator_authorization_file is not None):
            raise ServiceError("bad_argument", "retained-container approval options apply only to start or stop")
    elif expected_preview_sha256 is not None or operator_authorization_file is not None:
        raise ServiceError("bad_argument", "retained-container approval options require a retained binding")
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
            runtime = topo.runtime(resource.runtime)
            if _binding_matches_identity(row, resource, runtime, identity):
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
        for name, row in bindings.items():
            resource = topo.resource(row["resource"])
            runtime = topo.runtime(resource.runtime)
            if _binding_matches_identity(row, resource, runtime, identity):
                relevant.add(name)
    for name in sorted(relevant):
        row = bindings[name]
        owners[name] = _owner(row, topo, identity, target, host_os, action if name in selected else "status")
        adapter = adapters[row["manager"]]
        if row["manager"] == "docker":
            adapter.verify_context()
        descriptions[name] = adapter.describe(row)
        _retained_provenance(row)
        hint = descriptions[name].get("engine_hint", "unknown").replace("_", "-")
        if hint in MODEL_ENGINES and row["engine"] != hint:
            raise ServiceError("engine_mismatch", "declared engine differs from the verified launchd engine")
        observations[name] = adapter.inspect(row)
    if action == "status":
        rows = []
        for name in selected:
            item = {"id": name, "supervisor": observations[name],
                    "engine": _engine(bindings[name]), "support": bindings[name]["support"]}
            rows.append(_safe(item, inspect_process_environment=bindings[name].get("retained_container") is not True))
        return {"applied": False, "services": rows + (_safe(deferred) if deferred else [])}
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
    retained_row = bindings[service] if service and bindings[service].get("retained_container") is True else None
    if retained_row is not None:
        preview_sha256 = _retained_preview_sha256(receipt, revision, retained_row, owners[service], timeout_seconds)
        receipt["retained_container"] = {
            "container_id": retained_row["container_id"],
            "definition_sha256": retained_row["definition_sha256"],
            "custody_sha256": observations[service]["custody_sha256"],
            "preview_sha256": preview_sha256,
            "authorization_required": True,
            "timeout_seconds": timeout_seconds,
            "rollback_timeout_seconds": (
                retained_row["shutdown_grace_seconds"] + 5 if action == "up" else 0
            ),
            "maximum_total_seconds": timeout_seconds + (
                retained_row["shutdown_grace_seconds"] + 5 if action == "up" else 0
            ),
            "shutdown_grace_seconds": retained_row["shutdown_grace_seconds"],
            "verification_reserve_seconds": 5 if action == "down" else 0,
        }
    if action == "adopt" and bindings[service].get("external_compose") is True:
        row = bindings[service]
        receipt["external_compose_identity"] = {
            "container": row["container"], "image_id": row["image_id"],
            "compose_project": row["compose_project"], "compose_service": row["compose_service"],
            "config_mount": {"source": row["compose_config_source"], "target": row["compose_config_target"], "read_only": True},
        }
    if dry_run or not confirm:
        return _safe(receipt, inspect_process_environment=retained_row is None)
    retained_authorization_sha256 = None
    if retained_row is not None:
        if expected_preview_sha256 != receipt["retained_container"]["preview_sha256"]:
            raise ServiceError("stale_preview", "retained-container apply requires the current reviewed preview digest")
        retained_authorization_sha256 = _retained_authorization(
            operator_authorization_file, action=action, row=retained_row, revision=revision,
            preview_sha256=expected_preview_sha256, before=observations[service],
        )
        receipt["retained_container"]["authorization_sha256"] = retained_authorization_sha256
    with _lock(path):
        if digest(path) != revision:
            raise ServiceError("state_changed", "service manifest changed since inspection")
        for name in relevant:
            _retained_provenance(bindings[name])
            fresh = adapters[bindings[name]["manager"]].inspect(bindings[name])
            if name in selected and not _same(observations[name], fresh):
                raise ServiceError("state_changed", "service identity or state changed since inspection")
            observations[name] = fresh
        if retained_row is not None:
            fresh_authorization_sha256 = _retained_authorization(
                operator_authorization_file, action=action, row=retained_row, revision=revision,
                preview_sha256=expected_preview_sha256, before=observations[service],
            )
            if fresh_authorization_sha256 != retained_authorization_sha256:
                raise ServiceError("state_changed", "retained-container authorization changed before mutation")
            receipt["retained_container"]["consumption_sha256"] = _consume_retained_authorization(
                operator_authorization_file,
                authorization_sha256=retained_authorization_sha256,
                preview_sha256=expected_preview_sha256,
            )
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
                    rollback_timeout = (
                        row["shutdown_grace_seconds"] + 5
                        if row.get("retained_container") is True
                        else min(timeout_seconds, 10)
                    )
                    end = time.monotonic() + rollback_timeout
                    bounded_until[0] = end
                    state = adapter.inspect(row)
                    _known(state, "down")
                    _run_plan(adapter.plan(row, "down", state), _run, end)
                    restored = _verify(adapter, row, "down", end, _sleep, state)
                    rollback.append({"id": row["id"], "stopped": True,
                                     "registration_restored": restored.get("registered") == previous.get("registered")})
                except ServiceError:
                    rollback.append({"id": row["id"], "stopped": False})
            failure = {"services": plans, "rollback": rollback}
            if retained_row is not None:
                failure["retained_container"] = receipt["retained_container"]
                failure["hold"] = "authorization_consumed_fresh_preview_required"
            exc.details = _safe(failure, inspect_process_environment=retained_row is None)
            exc.may_have_executed = True
            raise
    return _safe({**receipt, "applied": True}, inspect_process_environment=retained_row is None)
