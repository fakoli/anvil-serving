"""Stopped-router route-authority rebase through a pinned native helper."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import stat
import subprocess
import tomllib

from . import router_manage, router_startup_abort
from . import operator_config
from .observability.dashboard.contracts import strict_json
from .router.container_owner import digest, require
from .router_recovery_status import _native_container


_MAX_CONFIG = router_manage.MAX_ROUTER_CONFIG_BYTES
_OLD_CONFIG_TARGET = "/run/anvil-router-rebase/old.toml"
_HELPER_MODULE = Path(__file__).with_name("router") / "route_updates.py"


def _sha256(value):
    return hashlib.sha256(value).hexdigest()


def _read_config(path, service_uid):
    path = Path(path).absolute()
    operator_config._assert_no_link_components(path, label="router config")
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and not before.st_mode & 0o077
            and before.st_uid in {0, service_uid})
    raw = operator_config._read_bounded(path, max_bytes=_MAX_CONFIG,
                                        expected_identity=operator_config._candidate_identity(before))
    after = path.lstat()
    require(operator_config._candidate_identity(before) == operator_config._candidate_identity(after))
    return path, raw


def _config_mount(rendered, service):
    mounts = rendered["services"][service]["volumes"]
    found = [mount for mount in mounts if mount.get("target") == router_manage.DEFAULT_INSTALLED_CONFIG]
    require(len(found) == 1 and found[0].get("type") == "bind" and found[0].get("read_only") is True)
    return found[0]


def _covering_mount(rendered, service, target):
    choices = []
    for mount in rendered["services"][service]["volumes"]:
        destination = mount.get("target")
        if isinstance(destination, str):
            candidate = PurePosixPath(destination)
            if target.is_relative_to(candidate):
                choices.append((len(candidate.parts), mount))
    return max(choices, default=(0, None), key=lambda item: item[0])[1]


def _actual_mount(rendered, mount):
    result = dict(mount)
    result["destination"] = result.pop("target")
    if result["type"] == "volume":
        declared = rendered.get("volumes", {}).get(result["source"], {})
        require(isinstance(declared, dict) and isinstance(declared.get("name"), str) and declared["name"])
        result["name"] = declared["name"]
    return result


def _helper_program():
    # Fixed helper code only.  The caller supplies no executable source or paths.
    return (
        "import hashlib,json,sys;from pathlib import Path;"
        "import anvil_serving.router.route_updates as module;"
        "request=json.loads(sys.stdin.buffer.readline(8193));"
        "old=Path('/run/anvil-router-rebase/old.toml').read_bytes();"
        f"new=Path({router_manage.DEFAULT_INSTALLED_CONFIG!r}).read_bytes();"
        "assert hashlib.sha256(old).hexdigest()==request['expected_old_config_sha256'];"
        "assert hashlib.sha256(new).hexdigest()==request['expected_new_config_sha256'];"
        "assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()==request['helper_module_sha256'];"
        "result=module.migrate_route_state_base_config(old,new,request['state_path'],"
        "expected_old_config_sha256=request['expected_old_config_sha256'],"
        "expected_new_config_sha256=request['expected_new_config_sha256'],"
        "expected_state_sha256=request['expected_state_sha256'],apply=request['apply']);"
        "print(json.dumps(result,sort_keys=True),flush=True)"
    )


def _run_helper(identity, request, *, _popen=subprocess.Popen):
    process = _popen(["docker", "start", "--attach", "--interactive", identity], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        process.stdin.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        process.stdin.flush()
        require(bool(select.select([process.stdout], [], [], 45)[0]))
        raw = process.stdout.readline(131073)
        require(0 < len(raw) <= 131072 and process.wait(timeout=5) == 0)
        return strict_json(raw)
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _expected_hex(value):
    require(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value))
    return value


def _helper_custody(helper_image_id, stopped_image_id, user, *, _run):
    require(isinstance(helper_image_id, str) and re.fullmatch("sha256:[0-9a-f]{64}", helper_image_id))
    inspected = _run(["docker", "image", "inspect", helper_image_id, stopped_image_id], capture_output=True,
                     text=True, encoding="utf-8", timeout=10)
    require(inspected.returncode == 0 and len(inspected.stdout or "") <= 131072)
    rows = strict_json((inspected.stdout or "").encode())
    require(isinstance(rows, list) and len(rows) == 2 and all(isinstance(row, dict) for row in rows))
    helper = next((row for row in rows if row.get("Id") == helper_image_id), None)
    stopped = next((row for row in rows if row.get("Id") == stopped_image_id), None)
    require(helper is not None and stopped is not None)
    helper_layers = helper.get("RootFS", {}).get("Layers")
    stopped_layers = stopped.get("RootFS", {}).get("Layers")
    require(isinstance(helper_layers, list) and isinstance(stopped_layers, list)
            and all(isinstance(layer, str) and re.fullmatch("sha256:[0-9a-f]{64}", layer)
                    for layer in helper_layers + stopped_layers)
            and len(helper_layers) == len(stopped_layers) + 1 and helper_layers[:-1] == stopped_layers
            and helper.get("Config", {}).get("User") == user)
    return {"helper_image_id": helper_image_id, "stopped_image_id": stopped_image_id,
            "base_layers": stopped_layers, "helper_layer": helper_layers[-1],
            "helper_module_sha256": _sha256(_HELPER_MODULE.read_bytes()), "user": user}


def _validated_inputs(*, old_config, new_config, compose, env_file, startup_abort_config,
                      current_stop_receipt, helper_image_id, _run):
    service, container = router_manage.DEFAULT_SERVICE, router_manage.DEFAULT_CONTAINER
    compose = str(Path(compose).absolute())
    stopped = router_manage._container_incarnation(container, _run=_run, stopped=True)
    abort_declaration, abort_receipt = router_startup_abort.completed_receipt(
        startup_abort_config, expected_stopped=stopped)
    require(abort_declaration["compose"] == compose)
    from . import router_current_stop
    # The original receipt names the predecessor stopped before the failed
    # successor existed.  Do not falsely demand its old Docker identity here.
    current_receipt = router_current_stop.completed_receipt(current_stop_receipt)
    successor = current_receipt["authorization"]["successor"]
    require(successor["compose"] == compose
            and successor["compose_sha256"] == abort_declaration["compose_sha256"]
            and successor["image_id"] == stopped["image_id"] == abort_declaration["expected_image_id"]
            and successor["configuration_revision"] == abort_declaration["configuration_revision"])
    predecessor_compose = current_receipt["preview"]["compose_path"]
    predecessor_raw = operator_config._read_bounded(Path(predecessor_compose),
        max_bytes=router_manage.MAX_COMPOSE_FILE_BYTES)
    require(_sha256(predecessor_raw) == current_receipt["preview"]["compose_sha256"])
    from .router.maintenance import _current_successor
    predecessor_target = {"compose": predecessor_compose,
        "image_id": current_receipt["preview"]["incarnation"]["image_id"],
        "configuration_revision": current_receipt["authorization"]["expected_configuration_revision"]}
    predecessor_rendered = _current_successor(predecessor_target, current_receipt["preview"]["incarnation"],
                                               _run=_run)
    execution_env = (router_manage._compose_execution_env(compose, env_file, environ={"PATH": os.defpath})
                     or {"PATH": os.defpath})
    predecessor_config = _config_mount(predecessor_rendered, service)
    # The exact service UID is authoritative even when the host runs as root.
    inspected_user = _run(["docker", "inspect", "--format", "{{json .Config.User}}", stopped["container_id"]],
                          capture_output=True, text=True, encoding="utf-8", timeout=10)
    require(inspected_user.returncode == 0)
    user = strict_json(inspected_user.stdout)
    require(isinstance(user, str) and re.fullmatch("[1-9][0-9]*:[1-9][0-9]*", user))
    service_uid = int(user.split(":", 1)[0])
    old_path, old_raw = _read_config(old_config, service_uid)
    new_path, new_raw = _read_config(new_config, service_uid)
    require(Path(predecessor_config["source"]).absolute() == old_path)
    expected_old = current_receipt["authorization"]["expected_configuration_revision"]
    require(_sha256(old_raw) == expected_old and _sha256(new_raw) == abort_declaration["configuration_revision"])
    roster = router_manage._offline_compose_roster(compose, service, container, _run=_run,
                                                    env_file=env_file, execution_env=execution_env,
                                                    _owner_state_only=True)
    rendered = roster["rendered"]
    successor_config = _config_mount(rendered, service)
    require(Path(successor_config["source"]).absolute() == new_path)
    selected = rendered["services"][service]
    image = selected.get("image")
    inspected = _run(["docker", "image", "inspect", "--format", "{{.Id}}", image], capture_output=True,
                     text=True, encoding="utf-8", timeout=10)
    require(inspected.returncode == 0 and inspected.stdout.strip() == stopped["image_id"])
    from .router.config import _parse_server_config
    settings = _parse_server_config(tomllib.loads(new_raw.decode()), str(new_path), container_paths=True)
    require(settings.route_state_path is not None)
    state_target = PurePosixPath(settings.route_state_path)
    route_mount = _covering_mount(rendered, service, state_target)
    require(route_mount is not None and route_mount is _covering_mount(rendered, service, PurePosixPath(str(state_target) + ".lock"))
            and route_mount.get("read_only", False) is False and route_mount.get("type") in {"bind", "volume"})
    def read(argv):
        result = _run(argv, capture_output=True, text=True, encoding="utf-8", timeout=10,
                      env=execution_env)
        require(result.returncode == 0 and len(result.stdout or "") <= router_manage.MAX_COMPOSE_FILE_BYTES)
        return result.stdout or ""
    predecessor_mounts = predecessor_rendered["services"][service]["volumes"]
    predecessor_scope = router_manage._owner_storage_scope(predecessor_rendered, predecessor_mounts,
        predecessor_config, {}, read)
    # The sealed predecessor declaration and current stopped successor share only durable owner scope.
    require(all(roster["owner_storage"][key] == predecessor_scope[key]
                for key in ("sources", "directories", "lineages")))
    old_mount = {"type": "bind", "source": str(old_path), "destination": _OLD_CONFIG_TARGET}
    helper_custody = _helper_custody(helper_image_id, stopped["image_id"], user, _run=_run)
    return dict(compose=compose, stopped=stopped, abort_declaration=abort_declaration,
                abort_receipt=abort_receipt, current_stop_receipt=current_receipt,
                old_raw=old_raw, new_raw=new_raw, old_mount=old_mount, new_mount=_actual_mount(rendered, successor_config),
                route_mount=_actual_mount(rendered, route_mount), user=user, helper_custody=helper_custody,
                state_path=str(state_target))


def _plan(preview, values):
    stopped = values["stopped"]
    return {"native": preview, "custody": {
        "helper": values["helper_custody"],
        "stopped": {key: stopped[key] for key in ("daemon_id", "container_id", "image_id", "finished_at")},
        "compose_sha256": values["abort_declaration"]["compose_sha256"],
        "startup_abort_receipt_sha256": digest(values["abort_receipt"]),
        "current_stop_receipt_sha256": digest(values["current_stop_receipt"]),
    }}


@router_manage._serving_authority_mutation
def run(*, old_config, new_config, compose, env_file, startup_abort_config, current_stop_receipt,
        helper_image_id, expected_state_sha256, expected_plan_sha256=None, confirm=False, dry_run=False,
        _run=subprocess.run, _native_run=_run_helper):
    """Preview or apply one receipt-bound stopped-router authority rebase."""
    expected_state_sha256 = _expected_hex(expected_state_sha256)
    require(type(confirm) is bool and type(dry_run) is bool and (confirm != dry_run))
    if confirm:
        expected_plan_sha256 = _expected_hex(expected_plan_sha256)
    elif expected_plan_sha256 is not None:
        expected_plan_sha256 = _expected_hex(expected_plan_sha256)
    values = _validated_inputs(old_config=old_config, new_config=new_config, compose=compose, env_file=env_file,
                               startup_abort_config=startup_abort_config, current_stop_receipt=current_stop_receipt,
                               helper_image_id=helper_image_id, _run=_run)
    request = dict(expected_old_config_sha256=_sha256(values["old_raw"]),
                   expected_new_config_sha256=_sha256(values["new_raw"]),
                   expected_state_sha256=expected_state_sha256, state_path=values["state_path"],
                   helper_module_sha256=values["helper_custody"]["helper_module_sha256"], apply=False)
    with _native_container(values["helper_custody"]["helper_image_id"], values["user"],
                           [(values["old_mount"], True), (values["new_mount"], True), (values["route_mount"], False)],
                           _helper_program(), _run=_run) as (helper, _name, observe):
        preview = _native_run(helper, request)
        require(isinstance(preview, dict) and preview.get("applied") is False
                and isinstance(preview.get("state_sha256"), str)
                and re.fullmatch("[0-9a-f]{64}", preview["state_sha256"]))
        plan = _plan(preview, values)
        plan_sha256 = digest(plan)
        if expected_plan_sha256 is not None:
            require(plan_sha256 == expected_plan_sha256)
        if not confirm:
            observe("exited")
            return {"applied": False, "dry_run": True, "plan": plan, "plan_sha256": plan_sha256}
        after_preview = _validated_inputs(old_config=old_config, new_config=new_config, compose=compose, env_file=env_file,
                                          startup_abort_config=startup_abort_config, current_stop_receipt=current_stop_receipt,
                                          helper_image_id=helper_image_id, _run=_run)
        require(after_preview == values)
        result = _native_run(helper, {**request, "apply": True})
        require(isinstance(result, dict) and result.get("applied") is True)
        final = _validated_inputs(old_config=old_config, new_config=new_config, compose=compose, env_file=env_file,
                                  startup_abort_config=startup_abort_config, current_stop_receipt=current_stop_receipt,
                                  helper_image_id=helper_image_id, _run=_run)
        require(final == values)
        observe("exited")
    return {"applied": True, "dry_run": False, "plan_sha256": plan_sha256, "result": result}
