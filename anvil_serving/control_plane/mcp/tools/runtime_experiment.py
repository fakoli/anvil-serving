"""One owner-managed router candidate, matched fixed probes, and exact restoration.

The durable marker fences every other serving mutation after an interruption.
Recovery executes restoration only: an ambiguous probe is never replayed.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request

from ....paths import config_path
from ..arguments import arg_bool, bounded_int_arg, str_arg
from ..arguments import schema as _schema, bounded_integer_schema as _bounded_integer_schema
from ..catalog import ToolFamily
from ..errors import ToolError, ok


_LOCAL = threading.local()
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_MAX_RECORD = 2 * 1024 * 1024
_FIELDS = frozenset({"action", "run_id", "config", "tier", "alias", "values",
    "expected_baseline_sha256", "compose", "service", "env_file", "container",
    "installed_config", "topology", "topology_overlay", "router_url", "probe_max_tokens",
    "probe_timeout_seconds", "drain_timeout", "dry_run", "confirm", "human_approved"})
_ROUTER_FIELDS = frozenset({"config", "tier", "compose", "service", "env_file", "container",
    "installed_config", "topology", "topology_overlay", "router_url", "drain_timeout"})
_PROMPT = "Reply with the single word READY."
_STEPS = ["baseline_probe", "install_candidate", "candidate_probe", "restore_exact_baseline", "verify_restore"]


def _marker_path():
    return Path(config_path("operations/runtime-experiment-pending.json"))


def assert_no_pending_runtime_experiment():
    """Call under the shared serving lock, before an unrelated mutation starts."""
    if not getattr(_LOCAL, "active", False) and _marker_path().exists():
        raise RuntimeError("runtime experiment recovery is required before another serving mutation")


@contextmanager
def _authority():
    from ....serves import _switch_role_lock

    previous = getattr(_LOCAL, "active", False)
    _LOCAL.active = True
    try:
        with _switch_role_lock("promotion"):
            yield
    finally:
        _LOCAL.active = previous


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _replace(path, raw, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".runtime-experiment-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > _MAX_RECORD:
        raise ToolError("experiment_record_too_large", "runtime experiment record exceeds its bound")
    _replace(path, raw)


def _read(path):
    with open(path, "rb") as handle:
        raw = handle.read(_MAX_RECORD + 1)
    if len(raw) > _MAX_RECORD:
        raise ToolError("experiment_record_invalid", "runtime experiment record exceeds its bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ToolError("experiment_record_invalid", "runtime experiment record is invalid")
    return value


def _record_path(run_id):
    if type(run_id) is not str or not _SAFE_ID.fullmatch(run_id):
        raise ToolError("bad_argument", "run_id must be a bounded stable identifier")
    return Path(config_path("operations/runtime-experiments")) / (_hash(run_id.encode()) + ".json")


def _binding(args):
    from .... import router_manage

    if set(args) - _FIELDS:
        raise ToolError("bad_argument", "unsupported runtime experiment field")
    binding = {key: args[key] for key in _ROUTER_FIELDS if key in args}
    for key in ("config", "tier", "compose", "service", "env_file"):
        binding[key] = str_arg(args, key, required=True)
    binding["alias"] = str_arg(args, "alias", required=True)
    if not _SAFE_ID.fullmatch(binding["alias"]):
        raise ToolError("bad_argument", "alias must be an explicit bounded static route")
    binding["router_url"] = router_manage._safe_router_url(
        str_arg(args, "router_url", router_manage.DEFAULT_ROUTER_URL))
    binding["container"] = str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    binding["installed_config"] = str_arg(args, "installed_config", router_manage.DEFAULT_INSTALLED_CONFIG)
    binding["probe_max_tokens"] = bounded_int_arg(args, "probe_max_tokens", 256, min_value=1, max_value=4096)
    binding["probe_timeout_seconds"] = bounded_int_arg(args, "probe_timeout_seconds", 60, min_value=1, max_value=300)
    binding["drain_timeout"] = bounded_int_arg(args, "drain_timeout", 120, min_value=1, max_value=3600)
    values = args.get("values", {})
    if type(values) is not dict or not values or set(values) - {"max_concurrency", "max_output_tokens"}:
        raise ToolError("bad_argument", "declare at least one supported runtime candidate setting")
    for key, value in values.items():
        limit = 4096 if key == "max_concurrency" else 1048576
        if type(value) is not int or not 1 <= value <= limit:
            raise ToolError("bad_argument", "runtime candidate setting is outside its supported bound")
    binding["values"] = dict(values)
    return binding


def _router_arguments(binding):
    return {key: value for key, value in binding.items() if key in _ROUTER_FIELDS}


def _source(binding):
    from .... import router_manage

    with open(binding["config"], "rb") as handle:
        raw = handle.read(router_manage.MAX_ROUTER_CONFIG_BYTES + 1)
    if len(raw) > router_manage.MAX_ROUTER_CONFIG_BYTES:
        raise ToolError("config_too_large", "router configuration exceeds its bound")
    return raw


def _compose_digest(binding):
    from .... import router_manage

    with open(binding["compose"], "rb") as handle:
        raw = handle.read(router_manage.MAX_COMPOSE_FILE_BYTES + 1)
    if len(raw) > router_manage.MAX_COMPOSE_FILE_BYTES:
        raise ToolError("compose_too_large", "router deployment declaration exceeds its bound")
    return _hash(raw)


def _environment_digest(binding):
    with open(binding["env_file"], "rb") as handle:
        raw = handle.read(262145)
    if len(raw) > 262144:
        raise ToolError("environment_too_large", "explicit runtime environment exceeds its bound")
    return _hash(raw)


def _mount(binding):
    from .router import _run_argv

    result = _run_argv(["docker", "inspect", "--format", "{{json .Mounts}}", binding["container"]],
                       confirm=True, timeout=30)
    rows = json.loads(result.get("stdout", "[]"))
    matches = [row for row in rows if isinstance(row, dict) and row.get("Destination") == binding["installed_config"]]
    if (len(matches) != 1 or matches[0].get("Type") != "bind" or matches[0].get("RW") is not False
            or os.path.realpath(matches[0].get("Source", "")) != os.path.realpath(binding["config"])):
        raise ToolError("experiment_mount_drift", "router configuration bind does not match the retained owner binding")
    return {key: matches[0][key] for key in ("Source", "Destination", "Type", "RW")}


def _admissions(binding):
    from .... import router_manage

    report = router_manage.transition_request("status", router_url=binding["router_url"])
    rows = report.get("tiers", [])
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ToolError("admission_unavailable", "router admissions could not be captured")
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or type(row.get("tier_id")) is not str
                or row.get("state") not in {"admitting", "quiesced"} or row["tier_id"] in result):
            raise ToolError("admission_unavailable", "router admissions are incomplete or ambiguous")
        result[row["tier_id"]] = row["state"]
    return result


def _preview(binding, expected=None):
    from ....router.config import load
    from .router import tool_router_configuration

    config = load(binding["config"])
    tier = config.route_tier(binding["alias"])
    if tier is None or tier.id != binding["tier"] or tier.replicas:
        raise ToolError("experiment_route_unsupported", "experiment requires one static non-replica alias on the declared tier")
    observation = tool_router_configuration({**_router_arguments(binding), "action": "preview",
        "values": binding["values"], **({"expected_baseline_sha256": expected} if expected else {})})["data"]
    if observation["baseline_sha256"] == observation["candidate_sha256"]:
        raise ToolError("experiment_candidate_unchanged", "runtime candidate must change a declared setting")
    return {"kind": "runtime_experiment", "applied": False, "dry_run": True,
        "baseline_sha256": observation["baseline_sha256"], "candidate_sha256": observation["candidate_sha256"],
        "configured": observation["configured"], "alias": binding["alias"],
        "parameters": {"candidate_values": binding["values"], "probe": {"prompt": _PROMPT,
            "max_tokens": binding["probe_max_tokens"], "timeout_seconds": binding["probe_timeout_seconds"], "temperature": 0}},
        "plan": [{"kind": step} for step in _STEPS]}


def _probe(binding):
    """Reuse the managed bounded probe through the exact authenticated router alias."""
    from ....serves import probe_serve
    from .serves import _open_safe_probe_request

    token = os.environ.get("ANVIL_ROUTER_TOKEN", "")
    if not token:
        raise ToolError("router_auth_unavailable", "router authentication is unavailable")
    base = binding["router_url"].rstrip("/")
    endpoint = base + "/v1/chat/completions"
    parsed = urllib.parse.urlsplit(base)

    def routed(request, **kwargs):
        if urllib.parse.urlsplit(request.full_url).path != "/v1/chat/completions":
            raise ValueError("unexpected probe protocol")
        headers = dict(request.header_items())
        headers["Authorization"] = "Bearer " + token
        return _open_safe_probe_request(urllib.request.Request(endpoint, data=request.data,
            headers=headers, method=request.get_method()), **kwargs)

    began = time.monotonic()
    result = probe_serve({"name": "runtime-candidate", "model": binding["alias"], "engine": "sglang",
                         "port": parsed.port or (443 if parsed.scheme == "https" else 80)},
        text=_PROMPT, timeout=binding["probe_timeout_seconds"], max_tokens=binding["probe_max_tokens"], _open=routed)
    incomplete = result.get("incomplete") is True
    passed = (str(result.get("recognized_excerpt", "")).strip().strip(".!,:;").upper() == "READY"
              and result.get("finish_reason") == "stop" and not incomplete)
    return {"passed": passed, "incomplete": incomplete, "finish_reason": result.get("finish_reason"),
        "recognized_characters": result.get("recognized_characters"),
        "elapsed_seconds": round(time.monotonic() - began, 6),
        "request": {"alias": binding["alias"], "prompt": _PROMPT, "max_tokens": binding["probe_max_tokens"],
                    "temperature": 0, "timeout_seconds": binding["probe_timeout_seconds"]}}


def _public(record):
    return {key: record.get(key) for key in ("kind", "run_id", "state", "phase", "baseline_sha256",
        "candidate_sha256", "alias", "parameters", "baseline_result", "candidate_result", "recovery",
        "correctness", "failure", "updated_at")}


def _save(record, phase):
    record.update(phase=phase, updated_at=time.time())
    _write(_record_path(record["run_id"]), record)


def _abandoned_before_probe(record):
    if record.get("state") == "running" and record.get("phase") == "prepared":
        return {**record, "state": "failed", "phase": "abandoned_before_probe", "correctness": "incomplete",
            "failure": "experiment_abandoned_before_probe", "recovery": {"status": "not_attempted",
                "message": "The operation ended before a probe or runtime change."}}
    return record


def _restore(record):
    from .... import router_manage

    binding = record["binding"]
    baseline = base64.b64decode(record["baseline_bytes"], validate=True)
    if _hash(baseline) != record["baseline_sha256"]:
        raise ToolError("experiment_baseline_invalid", "saved runtime baseline is invalid")
    current = _hash(_source(binding))
    if _compose_digest(binding) != record["compose_sha256"]:
        raise ToolError("experiment_restore_drift", "router deployment declaration changed; automatic restoration refused")
    if _environment_digest(binding) != record["environment_sha256"]:
        raise ToolError("experiment_restore_drift", "runtime environment changed; automatic restoration refused")
    container_state = router_manage.docker_state(binding["container"])
    if container_state != "absent" and _mount(binding) != record["mount"]:
        raise ToolError("experiment_mount_drift", "router configuration mount changed; automatic restoration refused")
    try:
        installed = router_manage.installed_fleet_status(container=binding["container"],
            installed_config=binding["installed_config"], timeout=4).get("config_sha256")
    except (ValueError, ToolError):
        # An interrupted recreate can leave the owned router unavailable. The
        # retained exact Compose declaration and source revision still bind the
        # managed restoration; unavailable runtime is never a successful check.
        installed = None
    allowed = {record["baseline_sha256"], record["candidate_sha256"]}
    if current not in allowed or (installed is not None and installed not in allowed):
        raise ToolError("experiment_restore_drift", "runtime changed outside the retained experiment; automatic restoration refused")
    _save(record, "restoring")
    if current != record["baseline_sha256"] or installed != record["baseline_sha256"]:
        def install_baseline(_snapshot):
            _replace(binding["config"], baseline, record["baseline_mode"])
            return router_manage.cmd_up(binding["compose"], binding["service"], env_file=binding["env_file"],
                                       recreate=True, container=binding["container"])

        if container_state in {"absent", "exited", "created", "dead"}:
            if install_baseline(None) != 0:
                raise ToolError("experiment_restore_failed", "previous router runtime could not be recreated")
        else:
            # The authority lock excludes lifecycle writers, not live requests.
            # Drain through the canonical owner transaction before recreating.
            with tempfile.NamedTemporaryFile(prefix="runtime-experiment-restore-", suffix=".toml") as snapshot:
                snapshot.write(baseline)
                snapshot.flush()
                router_manage.install_config(snapshot.name, topology_path=binding.get("topology"),
                    topology_overlay_path=binding.get("topology_overlay"), router_url=binding["router_url"],
                    drain_timeout=binding["drain_timeout"], confirm=True, dry_run=False, _install=install_baseline)
    verified = router_manage.installed_fleet_status(container=binding["container"],
        installed_config=binding["installed_config"], timeout=4)
    if verified.get("config_sha256") != record["baseline_sha256"] or _hash(_source(binding)) != record["baseline_sha256"]:
        raise ToolError("experiment_restore_failed", "previous router revision could not be verified")
    current_admissions = _admissions(binding)
    if set(current_admissions) != set(record["original_admissions"]):
        raise ToolError("experiment_restore_drift", "router tier identity changed during the experiment")
    for tier, state in record["original_admissions"].items():
        if current_admissions[tier] != state:
            router_manage.transition_request("readmit" if state == "admitting" else "quiesce",
                tier_id=tier, router_url=binding["router_url"], confirm=True, dry_run=False)
    if _admissions(binding) != record["original_admissions"]:
        raise ToolError("experiment_restore_failed", "previous admission states could not be verified")
    record["recovery"] = {"status": "succeeded", "baseline_sha256": record["baseline_sha256"],
                          "runtime_verified": True, "admissions_verified": True}
    record["state"] = "completed" if record.get("correctness") == "passed" else "failed"
    _save(record, "restored")
    _marker_path().unlink(missing_ok=True)
    _sync_directory(_marker_path().parent)


def tool_runtime_experiment(args):
    """Run or restore a closed candidate; status/preview never creates owner state."""
    action = str_arg(args, "action", required=True)
    if action not in {"status", "preview", "apply", "restore"}:
        raise ToolError("bad_action", "unsupported runtime experiment action")
    binding = _binding(args)
    run_id = args.get("run_id")
    if action in {"status", "restore"} and run_id:
        path = _record_path(run_id)
        try:
            record = _read(path)
        except FileNotFoundError as exc:
            raise ToolError("experiment_not_found", "runtime experiment does not exist") from exc
        if record.get("binding") != binding:
            raise ToolError("experiment_binding_conflict", "runtime experiment binding changed")
        if action == "status":
            if record.get("phase") == "prepared":
                # Distinguish an interrupted marker write from a live writer
                # between the record and marker fsyncs. This read never edits.
                try:
                    with _authority():
                        record = _read(path)
                        marker = _read(_marker_path()) if _marker_path().exists() else None
                        if not marker or marker.get("run_id") != run_id:
                            record = _abandoned_before_probe(record)
                except RuntimeError:
                    pass
            return ok(_public(record))
    elif action in {"status", "preview"}:
        return ok(_preview(binding, args.get("expected_baseline_sha256")))
    if action not in {"apply", "restore"} or not run_id:
        raise ToolError("bad_argument", "mutation requires one exact experiment run_id")
    if (arg_bool(args.get("dry_run"), True, name="dry_run") or
            not arg_bool(args.get("confirm"), False, name="confirm") or
            not arg_bool(args.get("human_approved"), False, name="human_approved")):
        raise ToolError("human_approval_required", "runtime experiment requires the confirmed human gate")
    with _authority():
        path = _record_path(run_id)
        marker = _read(_marker_path()) if _marker_path().exists() else None
        if marker and marker.get("run_id") != run_id:
            raise ToolError("experiment_recovery_required", "restore the pending runtime experiment before another mutation")
        if path.exists():
            record = _read(path)
            if record.get("binding") != binding:
                raise ToolError("experiment_binding_conflict", "runtime experiment binding changed")
            if not marker:
                abandoned = _abandoned_before_probe(record)
                if abandoned is not record:
                    record = abandoned
                    _save(record, record["phase"])
                return ok(_public(record))
            if action != "restore":
                raise ToolError("experiment_recovery_required", "the retained experiment may only be restored; probes will not replay")
        elif action == "restore":
            raise ToolError("experiment_not_found", "runtime experiment does not exist")
        else:
            expected = args.get("expected_baseline_sha256")
            if type(expected) is not str or not _DIGEST.fullmatch(expected):
                raise ToolError("bad_argument", "apply requires the exact preview baseline digest")
            preview = _preview(binding, expected)
            raw = _source(binding)
            if _hash(raw) != expected:
                raise ToolError("config_conflict", "runtime baseline changed after preview")
            admissions = _admissions(binding)
            if admissions.get(binding["tier"]) != "admitting":
                raise ToolError("experiment_tier_unavailable", "the declared experiment tier is not admitting")
            record = {**preview, "run_id": run_id, "binding": binding, "state": "running",
                "baseline_bytes": base64.b64encode(raw).decode(), "baseline_mode": os.stat(binding["config"]).st_mode & 0o777,
                "compose_sha256": _compose_digest(binding),
                "environment_sha256": _environment_digest(binding), "mount": _mount(binding),
                "original_admissions": admissions, "correctness": "incomplete", "baseline_result": None,
                "candidate_result": None, "recovery": {"status": "not_attempted"}}
            _save(record, "prepared")
            _write(_marker_path(), {"run_id": run_id})
        if action == "apply":
            try:
                _save(record, "baseline_probe_started")
                record["baseline_result"] = _probe(binding)
                _save(record, "baseline_probe_finished")
                if record["baseline_result"]["passed"] is not True:
                    record["correctness"] = "incomplete" if record["baseline_result"]["incomplete"] else "failed"
                    raise ToolError("experiment_baseline_failed", "baseline did not pass the fixed correctness probe")
                from .router import tool_router_configuration

                _save(record, "candidate_install_started")
                tool_router_configuration({**_router_arguments(binding), "action": "apply", "values": binding["values"],
                    "expected_baseline_sha256": record["baseline_sha256"], "confirm": True, "dry_run": False,
                    "human_approved": True})
                from .... import router_manage

                candidate_runtime = router_manage.installed_fleet_status(container=binding["container"],
                    installed_config=binding["installed_config"], timeout=4)
                if (candidate_runtime.get("config_sha256") != record["candidate_sha256"]
                        or _hash(_source(binding)) != record["candidate_sha256"]):
                    raise ToolError("experiment_candidate_unverified", "candidate runtime revision was not verified")
                _save(record, "candidate_probe_started")
                record["candidate_result"] = _probe(binding)
                record["correctness"] = ("passed" if record["candidate_result"]["passed"] else
                    "incomplete" if record["candidate_result"]["incomplete"] else "failed")
                _save(record, "candidate_probe_finished")
            except Exception as exc:
                record["failure"] = getattr(exc, "code", "experiment_stage_failed")
                _save(record, "experiment_failed")
        try:
            _restore(record)
        except Exception:
            record["state"] = "manual_recovery_required"
            record["recovery"] = {"status": "failed", "runtime_verified": False}
            _save(record, "restore_failed")
        return ok(_public(record))


FAMILY = ToolFamily(
    name="runtime-experiments",
    tools={
        "runtime_experiment": {
            "description": "Preview, run, inspect, or restore a bounded router runtime candidate with retained comparison and exact recovery.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string", "enum": ["status", "preview", "apply", "restore"]},
                    "config": {"type": "string"}, "tier": {"type": "string"}, "alias": {"type": "string"},
                    "values": {"type": "object", "additionalProperties": False,
                               "properties": {
                                   "max_concurrency": _bounded_integer_schema(1, 4096, 1),
                                   "max_output_tokens": _bounded_integer_schema(1, 1048576, 1),
                               }},
                    "run_id": {"type": "string", "minLength": 1, "maxLength": 128,
                               "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                    "expected_baseline_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                    "topology": {"type": "string"}, "topology_overlay": {"type": "string"},
                    "router_url": {"type": "string"},
                    "container": {"type": "string"}, "installed_config": {"type": "string"},
                    "compose": {"type": "string"}, "service": {"type": "string"},
                    "env_file": {"type": "string"},
                    "probe_max_tokens": _bounded_integer_schema(1, 4096, 256),
                    "probe_timeout_seconds": _bounded_integer_schema(1, 300, 60),
                    "drain_timeout": _bounded_integer_schema(1, 3600, 120),
                    "dry_run": {"type": "boolean"}, "confirm": {"type": "boolean"},
                    "human_approved": {"type": "boolean"},
                }, required=["action", "config", "tier", "alias"],
            ),
            "handler": tool_runtime_experiment,
        },
    },
)
