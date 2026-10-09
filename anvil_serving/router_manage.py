"""Bounded lifecycle and transition controls for the deployed router container."""
import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import envfile, guard
from .paths import config_path, resolve_topology_path, runtime_url
from .serves import docker_state, _serving_authority_mutation
from .transports import _is_safe_controller_ip


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_COMPOSE = os.path.join(REPO, "examples", "primary-node", "docker-compose.yml")
DEFAULT_COMPOSE_PROJECT = "anvil-serving"
DEFAULT_CONTAINER = "anvil-router"
DEFAULT_SERVICE = "router"
DEFAULT_ROUTER_URL = "http://127.0.0.1:8000"
DEFAULT_INSTALLED_CONFIG = "/etc/anvil/config.toml"
TRANSITION_PATH = "/v1/admin/transition"
MAX_ROUTER_CONFIG_BYTES = 1024 * 1024
MAX_COMPOSE_FILE_BYTES = 2 * 1024 * 1024
_COMPOSE_ENV_REFERENCE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")
_CREDENTIAL_ENV_NAME_RE = re.compile(
    r"(?:^|_)(?:API_KEY|AUTHORIZATION|CREDENTIAL|PASSWORD|PRIVATE_KEY|SECRET|TOKEN)(?:$|_)"
)


_RUNTIME_INSTALLED_PROBE_CODE = """
import hashlib
import json
import sys

from anvil_serving import router_manage

with open(sys.argv[1], "rb") as source:
    raw = source.read(1048577)
if len(raw) > 1048576:
    raise ValueError("installed router config exceeds its bound")
report = router_manage.runtime_fleet_status(
    sys.argv[1],
    timeout=float(sys.argv[2]),
)
with open(sys.argv[1], "rb") as source:
    if source.read(1048577) != raw:
        raise ValueError("installed router config changed during observation")
report["config_sha256"] = hashlib.sha256(raw).hexdigest()
print(json.dumps(report, indent=2, sort_keys=True))
raise SystemExit(1 if report["unreachable_aliases"] else 0)
"""


_RUNTIME_CONFIG_PROBE_CODE = """
import json
import os
import sys
import tempfile

from anvil_serving import router_manage

raw = sys.stdin.read()
handle = tempfile.NamedTemporaryFile(
    mode="w",
    encoding="utf-8",
    suffix=".toml",
    delete=False,
)
try:
    handle.write(raw)
    handle.close()
    report = router_manage.runtime_fleet_status(
        handle.name,
        timeout=float(sys.argv[1]),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
finally:
    if not handle.closed:
        handle.close()
    os.unlink(handle.name)
raise SystemExit(1 if report["unreachable_aliases"] else 0)
"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _safe_router_url(value):
    value = runtime_url(value)
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("router_url must be an HTTP(S) URL")
    import ipaddress
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        alias = (os.environ.get("ANVIL_SERVING_LOOPBACK_ALIAS") or "").strip()
        if parsed.hostname != alias:
            raise ValueError(
                "router_url must use a literal private IP address or the declared loopback alias"
            ) from None
        try:
            infos = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
        except OSError:
            raise ValueError("router_url loopback alias could not be resolved") from None
        addresses = {
            ipaddress.ip_address(info[4][0])
            for info in infos
            if info[4]
        }
        if not addresses or any(not _is_safe_controller_ip(item) for item in addresses):
            raise ValueError("router_url loopback alias resolved outside private ranges")
    else:
        if not _is_safe_controller_ip(address):
            raise ValueError("router_url must use a loopback, private, or tailnet address")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("router_url must not contain credentials, query, or fragment")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def transition_request(action, *, tier_id=None, member_id=None, scope="tier", barrier_token=None, timeout=None, router_url=None,
                       confirm=False, dry_run=True, reason="operator", env=None, _open=None):
    if action != "status" and confirm and not dry_run:
        from .serves import _switch_role_lock
        with _switch_role_lock("promotion"):
            return _transition_request(action, tier_id=tier_id, member_id=member_id, scope=scope, barrier_token=barrier_token, timeout=timeout,
                router_url=router_url, confirm=confirm, dry_run=dry_run, reason=reason, env=env, _open=_open)
    return _transition_request(action, tier_id=tier_id, member_id=member_id, scope=scope, barrier_token=barrier_token, timeout=timeout,
        router_url=router_url, confirm=confirm, dry_run=dry_run, reason=reason, env=env, _open=_open)


def _transition_request(action, *, tier_id=None, member_id=None, scope="tier", barrier_token=None, timeout=None, router_url=None,
                        confirm=False, dry_run=True, reason="operator", env=None, _open=None):
    if action not in ("status", "quiesce", "drain", "readmit", "consume", "maintenance-preview", "maintenance-readmit"):
        raise ValueError("unsupported transition action")
    if action.startswith('maintenance-') and scope != 'router':
        raise ValueError('maintenance requires router scope')
    if scope not in {"tier", "router"} or (scope == "router" and (tier_id is not None or member_id is not None)):
        raise ValueError("router scope excludes tier/member")
    if action == "consume" and scope != "router":
        raise ValueError("consume requires router scope")
    if scope == "router" and action in {"drain", "readmit", "consume"} and (type(barrier_token) is not str or not re.fullmatch("[0-9a-f]{64}", barrier_token)):
        raise ValueError("router barrier token is required")
    if action != "status" and scope != "router" and not tier_id:
        raise ValueError("tier_id is required")
    if member_id is not None:
        from .router.config import _REPLICA_ID_RE

        if type(member_id) is not str or not _REPLICA_ID_RE.fullmatch(member_id):
            raise ValueError("member_id must be a safe replica member ID")
        if type(tier_id) is not str or not tier_id:
            raise ValueError("tier_id is required for member transitions")
    base = _safe_router_url(router_url or (env or os.environ).get("ANVIL_ROUTER_URL") or DEFAULT_ROUTER_URL)
    preview = action in ("quiesce", "readmit") and (not confirm or dry_run)
    if preview and member_id is None and scope != "router":
        return {"applied": False, "dry_run": True, "action": action, "tier_id": tier_id, "router_url": base}
    token = (env or os.environ).get("ANVIL_ROUTER_TOKEN") or ""
    if not token:
        raise ValueError("ANVIL_ROUTER_TOKEN is required")
    headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
    if action == "status":
        target = {"scope":"router"} if scope == "router" else {"tier_id": tier_id} if tier_id else {}
        if member_id is not None:
            target["member_id"] = member_id
        suffix = "?" + urllib.parse.urlencode(target) if target else ""
        request = urllib.request.Request(base + TRANSITION_PATH + suffix, headers=headers)
        request_timeout = 5.0
    else:
        body = {"action": action, "tier_id": tier_id, "confirm": bool(confirm), "dry_run": bool(dry_run), "reason": reason}
        if scope == "router":
            body.pop("tier_id")
            body["scope"] = "router"
            if barrier_token is not None:
                body["barrier_token"] = barrier_token
        if member_id is not None:
            body["member_id"] = member_id
            if preview:
                body["dry_run"] = True
        request_timeout = 5.0
        if action == "drain":
            if scope == "router":
                timeout = 30 if timeout is None else timeout
                if type(timeout) is not int or not 1 <= timeout <= 900:
                    raise ValueError("router timeout must be an integer between 1 and 900")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
                raise ValueError("timeout must be between 0 and 3600 seconds")
            body["timeout"] = timeout if scope == "router" else float(timeout)
            request_timeout += float(timeout)
        headers["Content-Type"] = "application/json"
        request = urllib.request.Request(base + TRANSITION_PATH, data=json.dumps(body).encode(), headers=headers, method="POST")
    opener = _open or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open
    try:
        with opener(request, timeout=request_timeout) as response:
            raw = response.read(256 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError("router transition request failed with HTTP %s" % exc.code) from None
    except Exception as exc:
        raise ValueError("router transition transport failed (%s)" % type(exc).__name__) from None
    if len(raw) > 256 * 1024:
        raise ValueError("router transition response was oversized")
    try:
        from .observability.dashboard.contracts import strict_json
        result = strict_json(raw)
    except ValueError:
        raise ValueError("router transition response was malformed") from None
    if not isinstance(result, dict):
        raise ValueError("router transition response was malformed")
    if member_id is not None and preview:
        expected = {"applied": False, "dry_run": True, "action": action, "tier_id": tier_id, "member_id": member_id}
        if result != expected or result.get("applied") is not False or result.get("dry_run") is not True:
            raise ValueError("router member transition preview was malformed")
        return expected
    return result


def default_compose_candidates():
    return [config_path("docker-compose.yml"), DEFAULT_COMPOSE]


def resolve_compose_path(path=None):
    if path:
        return os.path.abspath(os.path.expanduser(path))
    return next(
        (
            os.path.abspath(os.path.expanduser(candidate))
            for candidate in default_compose_candidates()
            if os.path.isfile(os.path.expanduser(candidate))
        ),
        os.path.abspath(os.path.expanduser(DEFAULT_COMPOSE)),
    )


def _run_argv(argv, _run, *, dry_run=False, env=None):
    if dry_run:
        return 0
    try:
        kwargs = {"capture_output": True, "text": True}
        if env is not None:
            kwargs["env"] = env
        result = _run(argv, **kwargs)
    except FileNotFoundError:
        print("docker not available", file=sys.stderr)
        return 1
    if result.returncode:
        print((result.stderr or result.stdout or "docker command failed").strip(), file=sys.stderr)
        return 1
    return 0


def _default_env_file():
    for path in (config_path(".env"), os.path.join(os.path.expanduser("~"), ".anvil_env"), os.path.join(os.path.expanduser("~"), ".env")):
        if os.path.isfile(path):
            return path
    return None


def resolve_env_file(path=None):
    selected = path if path is not None else _default_env_file()
    return None if selected is None else os.path.abspath(os.path.expanduser(selected))


def _compose_argv(compose, *, env_file=None):
    argv = ["docker", "compose", "--project-name", DEFAULT_COMPOSE_PROJECT]
    if env_file:
        argv += ["--env-file", os.path.abspath(os.path.expanduser(env_file))]
    return argv + ["-f", compose]


def _compose_up_argv(compose, service, env_file=None, recreate=False):
    argv = _compose_argv(compose, env_file=env_file)
    argv += ["up", "-d", "--no-deps"]
    if recreate:
        argv.append("--force-recreate")
    return argv + [service]


def _compose_execution_env(compose, env_file, *, environ=None):
    """Make file-backed Compose credentials authoritative without exposing them."""
    if not env_file:
        return None
    try:
        with open(compose, "rb") as handle:
            raw = handle.read(MAX_COMPOSE_FILE_BYTES + 1)
    except OSError as exc:
        raise ValueError("could not inspect router Compose file") from exc
    if len(raw) > MAX_COMPOSE_FILE_BYTES:
        raise ValueError("router Compose file exceeds the 2 MiB limit")
    try:
        referenced = set(_COMPOSE_ENV_REFERENCE_RE.findall(raw.decode("utf-8")))
        file_values = envfile.read_dotenv(env_file)
    except (OSError, UnicodeError) as exc:
        raise ValueError("could not inspect router Compose environment") from exc

    execution_env = dict(os.environ if environ is None else environ)
    for name in sorted(referenced & set(file_values)):
        if _CREDENTIAL_ENV_NAME_RE.search(name.upper()):
            execution_env[name] = file_values[name]
    return execution_env


def _container_compose_project(container, _run=subprocess.run):
    state = docker_state(container, _run=_run)
    if state in {"absent", "error"}:
        return state, None
    result = _run(
        [
            "docker",
            "inspect",
            "--format",
            '{{ index .Config.Labels "com.docker.compose.project" }}',
            container,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        return "error", None
    return state, (result.stdout or "").strip() or None


@_serving_authority_mutation
def cmd_up(
    compose,
    service,
    env_file=None,
    dry_run=False,
    _run=subprocess.run,
    recreate=False,
    container=DEFAULT_CONTAINER,
    environ=None,
):
    try:
        execution_env = _compose_execution_env(
            compose,
            env_file,
            environ=environ,
        )
    except ValueError as exc:
        print("cannot prepare router Compose environment: %s" % exc, file=sys.stderr)
        return 1
    state, observed_project = _container_compose_project(container, _run=_run)
    if state == "error":
        print("cannot determine router Compose ownership", file=sys.stderr)
        return 1
    if state != "absent" and observed_project != DEFAULT_COMPOSE_PROJECT:
        owner = observed_project or "none"
        if not recreate:
            print(
                "router container %s belongs to Compose project %r, expected %r; "
                "rerun `anvil-serving router up --recreate` to reconcile ownership"
                % (container, owner, DEFAULT_COMPOSE_PROJECT),
                file=sys.stderr,
            )
            return 1
    if not dry_run:
        try:
            if state in {'absent', 'exited', 'created'}:
                managed = _compose_owner_backend(compose, service, _run=_run, env_file=env_file,
                                                   execution_env=execution_env) == 'managed-container'
                if managed and state != 'absent' and observed_project != DEFAULT_COMPOSE_PROJECT:
                    raise ValueError('router_managed_target_mismatch')
                if managed and state == 'exited':
                    before = _container_incarnation(container, _run=_run, stopped=True)
                    try:
                        require_router_offline(compose,service,container=container,_run=_run,
                            env_file=env_file,execution_env=execution_env,_legacy_incarnation=before)
                        if before != _container_incarnation(container,_run=_run,stopped=True):
                            raise ValueError('router_legacy_incarnation_changed')
                    except ValueError:
                        _managed_container_custody('dead', compose, service, container, _run=_run,
                                                   env_file=env_file, execution_env=execution_env)
                require_router_offline(compose, service, container=container, _run=_run,
                                       env_file=env_file, execution_env=execution_env)
            else:
                receipt = require_router_drain(container, _run=_run, compose=compose, service=service,
                                               env_file=env_file, execution_env=execution_env,
                                               allow_foreign=recreate)
                managed = receipt.get('owner_backend', 'native-process') == 'managed-container'
                if managed:
                    if observed_project != DEFAULT_COMPOSE_PROJECT:
                        raise ValueError('router_managed_target_mismatch')
                    if _run_argv(['docker', 'stop', container], _run):
                        return 1
                    _managed_container_custody('dead', compose, service, container, _run=_run,
                                               env_file=env_file, execution_env=execution_env)
        except (ValueError, OSError, subprocess.SubprocessError):
            print("router lifecycle HOLD: native drain or exclusive offline custody is required", file=sys.stderr)
            return 1
    if state != "absent" and observed_project != DEFAULT_COMPOSE_PROJECT and not dry_run:
        remove_rc = _run_argv(["docker", "rm", "-f", container], _run)
        if remove_rc:
            return remove_rc
    rc = _run_argv(_compose_up_argv(compose, service, env_file=env_file, recreate=recreate),
                   _run, dry_run=dry_run, env=execution_env)
    if rc == 0 and not dry_run and managed:
        try:
            _managed_container_custody('live', compose, service, container, _run=_run,
                                       env_file=env_file, execution_env=execution_env)
        except (ValueError, OSError, subprocess.SubprocessError):
            print('router lifecycle HOLD: new owner remains closed without finalized native custody', file=sys.stderr)
            return 1
    return rc



@_serving_authority_mutation
def cmd_down(compose, service, dry_run=False, _run=subprocess.run):
    if not dry_run:
        try:
            receipt = require_router_drain(DEFAULT_CONTAINER, _run=_run, compose=compose, service=service)
            managed = receipt.get('owner_backend', 'native-process') == 'managed-container'
        except ValueError:
            print("router lifecycle HOLD: old runtime all-path owned drain is required", file=sys.stderr)
            return 1
    rc = _run_argv([*_compose_argv(compose), "stop", service], _run, dry_run=dry_run)
    if rc == 0 and not dry_run and managed:
        try:
            _managed_container_custody('dead', compose, service, DEFAULT_CONTAINER, _run=_run)
        except (ValueError, OSError, subprocess.SubprocessError):
            print('router lifecycle HOLD: stopped owner lacks finalized native custody', file=sys.stderr)
            return 1
    return rc



@_serving_authority_mutation
def cmd_restart(container, dry_run=False, verify=True, _run=subprocess.run, _sleep=None,
                compose=None, service=DEFAULT_SERVICE, env_file=None):
    if not dry_run:
        try:
            receipt = require_router_drain(container, _run=_run)
            managed = receipt.get('owner_backend', 'native-process') == 'managed-container'
            if managed:
                compose = resolve_compose_path(compose)
                execution_env = _compose_execution_env(compose, env_file)
                if _run_argv(['docker', 'stop', container], _run):
                    return 1
                _managed_container_custody('dead', compose, service, container, _run=_run,
                                           env_file=env_file, execution_env=execution_env)
                if _run_argv(['docker', 'start', container], _run):
                    return 1
                _managed_container_custody('live', compose, service, container, _run=_run,
                                           env_file=env_file, execution_env=execution_env)
                return 0
        except (ValueError, OSError, subprocess.SubprocessError):
            print("router lifecycle HOLD: old runtime all-path owned drain is required", file=sys.stderr)
            return 1
    return _run_argv(["docker", "restart", container], _run, dry_run=dry_run)


@_serving_authority_mutation
def cmd_reload(container, dry_run=False, verify=True, _run=subprocess.run, _sleep=None, **kwargs):
    print("router reload restarts the container because configuration is startup-read")
    return cmd_restart(container, dry_run=dry_run, verify=verify, _run=_run, _sleep=_sleep, **kwargs)


def lifecycle_plan(action, *, compose=None, service=DEFAULT_SERVICE, env_file=None,
                   container=DEFAULT_CONTAINER, recreate=False):
    """Resolve one router lifecycle operation without invoking Docker."""
    if action not in {"up", "down", "restart", "reload"}:
        raise ValueError("unsupported lifecycle action")
    if recreate and action != "up":
        raise ValueError("recreate is only supported for router up")

    plan = {
        "action": action,
        "compose": None,
        "compose_project": None,
        "env_file": None,
        "service": None,
        "container": container,
        "recreate": bool(recreate),
    }
    if action in {"up", "down"}:
        selected_compose = resolve_compose_path(compose)
        plan["compose"] = selected_compose
        plan["compose_project"] = DEFAULT_COMPOSE_PROJECT
        plan["service"] = service
        if action == "up":
            selected_env_file = resolve_env_file(env_file)
            plan["env_file"] = selected_env_file
            plan["command"] = _compose_up_argv(
                selected_compose,
                service,
                env_file=selected_env_file,
                recreate=recreate,
            )
        else:
            plan["command"] = [*_compose_argv(selected_compose), "stop", service]
    else:
        plan["command"] = ["docker", "restart", container]
    return plan


def cmd_logs(container, tail="200", since=None, follow=False, _run=subprocess.run):
    if docker_state(container, _run=_run) != "running":
        print("cannot read logs: router is not running", file=sys.stderr)
        return 1
    argv = ["docker", "logs", "--tail", str(tail)]
    if since:
        argv += ["--since", since]
    if follow:
        argv.append("--follow")
    argv.append(container)
    result = _run(argv) if follow else _run(argv, capture_output=True, text=True)
    if not follow:
        sys.stdout.write(result.stdout or "")
        sys.stderr.write(result.stderr or "")
    return result.returncode


def _health(_open, port=8000):
    url = runtime_url("http://127.0.0.1:%s/" % port)
    try:
        with _open(url, timeout=3) as response:
            return getattr(response, "status", None) or response.getcode()
    except Exception:
        return None


# Inspect only restart-custody metadata: never request Config.Env, command arguments,
# arbitrary labels, mount contents, or Docker's full container document.
_CUSTODY_FORMAT = (
    '{"container_id":{{json .Id}},"image_id":{{json .Image}},'
    '"image_reference":{{json .Config.Image}},"started_at":{{json .State.StartedAt}},'
    '"restart_count":{{json .RestartCount}},'
    '"compose_project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"compose_service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"mounts":{{json .Mounts}}}'
)


def _restart_custody(container, _run):
    """Read an allowlisted observation, not an ownership or restart authorization."""
    unavailable = {"available": False, "error": "router_custody_unavailable"}
    if not _CONTAINER_NAME_RE.fullmatch(container):
        return unavailable
    try:
        result = _run(["docker", "inspect", "--format", _CUSTODY_FORMAT, container],
                      capture_output=True, text=True, encoding="utf-8", timeout=5)
        raw = result.stdout or ""
        if result.returncode or len(raw) > 131072:
            return unavailable
        observed = json.loads(raw)
        if not isinstance(observed, dict):
            return unavailable
        fields = ("container_id", "image_id", "image_reference", "started_at")
        if any(not isinstance(observed.get(key), str) or len(observed[key]) > 4096
               or any(ord(ch) < 32 for ch in observed[key]) for key in fields):
            return unavailable
        if not re.fullmatch(r"[a-f0-9]{64}", observed["container_id"]) or not re.fullmatch(
            r"sha256:[a-f0-9]{64}", observed["image_id"]
        ):
            return unavailable
        reference = observed["image_reference"]
        if not reference or reference.startswith("-"):
            return unavailable
        if type(observed.get("restart_count")) is not int or observed["restart_count"] < 0:
            return unavailable
        mounts = observed.get("mounts")
        if not isinstance(mounts, list) or len(mounts) > 128:
            return unavailable
        projected = []
        for mount in mounts:
            if not isinstance(mount, dict) or type(mount.get("RW")) is not bool:
                return unavailable
            row = {}
            for field in ("Type", "Name", "Source", "Destination"):
                value = mount.get(field, "")
                if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
                    return unavailable
                row[field.lower()] = value
            row["read_only"] = not mount["RW"]
            projected.append(row)
        custody = {key: observed[key] for key in fields}
        for label in ("compose_project", "compose_service"):
            value = observed.get(label)
            if value is not None and (not isinstance(value, str) or len(value) > 4096
                                      or any(ord(c) < 32 for c in value)):
                return unavailable
            custody[label] = value
        custody.update(available=True, restart_count=observed["restart_count"], mounts=projected,
                       local_reference_image_id=None, local_reference_matches=None)
        reference_result = _run(["docker", "image", "inspect", "--format", "{{.Id}}", reference],
                                capture_output=True, text=True, encoding="utf-8", timeout=5)
        reference_id = (reference_result.stdout or "").strip()
        if reference_result.returncode == 0 and re.fullmatch(r"sha256:[a-f0-9]{64}", reference_id):
            custody["local_reference_image_id"] = reference_id
            custody["local_reference_matches"] = reference_id == custody["image_id"]
        return custody
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        # Docker errors can contain private arguments or daemon diagnostics. The
        # observation is unavailable; preserve the pre-existing health result.
        return unavailable


def status_summary(container, _run=subprocess.run, _open=urllib.request.urlopen, port=8000):
    state = docker_state(container, _run=_run)
    running = state == "running"
    return {"container": container, "docker_state": state, "running": running,
            "health_status": _health(_open, port) if running else None,
            "health_url": runtime_url("http://127.0.0.1:%s/" % port) if running else None,
            "ok": state != "error",
            "custody": (_restart_custody(container, _run) if state not in {"absent", "error"}
                        else {"available": False, "error": "router_custody_unavailable"})}


def cmd_status(container, _run=subprocess.run, _open=urllib.request.urlopen):
    summary = status_summary(container, _run=_run, _open=_open)
    print("router container: %s" % container)
    print("docker state:     %s" % summary["docker_state"])
    if summary["docker_state"] == "error":
        print("status:           UNKNOWN (docker unavailable)")
        return 1
    return 0


def cmd_token(container, *, reveal=False, _run=subprocess.run):
    if docker_state(container, _run=_run) != "running":
        print("cannot read token: router is not running", file=sys.stderr)
        return 1
    result = _run(["docker", "exec", container, "printenv", "ANVIL_ROUTER_TOKEN"], capture_output=True, text=True)
    token = (result.stdout or "").strip()
    if not token:
        print("auth is UNSET")
        return 0
    if reveal and guard.confirm("Reveal the deployed router bearer token?"):
        print(token)
    else:
        print("auth is SET")
    return 0


@_serving_authority_mutation
def install_config(
    config_file,
    *,
    topology_path=None,
    topology_overlay_path=None,
    router_url=None,
    drain_timeout=120,
    confirm=False,
    dry_run=True,
    _transition=transition_request,
    _install=None,
    _sleep=time.sleep,
):
    """Safely replace a deployed router config even when its tier set changes.

    The old owner must close, drain and consume its complete barrier before
    any installer callback may replace source or configuration. A successor
    stays closed until its changed ownership can be reviewed.
    """
    from .router.topology_validation import load_validated_router_snapshot
    from .serves import _install_router_config

    # Capture one immutable, topology-joined artifact before any status read or
    # lifecycle mutation.  The installer receives this same object, so a later
    # path replacement cannot change what the deployed validator or writer see.
    snapshot = load_validated_router_snapshot(
        config_file,
        resolve_topology_path(topology_path),
        (
            resolve_topology_path(topology_overlay_path)
            if topology_overlay_path is not None
            else None
        ),
    )
    desired = [tier.id for tier in snapshot.config.tiers]
    status = _transition("status", router_url=router_url)
    rows = status.get("tiers", [])
    if not isinstance(rows, list):
        raise ValueError("router transition status was malformed")
    current = [
        row.get("tier_id") for row in rows
        if isinstance(row, dict) and isinstance(row.get("tier_id"), str)
    ]
    plan = {
        "config_sha256": snapshot.config_sha256,
        "current_tiers": current,
        "desired_tiers": desired,
        "drain_timeout": drain_timeout,
    }
    if dry_run or not confirm:
        return {"applied": False, "dry_run": True, **plan}

    closure = _transition("quiesce", scope="router", router_url=router_url,
                          confirm=True, dry_run=False).get("result", {})
    barrier = closure.get("barrier_token")
    if (closure.get("durable") is not True or closure.get("state") != "quiesced"
            or type(barrier) is not str or re.fullmatch("[0-9a-f]{64}", barrier) is None):
        raise ValueError("router old-owner all-path barrier is unavailable")
    drained = _transition("drain", scope="router", router_url=router_url,
                          barrier_token=barrier, timeout=drain_timeout).get("result", {})
    counts = drained.get("counts")
    if (drained.get("drained") is not True or drained.get("unknown") != []
            or type(counts) is not dict
            or set(counts) != {"chat", "purpose", "audio", "memory", "media", "internal", "delivery", "maintenance"}
            or any(type(v) is not int or v != 0 for v in counts.values())
            or any(drained.get(k) != closure.get(k) for k in ("configuration_revision", "roster_revision", "generation"))):
        raise ValueError("router owned-work drain remains held")
    consumed = _transition("consume", scope="router", router_url=router_url,
                           barrier_token=barrier, confirm=True, dry_run=False).get("result", {})
    if (consumed.get("drained") is not True or consumed.get("cutover_pending") is not True
            or any(consumed.get(k) != closure.get(k) for k in ("configuration_revision", "roster_revision", "generation"))):
        raise ValueError("router barrier consumption is unverified")

    installer = _install or _install_router_config
    if installer(snapshot) != 0:
        raise ValueError("router config install held or failed; admission remains closed")
    deadline = time.monotonic() + 60
    while True:
        try:
            post = _transition("status", router_url=router_url)
            post_rows = post.get("tiers", [])
            if not isinstance(post_rows, list):
                raise ValueError("router transition status was malformed")
            tier_ids = []
            for row in post_rows:
                if not isinstance(row, dict) or not isinstance(row.get("tier_id"), str):
                    raise ValueError("router transition status was malformed")
                tier_ids.append(row["tier_id"])
            if len(tier_ids) == len(set(tier_ids)) and set(tier_ids) == set(desired):
                closed = _transition("status", scope="router", router_url=router_url).get("result", {})
                if (closed.get("state") != "quiesced" or closed.get("durable") is not True
                        or closed.get("configuration_revision") != snapshot.config_sha256):
                    raise ValueError("installed router closure is unverified")
                return {"applied":True, "dry_run":False, "tier_status":post_rows,
                        "unavailable_tiers":[tid for tid,row in zip(tier_ids,post_rows) if row.get("ready") is not True],
                        "readmitted_tiers":[], "admission":"quiesced",
                        "readmission_hold":"successor_owner_transfer_required", **plan}
        except ValueError:
            pass
        if time.monotonic() >= deadline:
            raise ValueError("installed router config did not expose the desired tier set and durable closure; admission remains closed")
        _sleep(1)


def _build_parser():
    parser = argparse.ArgumentParser(prog="anvil-serving router")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("up", "down"):
        item = actions.add_parser(name)
        item.add_argument("--compose")
        item.add_argument("--service", default=DEFAULT_SERVICE)
        item.add_argument("--dry-run", action="store_true")
        if name == "up":
            item.add_argument("--env-file")
            item.add_argument("--recreate", action="store_true")
    fleet = actions.add_parser("fleet-status")
    fleet_source = fleet.add_mutually_exclusive_group()
    fleet_source.add_argument(
        "--config",
        help="inspect one router config file from the selected probe perspective.",
    )
    fleet_source.add_argument(
        "--live",
        action="store_true",
        help="probe the installed config from inside the live router runtime (default).",
    )
    fleet.add_argument("--container", default=DEFAULT_CONTAINER)
    fleet.add_argument("--installed-config", default=DEFAULT_INSTALLED_CONFIG)
    fleet.add_argument(
        "--probe-perspective",
        choices=("command-host", "router-runtime"),
        help="execution perspective for explicit --config inspection.",
    )
    fleet.add_argument("--json", action="store_true", dest="json_out",
                       help="emit the report as JSON for tooling.")
    fleet.add_argument("--timeout", type=float, default=4.0,
                       help="per-endpoint probe timeout in seconds (default: 4).")
    for name in ("restart", "reload"):
        item = actions.add_parser(name)
        item.add_argument("--container", default=DEFAULT_CONTAINER)
        item.add_argument("--dry-run", action="store_true")
        item.add_argument("--no-verify", action="store_true")
        item.add_argument("--compose")
        item.add_argument("--service", default=DEFAULT_SERVICE)
        item.add_argument("--env-file")
    for name in ("status", "token", "logs"):
        item = actions.add_parser(name)
        item.add_argument("--container", default=DEFAULT_CONTAINER)
        if name == "token": item.add_argument("--reveal", action="store_true")
        if name == "logs":
            item.add_argument("--tail", default="200")
            item.add_argument("--since")
            item.add_argument("--follow", action="store_true")
    for action in ("transition-status", "quiesce", "drain", "readmit"):
        item = actions.add_parser(action)
        item.add_argument("--tier")
        item.add_argument("--scope", choices=("tier", "router"), default="tier")
        item.add_argument("--barrier-token")
        item.add_argument("--member", help="optional declared replica member; requires --tier")
        item.add_argument("--router-url")
        if action == "drain":
            item.add_argument("--timeout", type=lambda value: int(value) if value.isdecimal() else float(value), default=30)
        if action in ("quiesce", "readmit"):
            item.add_argument("--confirm", action="store_true")
            item.add_argument("--dry-run", action="store_true")
        if action == "quiesce":
            item.add_argument(
                "--reason",
                default="operator",
                help="content-free quiesce reason code (e.g. promotion, eviction)",
            )
    install = actions.add_parser("install-config")
    install.add_argument("--config", required=True)
    install.add_argument("--topology")
    install.add_argument("--topology-overlay")
    install.add_argument("--router-url")
    install.add_argument("--drain-timeout", type=int, default=120)
    install.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None):
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)
    if args.action in {"up", "down", "restart", "reload"}:
        plan = lifecycle_plan(
            args.action,
            compose=getattr(args, "compose", None),
            service=getattr(args, "service", DEFAULT_SERVICE),
            env_file=getattr(args, "env_file", None),
            container=getattr(args, "container", DEFAULT_CONTAINER),
            recreate=getattr(args, "recreate", False),
        )
        if args.dry_run:
            print(json.dumps({"applied": False, "dry_run": True, **plan}, sort_keys=True))
            return 0
        if args.action == "up":
            rc = cmd_up(
                plan["compose"],
                plan["service"],
                plan["env_file"],
                recreate=plan["recreate"],
                container=plan["container"],
            )
        elif args.action == "down":
            rc = cmd_down(plan["compose"], plan["service"])
        else:
            rc = (cmd_restart if args.action == "restart" else cmd_reload)(
                plan["container"], verify=not args.no_verify, compose=args.compose, service=args.service, env_file=args.env_file
            )
        print(json.dumps({"applied": rc == 0, "dry_run": False, **plan}, sort_keys=True))
        return rc
    if args.action == "status":
        from .operator_output import CommandResult, OperatorError
        summary = status_summary(args.container)
        human = "router container: %s\ndocker state:     %s\n" % (
            args.container, summary["docker_state"])
        error = None
        if summary["docker_state"] == "error":
            human += "status:           UNKNOWN (docker unavailable)\n"
            error = OperatorError("Docker unavailable", code="router_status_unavailable")
        return CommandResult(data=summary, human_stdout=human, error=error)
    if args.action == "fleet-status":
        return cmd_fleet_status(
            args.config,
            as_json=args.json_out,
            timeout=args.timeout,
            live=args.live,
            probe_perspective=args.probe_perspective,
            container=args.container,
            installed_config=args.installed_config,
        )
    if args.action == "install-config":
        confirmed = guard.confirmation_authorized()
        try:
            result = install_config(
                args.config,
                topology_path=args.topology,
                topology_overlay_path=args.topology_overlay,
                router_url=args.router_url,
                drain_timeout=args.drain_timeout,
                confirm=confirmed,
                dry_run=args.dry_run or not confirmed,
            )
        except ValueError as exc:
            code = getattr(exc, "code", None)
            if not isinstance(code, str):
                code = "router_config_install_refused"
            print("router config install failed: %s" % code, file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.action in {"transition-status", "quiesce", "drain", "readmit"}:
        action = "status" if args.action == "transition-status" else args.action
        confirmed = bool(
            getattr(args, "confirm", False) or guard.confirmation_authorized()
        )
        try:
            result = transition_request(
                action,
                tier_id=getattr(args, "tier", None), scope=args.scope, barrier_token=args.barrier_token,
                **({"member_id": args.member} if args.member is not None else {}),
                timeout=getattr(args, "timeout", None),
                router_url=args.router_url,
                confirm=confirmed,
                dry_run=(
                    getattr(args, "dry_run", False) or not confirmed
                    if action in ("quiesce", "readmit") else False
                ),
                reason=getattr(args, "reason", "operator"),
            )
        except ValueError as exc:
            print("router transition failed: %s" % exc, file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        payload = result.get("result", result)
        if action == "drain" and isinstance(payload, dict) and not payload.get("drained", False):
            return 1
        if action == "status":
            rows = result.get("tiers", [])
            if isinstance(rows, list) and any(
                isinstance(row, dict) and row.get("ready") is False for row in rows
            ):
                return 1
        if action == "readmit" and isinstance(payload, dict) and payload.get("readmitted") is False:
            return 1
        return 0
    if args.action == "logs": return cmd_logs(args.container, args.tail, args.since, args.follow)
    return cmd_token(args.container, reveal=args.reveal)


# --- fleet status -----------------------------------------------------------
# Feature 3 of docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md. On 2026-08-08 the router
# advertised three voice/audio routes whose backing serves had been off for
# hours and nothing anywhere said so. Answering "is every configured capability
# actually served" required SSH to another host.

def _probe_endpoint(url, timeout=4.0, _open=urllib.request.urlopen):
    """Return (reachable, detail) for one endpoint. Never raises."""
    try:
        with _open(url, timeout=timeout) as response:
            code = getattr(response, "status", None) or response.getcode()
            return True, "HTTP %s" % code
    except urllib.error.HTTPError as exc:
        # An authenticated endpoint answering 401/403 is reachable and serving;
        # only a transport failure means "nothing is there".
        return True, "HTTP %s" % exc.code
    except Exception as exc:  # noqa: BLE001 - any transport failure is "down"
        return False, type(exc).__name__


# The router runs in a container, so its config names the Docker host as
# `host.docker.internal`. That name does not resolve on the host itself, so
# probing it from here would report a healthy serve as unreachable. Translating
# it to the host-relative loopback address is faithful -- it is the same
# machine -- and the translation is reported so it is never silent.
# CLAUDE.md: 127.0.0.1 is host-relative; never substitute `localhost`.
_DOCKER_HOST_ALIAS = "host.docker.internal"
_HOST_RELATIVE_LOOPBACK = "127.0.0.1"
_CONTAINER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def _validated_probe_timeout(timeout):
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < float(timeout) <= 60
    ):
        raise ValueError("probe timeout must be greater than 0 and at most 60 seconds")
    return float(timeout)


def _probe_url_for_perspective(url, perspective):
    """Resolve one health URL for the declared probe execution perspective."""
    if perspective not in {"command-host", "router-runtime"}:
        raise ValueError("probe perspective must be command-host or router-runtime")
    parsed = urllib.parse.urlparse(url)
    if (
        perspective == "router-runtime"
        or (parsed.hostname or "").lower() != _DOCKER_HOST_ALIAS
    ):
        return url, False
    netloc = _HOST_RELATIVE_LOOPBACK
    if parsed.port:
        netloc += ":%d" % parsed.port
    return urllib.parse.urlunparse(parsed._replace(netloc=netloc)), True


def _endpoint_kind(base_url):
    """Classify an endpoint without returning its operator-private identity."""
    try:
        host = urllib.parse.urlparse(base_url).hostname or ""
    except ValueError:
        return "invalid"
    if host.lower() == _DOCKER_HOST_ALIAS:
        return "host-relative-loopback"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "dns"
    if address.is_loopback:
        return "loopback"
    if address.is_private or address in ipaddress.ip_network("100.64.0.0/10"):
        return "private-network"
    return "public-network"


def _failure_class(*, reachable, endpoint_kind, perspective):
    if reachable:
        return None
    if endpoint_kind == "host-relative-loopback" and perspective == "command-host":
        return "probe_perspective_mismatch"
    return "transport_unreachable"


def fleet_status(
    config,
    timeout=4.0,
    _probe=_probe_endpoint,
    *,
    probe_perspective="command-host",
    evidence_source="configured-file",
):
    """Probe every configured capability and report which are actually served.

    Reports aliases (the declared chat vocabulary), purpose models, and audio
    routes. Read-only: no Docker, no mutation, no lifecycle.
    """
    timeout = _validated_probe_timeout(timeout)
    rows = []
    seen = {}

    def _check(base_url, health_path):
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        url += health_path if health_path.startswith("/") else "/" + health_path
        probe_url, translated = _probe_url_for_perspective(url, probe_perspective)
        if probe_url not in seen:
            seen[probe_url] = _probe(probe_url, timeout=timeout)
        ok, detail = seen[probe_url]
        if translated:
            detail += " via host-relative loopback"
        endpoint_kind = _endpoint_kind(base_url)
        return ok, detail, endpoint_kind

    def _row(kind, name, target, base_url, health_path):
        ok, detail, endpoint_kind = _check(base_url, health_path)
        return {
            "kind": kind,
            "name": name,
            "target": target,
            "endpoint_kind": endpoint_kind,
            "probe_perspective": probe_perspective,
            "reachable": ok,
            "detail": detail,
            "failure_class": _failure_class(
                reachable=ok,
                endpoint_kind=endpoint_kind,
                perspective=probe_perspective,
            ),
        }

    for alias, tier_id in sorted(dict(config.model_routes).items()):
        try:
            tier = config.tier(tier_id)
        except Exception:  # noqa: BLE001 - an unresolvable tier is the finding
            rows.append({"kind": "alias", "name": alias, "target": tier_id,
                         "endpoint_kind": "undeclared",
                         "probe_perspective": probe_perspective,
                         "reachable": False,
                         "detail": "alias maps to an undeclared tier",
                         "failure_class": "undeclared_tier"})
            continue
        rows.append(
            _row(
                "alias",
                alias,
                tier_id,
                tier.base_url,
                getattr(tier, "health_path", "/health") or "/health",
            )
        )

    for purpose in getattr(config, "purpose_models", ()) or ():
        rows.append(_row("purpose", purpose.id, purpose.model, purpose.base_url, "/health"))

    for route in getattr(config, "audio_routes", ()) or ():
        rows.append(_row("audio", route.id, route.purpose, route.base_url, "/health"))

    unreachable = [r for r in rows if not r["reachable"]]
    return {
        "rows": rows,
        "checked": len(rows),
        "unreachable": len(unreachable),
        "perspective_mismatches": sum(
            row["failure_class"] == "probe_perspective_mismatch" for row in rows
        ),
        "unreachable_aliases": sorted(
            r["name"] for r in unreachable if r["kind"] == "alias"),
        "probe_perspective": probe_perspective,
        "evidence_source": evidence_source,
    }


def _config_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_fleet_report(stdout):
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        raise ValueError("router-runtime fleet probe returned malformed JSON") from None
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                raise ValueError("router-runtime fleet probe returned malformed data") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise ValueError("router-runtime fleet probe returned malformed report")
    for row in payload["rows"]:
        if not isinstance(row, dict):
            raise ValueError("router-runtime fleet probe returned malformed rows")
        # Fail closed if an older or altered runtime returned endpoint identities.
        row.pop("endpoint", None)
        row.pop("host", None)
    return payload


def runtime_fleet_status(config_file, *, timeout=4.0, _probe=_probe_endpoint):
    """Probe one config directly from the process that owns the router perspective."""
    from .router import config as router_config

    timeout = _validated_probe_timeout(timeout)
    config = router_config.load(config_file)
    return fleet_status(
        config,
        timeout=timeout,
        _probe=_probe,
        probe_perspective="router-runtime",
        evidence_source="configured-file",
    )


def installed_fleet_status(
    *,
    container=DEFAULT_CONTAINER,
    installed_config=DEFAULT_INSTALLED_CONFIG,
    timeout=4.0,
    _run=subprocess.run,
):
    """Probe the installed config from inside the live router runtime."""
    timeout = _validated_probe_timeout(timeout)
    if not isinstance(container, str) or not _CONTAINER_NAME_RE.fullmatch(container):
        raise ValueError("router container name is invalid")
    argv = [
        "docker",
        "exec",
        container,
        "python",
        "-c",
        _RUNTIME_INSTALLED_PROBE_CODE,
        installed_config,
        str(timeout),
    ]
    try:
        result = _run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(600.0, max(30.0, timeout * 64 + 10.0)),
        )
    except FileNotFoundError:
        raise ValueError("router-runtime fleet probe requires Docker") from None
    except subprocess.TimeoutExpired:
        raise ValueError("router-runtime fleet probe exceeded its total timeout") from None
    if result.returncode not in {0, 1}:
        raise ValueError("router-runtime fleet probe failed before producing a report")
    report = _decode_fleet_report(result.stdout)
    report["evidence_source"] = "installed-router"
    report["probe_perspective"] = "router-runtime"
    return report


def configured_fleet_status(
    config_file,
    *,
    container=DEFAULT_CONTAINER,
    timeout=4.0,
    _run=subprocess.run,
):
    """Probe one candidate config from inside the live router runtime.

    The candidate TOML is bounded and supplied on stdin, never copied into the
    container or exposed in process arguments.  The runtime uses a short-lived
    temporary file only because the installed config loader accepts a path.
    """
    timeout = _validated_probe_timeout(timeout)
    if not isinstance(container, str) or not _CONTAINER_NAME_RE.fullmatch(container):
        raise ValueError("router container name is invalid")
    selected = os.path.abspath(os.path.expanduser(config_file))
    try:
        with open(selected, "rb") as handle:
            raw = handle.read(MAX_ROUTER_CONFIG_BYTES + 1)
    except OSError as exc:
        raise ValueError("could not read candidate router config") from exc
    if len(raw) > MAX_ROUTER_CONFIG_BYTES:
        raise ValueError("candidate router config exceeds the 1 MiB limit")
    try:
        config_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("candidate router config is not valid UTF-8") from None

    argv = [
        "docker",
        "exec",
        "-i",
        container,
        "python",
        "-c",
        _RUNTIME_CONFIG_PROBE_CODE,
        str(timeout),
    ]
    try:
        result = _run(
            argv,
            input=config_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(600.0, max(30.0, timeout * 64 + 10.0)),
        )
    except FileNotFoundError:
        raise ValueError("router-runtime fleet probe requires Docker") from None
    except subprocess.TimeoutExpired:
        raise ValueError("router-runtime fleet probe exceeded its total timeout") from None
    if result.returncode not in {0, 1}:
        raise ValueError("router-runtime fleet probe failed before producing a report")
    report = _decode_fleet_report(result.stdout)
    report["evidence_source"] = "configured-file"
    report["probe_perspective"] = "router-runtime"
    report["config_sha256"] = hashlib.sha256(raw).hexdigest()
    return report


def cmd_fleet_status(
    config_path_arg=None,
    as_json=False,
    timeout=4.0,
    *,
    live=False,
    probe_perspective=None,
    container=DEFAULT_CONTAINER,
    installed_config=DEFAULT_INSTALLED_CONFIG,
    _probe=_probe_endpoint,
    _installed=installed_fleet_status,
    _configured_runtime=configured_fleet_status,
):
    """Report which configured capabilities have a reachable backing serve."""
    from .doctor import resolve_default_config_path
    from .router import config as router_config

    try:
        timeout = _validated_probe_timeout(timeout)
    except ValueError as exc:
        print("router fleet status failed: %s" % exc, file=sys.stderr)
        return 2
    use_live = bool(live or (config_path_arg is None and probe_perspective is None))
    if use_live and config_path_arg:
        print("--live and --config are mutually exclusive", file=sys.stderr)
        return 2
    if use_live and probe_perspective is not None:
        print("--live selects the router-runtime perspective automatically", file=sys.stderr)
        return 2
    if use_live:
        try:
            report = _installed(
                container=container,
                installed_config=installed_config,
                timeout=timeout,
            )
        except ValueError as exc:
            print("live router fleet status failed: %s" % exc, file=sys.stderr)
            return 2
        if as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
            return 1 if report.get("unreachable_aliases") else 0
        return _print_fleet_status(report)

    path = config_path_arg or resolve_default_config_path()
    if not path:
        print("no router config found; pass --config PATH", file=sys.stderr)
        return 2
    try:
        config = router_config.load(path)
    except Exception as exc:  # noqa: BLE001 - surface the load failure verbatim
        print("could not load router config %s: %s" % (path, exc), file=sys.stderr)
        return 2

    perspective = probe_perspective or "command-host"
    if perspective == "router-runtime":
        try:
            report = _configured_runtime(
                path,
                container=container,
                timeout=timeout,
            )
        except ValueError as exc:
            print("router-runtime fleet status failed: %s" % exc, file=sys.stderr)
            return 2
    else:
        report = fleet_status(
            config,
            timeout=timeout,
            _probe=_probe,
            probe_perspective=perspective,
            evidence_source="configured-file",
        )
        report["config_sha256"] = _config_sha256(path)
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if report["unreachable_aliases"] else 0

    return _print_fleet_status(report)


def _print_fleet_status(report):
    print(
        "source=%s perspective=%s"
        % (report["evidence_source"], report["probe_perspective"])
    )

    print(
        "%-9s %-16s %-22s %-24s %s"
        % ("KIND", "NAME", "TARGET", "ENDPOINT KIND", "STATE")
    )
    for row in report["rows"]:
        state = (
            "ok (%s)" % row["detail"]
            if row["reachable"]
            else "%s (%s)" % (row["failure_class"].upper(), row["detail"])
        )
        print(
            "%-9s %-16s %-22s %-24s %s"
            % (row["kind"], row["name"], row["target"], row["endpoint_kind"], state)
        )
    print("\nfleet status: %d configured, %d unreachable" % (
        report["checked"], report["unreachable"]))
    if report.get("perspective_mismatches"):
        print(
            "probe perspective mismatches: %d; use the installed-router live probe"
            % report["perspective_mismatches"]
        )
    if report["unreachable_aliases"]:
        print("aliases with no reachable backing serve: %s"
              % ", ".join(report["unreachable_aliases"]))
    return 1 if report["unreachable_aliases"] else 0


def _local_router_cutover(timeout=30):
    """Run inside the selected native container; never follows a remote URL.

    The old image must implement the all-path boundary itself. Import or
    protocol failures leave bootstrap on HOLD. No host dotenv resolution.
    """
    from pathlib import Path
    from .router.config import load_server_config
    settings = load_server_config(DEFAULT_INSTALLED_CONFIG)
    if settings.router_owner_id is None:
        raise ValueError("router_owner_roster_unknown")
    token = os.environ.get(settings.auth_env or "")
    if not token:
        raise ValueError("router_owner_auth_unavailable")
    local_env = {"ANVIL_ROUTER_TOKEN":token}
    expected = hashlib.sha256(Path(DEFAULT_INSTALLED_CONFIG).read_bytes()).hexdigest()
    status = _transition_request("status", scope="router", router_url="http://127.0.0.1:8000", env=local_env).get("result", {})
    if status.get("cutover_pending") is True:
        # Repeat native verification within the same closed owner transaction;
        # source bytes may already have been replaced under that barrier.
        expected = status.get("configuration_revision")
        if type(expected) is not str or re.fullmatch("[0-9a-f]{64}", expected) is None:
            raise ValueError("router_owner_binding_changed")
    def request(action, **kwargs):
        result = _transition_request(action, scope="router", router_url="http://127.0.0.1:8000",
                                     confirm=True, dry_run=False, env=local_env, **kwargs)
        payload = result.get("result")
        if (result.get("scope") != "router" or type(payload) is not dict
                or payload.get("scope") != "router" or payload.get("configuration_revision") != expected
                or payload.get("durable") is not True):
            raise ValueError("router_owner_binding_changed")
        return payload
    closure = request("quiesce")
    barrier_token = closure.get("barrier_token")
    drained = request("drain", barrier_token=barrier_token, timeout=timeout)
    if (drained.get("drained") is not True or drained.get("unknown") != []
            or type(drained.get("counts")) is not dict
            or set(drained["counts"]) != {"chat", "purpose", "audio", "memory", "media", "internal", "delivery", "maintenance"}
            or any(type(v) is not int or v != 0 for v in drained["counts"].values())
            or drained.get("roster_revision") != closure.get("roster_revision")):
        raise ValueError("router_owned_work_not_drained")
    consumed = request("consume", barrier_token=barrier_token)
    if consumed.get("drained") is not True or consumed.get("cutover_pending") is not True:
        raise ValueError("router_barrier_not_consumed")
    return {"schema":"router-native-cutover/v1", "owner_backend":settings.router_owner_backend, "configuration_revision":expected,
            "roster_revision":consumed["roster_revision"], "generation":consumed["generation"],
            "drained":True, "closed":True}


def _verify_compose_target(compose, service, custody, *, _run, env_file=None,
                           execution_env=None, allow_foreign=False, exclude_oneoff=None):
    """Bind a Compose operation to the one actual owner consumed by its gate."""
    if (not isinstance(service, str) or not _CONTAINER_NAME_RE.fullmatch(service)
            or custody.get("compose_service") != service):
        raise ValueError("router_compose_target_mismatch")
    result = _run([*_compose_argv(compose, env_file=env_file), "ps", "--all", "--quiet", service],
                  capture_output=True, text=True, encoding="utf-8", timeout=5,
                  env=execution_env)
    raw = result.stdout or ""
    if result.returncode or len(raw) > 131072:
        raise ValueError("router_compose_target_unknown")
    ids = raw.split()
    if exclude_oneoff is not None:
        if ids.count(exclude_oneoff) != 1 or not re.fullmatch("[a-f0-9]{64}", exclude_oneoff):
            raise ValueError("router_offline_helper_unknown")
        ids.remove(exclude_oneoff)
    expected = [custody["container_id"]]
    if custody.get("compose_project") != DEFAULT_COMPOSE_PROJECT:
        # Explicit foreign replacement may remove its consumed owner only when
        # the destination project has no other potential target to mutate.
        if not allow_foreign:
            raise ValueError("router_compose_target_mismatch")
        expected = []
    if ids != expected:
        raise ValueError("router_compose_target_mismatch")


def require_router_drain(container, *, _run=subprocess.run, timeout=30,
                         compose=None, service=None, env_file=None,
                         execution_env=None, allow_foreign=False):
    """Shared mutation gate: exact container, actual old-owner zero, retained closure."""
    if type(timeout) is not int or not 1 <= timeout <= 900:
        raise ValueError("timeout_must_be_integer_1_900")
    before = _restart_custody(container, _run)
    if before.get("available") is not True:
        raise ValueError("router_old_runtime_bootstrap_hold")
    if compose is not None:
        _verify_compose_target(compose, service, before, _run=_run, env_file=env_file,
                               execution_env=execution_env, allow_foreign=allow_foreign)
    code = ("import json; from anvil_serving.router_manage import _local_router_cutover; "
            f"print(json.dumps(_local_router_cutover(timeout={timeout}),sort_keys=True))")
    result = _run(["docker", "exec", container, "python", "-c", code], capture_output=True,
                  text=True, encoding="utf-8", timeout=timeout+15)
    try:
        from .observability.dashboard.contracts import strict_json
        receipt = strict_json((result.stdout or "").encode())
        if (result.returncode or type(receipt) is not dict or receipt.get("schema") != "router-native-cutover/v1"
                or receipt.get("closed") is not True or receipt.get("drained") is not True
                or any(type(receipt.get(key)) is not str or not re.fullmatch(r"[a-f0-9]{64}", receipt[key])
                       for key in ("configuration_revision", "roster_revision"))
                or type(receipt.get("generation")) is not int or receipt["generation"] < 1):
            raise ValueError()
    except Exception:
        raise ValueError("router_old_runtime_bootstrap_hold") from None
    backend = receipt.get('owner_backend', 'native-process')
    if backend not in {'native-process', 'managed-container'}:
        raise ValueError('router_owner_backend_unknown')
    after = _restart_custody(container, _run)
    if before != after:
        raise ValueError("router_owner_binding_changed")
    if compose is not None:
        _verify_compose_target(compose, service, after, _run=_run, env_file=env_file,
                               execution_env=execution_env, allow_foreign=allow_foreign)
    return {**receipt, "container_id":before["container_id"], "image_id":before["image_id"]}


def _offline_router_start(*, _legacy_incarnation=None):
    """Candidate-native first bootstrap; never consumes predecessor ownership."""
    from .router.config import load_server_config
    from .router.keys import KeyStore
    from .router.usage_store import _validate_schema
    settings = load_server_config(DEFAULT_INSTALLED_CONFIG)
    store = KeyStore(settings.api_keys_path)
    with store._offline_custody(settings, allow_retained=True), store._connect() as db:
        if store.version != 3:
            raise ValueError('router_accounting_migration_required')
        _validate_schema(db)
        from .router.usage_store import UsageStore
        usage = UsageStore(store)
        usage.owner_config = settings
        usage.inactive_native_runs(usage._observed_owner(settings.router_owner_id), domain_id=settings.usage_domain_id)
        if _legacy_incarnation is not None:
            from .router.maintenance import legacy_frontier
            from .router.container_owner import require
            from .router.keys import _private_json
            from pathlib import Path
            legacy = legacy_frontier(store)
            require(legacy is not None and db.execute('SELECT count(*) FROM usage_runs').fetchone()==(0,)
                and _private_json(Path(str(store.path)+'.maintenance-legacy.json'))['receipt']['stopped']==_legacy_incarnation)
    result = {'schema': 'router-offline-start/v1', 'offline': True, 'schema_version': 3}
    if _legacy_incarnation is not None: result['legacy_receipt_sha256']=legacy
    return result


def _offline_compose_roster(compose, service, container, *, _run, env_file=None, execution_env=None, live_target=False, helper_container=None, _metadata_only=False):
    """Verify the selected service and every active durable-mount consumer."""
    def read(argv):
        result = _run(argv, capture_output=True, text=True, encoding='utf-8', timeout=10, env=execution_env)
        if result.returncode or len(result.stdout or '') > MAX_COMPOSE_FILE_BYTES:
            raise ValueError('router_offline_roster_unknown')
        return result.stdout or ''
    from .observability.dashboard.contracts import strict_json
    # Acknowledged legacy STOP reads only declared metadata, never resolves a
    # service/shared dotenv. The actual mount/consumer roster is Docker-native.
    metadata_flags = ['--no-env-resolution','--no-interpolate'] if _metadata_only else []
    selected_env = os.devnull if _metadata_only else env_file
    rendered = strict_json(read([*_compose_argv(compose, env_file=selected_env), 'config',
                                *metadata_flags, '--format', 'json']).encode())
    if (type(rendered) is not dict or type(rendered.get('services')) is not dict
            or type(rendered.get('volumes', {})) is not dict):
        raise ValueError('router_offline_roster_unknown')
    selected = rendered['services'].get(service, {})
    if type(selected) is not dict:
        raise ValueError('router_offline_target_mismatch')
    if (service != DEFAULT_SERVICE or rendered.get('name') != DEFAULT_COMPOSE_PROJECT
            or selected.get('container_name') != container):
        raise ValueError('router_offline_target_mismatch')
    mounts = selected.get('volumes')
    if not isinstance(mounts, list) or any(type(row) is not dict for row in mounts):
        raise ValueError('router_offline_storage_unknown')
    config_mounts = [row for row in mounts if row.get('target') == DEFAULT_INSTALLED_CONFIG]
    if (len(config_mounts) != 1 or config_mounts[0].get('type') != 'bind'
            or config_mounts[0].get('read_only') is not True):
        raise ValueError('router_offline_config_mount_unknown')
    sources = set()
    for row in mounts:
        if row.get('type') not in {'bind', 'volume'} or not isinstance(row.get('source'), str):
            raise ValueError('router_offline_storage_unknown')
        if row.get('read_only') is not True:
            source = row['source']
            if row['type'] == 'volume':
                declaration = rendered.get('volumes', {}).get(source, {})
                if type(declaration) is not dict:
                    raise ValueError('router_offline_storage_unknown')
                source = declaration.get('name')
            if not isinstance(source, str) or not source:
                raise ValueError('router_offline_storage_unknown')
            sources.add((row['type'], source))
    if not sources:
        raise ValueError('router_offline_storage_unknown')
    ids = read(['docker', 'ps', '--all', '--quiet', '--no-trunc']).split()
    if len(ids) > 1024 or len(set(ids)) != len(ids) or any(not re.fullmatch('[a-f0-9]{64}', value) for value in ids):
        raise ValueError('router_offline_roster_unknown')
    rows, helper_id = [], None
    if ids:
        projection = ('{"id":{{json .Id}},"status":{{json .State.Status}},'
                      '"mounts":{{json .Mounts}}}')
        rows = [strict_json(line.encode()) for line in read(['docker', 'inspect', '--format', projection, *ids]).splitlines()]
        if (len(rows) != len(ids) or any(type(row) is not dict for row in rows)
                or {row.get('id') for row in rows} != set(ids)):
            raise ValueError('router_offline_roster_unknown')
        if helper_container is not None:
            helper = _restart_custody(helper_container, _run)
            if helper.get('available') is True:
                image = read(['docker', 'image', 'inspect', '--format', '{{.Id}}', selected['image']]).strip()
                if (helper['container_id'] not in ids or helper['compose_project'] != DEFAULT_COMPOSE_PROJECT
                        or helper['compose_service'] != service or helper['image_id'] != image):
                    raise ValueError('router_offline_helper_unknown')
                helper_id = helper['container_id']
                rows = [r for r in rows if r['id'] != helper_id]
        selected_id = (_restart_custody(container, _run).get('container_id') if live_target else None)
        for row in rows:
            status = row.get('status')
            if status not in {'created', 'exited', 'dead', 'running', 'paused', 'restarting', 'removing'}:
                raise ValueError('router_offline_roster_unknown')
            if not isinstance(row.get('mounts'), list):
                raise ValueError('router_offline_storage_unknown')
            for mount in row['mounts']:
                if type(mount) is not dict or type(mount.get('RW')) is not bool:
                    raise ValueError('router_offline_storage_unknown')
                identity = (mount.get('Type'), mount.get('Name') if mount.get('Type') == 'volume' else mount.get('Source'))
                if status not in {'created', 'exited', 'dead'} and mount['RW'] and identity in sources and row['id'] != selected_id:
                    raise ValueError('router_offline_storage_busy')
    state, project = _container_compose_project(container, _run=_run)
    allowed = {'running'} if live_target else {'absent', 'exited', 'created'}
    if state not in allowed or (state != 'absent' and project != DEFAULT_COMPOSE_PROJECT):
        raise ValueError('router_offline_target_active_or_unknown')
    custody = None if state == 'absent' else _restart_custody(container, _run)
    if custody is not None:
        if custody.get('available') is not True:
            raise ValueError('router_offline_target_unknown')
        if _metadata_only:
            ids = read(['docker','ps','--all','--quiet','--no-trunc',
                '--filter','label=com.docker.compose.project='+DEFAULT_COMPOSE_PROJECT,
                '--filter','label=com.docker.compose.service='+service]).split()
            if ids != [custody['container_id']]:
                raise ValueError('router_offline_target_mismatch')
        else:
            _verify_compose_target(compose, service, custody, _run=_run, env_file=env_file, execution_env=execution_env,
                                   exclude_oneoff=helper_id)
    else:
        selected_ids = read([*_compose_argv(compose, env_file=env_file), 'ps', '--all', '--quiet', service]).split()
        if helper_id is not None:
            if selected_ids.count(helper_id) != 1:
                raise ValueError('router_offline_helper_unknown')
            selected_ids.remove(helper_id)
        if selected_ids:
            raise ValueError('router_offline_target_mismatch')
    return {'rendered': rendered, 'roster': rows, 'target': custody}


def _compose_owner_backend(compose, service, *, _run, env_file=None, execution_env=None):
    result = _run([*_compose_argv(compose, env_file=env_file), 'config', '--format', 'json'],
                  capture_output=True, text=True, encoding='utf-8', timeout=10, env=execution_env)
    from .observability.dashboard.contracts import strict_json
    value = strict_json((result.stdout or '').encode())
    if result.returncode or type(value) is not dict or len(result.stdout or '') > MAX_COMPOSE_FILE_BYTES:
        raise ValueError('router_owner_backend_unknown')
    mounts = value.get('services', {}).get(service, {}).get('volumes', [])
    config = [m for m in mounts if m.get('target') == DEFAULT_INSTALLED_CONFIG]
    if len(config) != 1 or config[0].get('type') != 'bind' or config[0].get('read_only') is not True:
        raise ValueError('router_owner_backend_unknown')
    from pathlib import Path
    import tomllib
    path = Path(config[0]['source'])
    # Read only the declared non-secret config. No dotenv or token resolution.
    if path.expanduser().resolve() == (Path.home() / '.env').resolve():
        raise ValueError('router_owner_backend_unknown')
    raw = path.read_bytes()
    if len(raw) > MAX_COMPOSE_FILE_BYTES:
        raise ValueError('router_owner_backend_unknown')
    backend = tomllib.loads(raw.decode()).get('server', {}).get('router_owner_backend', 'native-process')
    if backend not in {'native-process', 'managed-container'}:
        raise ValueError('router_owner_backend_unknown')
    return backend


def _container_incarnation(container, *, _run, stopped=False):
    from .router.container_owner import docker_identity
    custody = _restart_custody(container, _run)
    if custody.get('available') is not True:
        raise ValueError('router_container_custody_unknown')
    daemon = _run(['docker', 'info', '--format', '{{.ID}}'], capture_output=True,
                  text=True, encoding='utf-8', timeout=10)
    if daemon.returncode:
        raise ValueError('router_container_custody_unknown')
    value = {k: custody[k] for k in ('container_id', 'image_id', 'started_at', 'restart_count',
                                    'compose_project', 'compose_service')}
    value['daemon_id'] = (daemon.stdout or '').strip()
    result = _run(['docker', 'inspect', '--format', '{{json .State}}', custody['container_id']],
                  capture_output=True, text=True, encoding='utf-8', timeout=10)
    from .observability.dashboard.contracts import strict_json
    state = strict_json((result.stdout or '').encode())
    if result.returncode or type(state) is not dict:
        raise ValueError('router_container_custody_unknown')
    if stopped:
        value.update(finished_at=state.get('FinishedAt'), status=state.get('Status'), running=state.get('Running'),
                     paused=state.get('Paused'), restarting=state.get('Restarting'), pid=state.get('Pid'))
    elif (state.get('Status') != 'running' or state.get('Running') is not True
          or state.get('Paused') is not False or state.get('Restarting') is not False
          or type(state.get('Pid')) is not int or state['Pid'] <= 0):
        raise ValueError('router_container_custody_unknown')
    docker_identity(value, stopped=stopped)
    return value


def _native_owner_commit(argv, observation, *, _popen=subprocess.Popen, execution_env=None, _clients=False, _client_auth=None, _keys=False):
    """Host after-check completes while the native helper still holds its fences.

    This bounded pipe is native operator authority, never an inference endpoint.
    Host failure/EOF leaves non-authoritative pending custody and a closed owner.
    """
    import select
    from .observability.dashboard.contracts import strict_json
    if os.name != 'posix':
        raise ValueError('router_native_container_custody_unsupported')
    before = observation()
    process = _popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                     env=execution_env)
    def read():
        if not select.select([process.stdout], [], [], 35)[0]:
            raise ValueError('router_container_custody_timeout')
        raw = process.stdout.readline(16385)
        if len(raw) > 16384:
            raise ValueError('router_container_custody_unknown')
        return strict_json(raw)
    try:
        pending = read()
        if (type(pending) is not dict or set(pending) != {'pending_sha256', 'run_id'}
                or type(pending['pending_sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', pending['pending_sha256'])):
            raise ValueError('router_container_custody_unknown')
        if _keys and (pending['pending_sha256'] != before['request_sha256']
                      or pending['run_id'] != before['config_sha256']):
            raise ValueError('router_key_binding_changed')
        if _clients and (pending['pending_sha256'] != before['inputs']['job_sha256']
                         or pending['run_id'] != before['inputs']['declaration_sha256']):
            raise ValueError('router_client_binding_changed')
        if before != observation():
            raise ValueError('router_container_custody_changed')
        ack = {'commit': pending['pending_sha256']}
        if _clients and _client_auth is not None:
            if (type(_client_auth) is not dict
                    or set(_client_auth) != {'client_id', 'credential', 'router_config_sha256'}
                    or type(_client_auth['credential']) is not str or len(_client_auth['credential']) > 8192):
                raise ValueError('router_client_auth_invalid')
            ack.update(_client_auth)
        process.stdin.write(json.dumps(ack).encode() + b'\n')
        process.stdin.flush()
        final = read()
        client_final = (_clients and type(final) is dict
                        and set(final) == {'finalized', 'run_id', 'result'}
                        and final['finalized'] == 'clients' and final['run_id'] == pending['run_id']
                        and type(final['result']) is dict
                        and final['result'].get('schema') == 'anvil.client-worker/v1'
                        and final['result'].get('live_status') == 'unqualified'
                        and final['result'].get('providers_modified') is False)
        if client_final:
            value = final['result']
            auth_keys = {'credential_validated', 'grant_sha256'} if _client_auth is not None else set()
            client_final = (set(value) == {'schema', 'configuration_status', 'installed_sha256', 'clients_count',
                                          'recipients_staged', 'providers_modified', 'live_status'} | auth_keys
                            and (_client_auth is None or (value['credential_validated'] is True
                                 and type(value['grant_sha256']) is str
                                 and re.fullmatch('[a-f0-9]{64}', value['grant_sha256'])))
                            and value['configuration_status'] in {'installed', 'incomplete'}
                            and (value['installed_sha256'] is None or (type(value['installed_sha256']) is str
                                 and re.fullmatch('[a-f0-9]{64}', value['installed_sha256'])))
                            and all(type(value[k]) is int and 0 <= value[k] <= 128
                                    for k in ('clients_count', 'recipients_staged')))
        if _clients and not client_final:
            raise ValueError('router_client_worker_refused')
        key_final = (_keys and type(final) is dict and set(final) == {'finalized', 'run_id', 'result'}
                     and final['finalized'] == 'keys' and final['run_id'] == pending['run_id'])
        if key_final:
            from .router.keys import KeyStore
            value = final['result']
            if (type(value) is not dict or set(value) != {'key_id', 'name', 'models', 'paths', 'rpm', 'created_at', 'expires_at', 'revoked_at'}):
                raise ValueError('router_key_worker_refused')
            KeyStore._metadata(tuple(value[k] for k in ('key_id', 'name', 'models', 'paths', 'rpm', 'created_at', 'expires_at', 'revoked_at'))[:2]
                               + (json.dumps(value['models']), json.dumps(value['paths']))
                               + tuple(value[k] for k in ('rpm', 'created_at', 'expires_at', 'revoked_at')))
        if _keys and not key_final:
            raise ValueError('router_key_worker_refused')
        if (not client_final and not key_final and final != {'finalized': 'live', 'run_id': pending['run_id']}
                and final != {'finalized': 'dead', 'run_id': pending['run_id']}):
            raise ValueError('router_container_custody_unknown')
        if process.wait(timeout=10):
            raise ValueError('router_container_custody_refused')
        return final
    finally:
        process.stdin.close(); process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)


def _managed_container_custody(kind, compose, service, container, *, _run=subprocess.run,
                               env_file=None, execution_env=None, _popen=subprocess.Popen):
    import uuid
    helper = 'router-custody-' + uuid.uuid4().hex if kind == 'dead' else None
    def observation():
        roster = _offline_compose_roster(compose, service, container, _run=_run, env_file=env_file,
                                         execution_env=execution_env, live_target=kind == 'live', helper_container=helper)
        return {'roster': roster, 'incarnation': _container_incarnation(container, _run=_run, stopped=kind == 'dead')}
    before = observation()
    if kind == 'live':
        ready = ('from anvil_serving.router.container_owner import native_ready; native_ready()')
        deadline = time.monotonic() + 20
        while True:
            result = _run(['docker', 'exec', before['incarnation']['container_id'], 'python', '-c', ready],
                          capture_output=True, text=True, encoding='utf-8', timeout=3)
            if result.returncode == 0 and (result.stdout or '').strip() == 'ready':
                break
            if time.monotonic() >= deadline:
                raise ValueError('router_managed_owner_not_ready')
            time.sleep(.2)
    code = ('from anvil_serving.router.container_owner import native_transaction; '
            f'native_transaction({kind!r}, {before["incarnation"]!r})')
    if kind == 'live':
        argv = ['docker', 'exec', '-i', before['incarnation']['container_id'], 'python', '-c', code]
    else:
        argv = [*_compose_argv(compose, env_file=env_file), 'run', '--rm', '--no-deps', '-T', '--name', helper,
                '--entrypoint', 'python', service, '-c', code]
    # Repeated host observation is compared against the identity sent to native.
    def unchanged():
        current = observation()
        if current != before:
            raise ValueError('router_container_custody_changed')
        return current
    return _native_owner_commit(argv, unchanged, _popen=_popen, execution_env=execution_env)


def _offline_compose_run(compose, service, container, code, *, _run, env_file=None, execution_env=None):
    before = _offline_compose_roster(compose, service, container, _run=_run,
                                    env_file=env_file, execution_env=execution_env)
    result = _run([*_compose_argv(compose, env_file=env_file), 'run', '--rm', '--no-deps',
                   '--entrypoint', 'python', service, '-c', code], capture_output=True,
                  text=True, encoding='utf-8', timeout=30, env=execution_env)
    if result.returncode or len(result.stdout or '') > 131072:
        raise ValueError('router_offline_custody_refused')
    from .observability.dashboard.contracts import strict_json
    receipt = strict_json((result.stdout or '').encode())
    after = _offline_compose_roster(compose, service, container, _run=_run,
                                   env_file=env_file, execution_env=execution_env)
    if before != after:
        raise ValueError('router_offline_roster_changed')
    return receipt


def require_router_offline(compose, service, *, container=DEFAULT_CONTAINER, _run=subprocess.run,
                           env_file=None, execution_env=None, _legacy_incarnation=None):
    """Offline first start under the caller's lifecycle lock, not a drain proof.

    Probe locks end before launch. Actual managed startup reacquires its producer
    lock and checks retained ownership; external producer holds remain required.
    """
    try:
        code = ('import json; from anvil_serving.router_manage import _offline_router_start; '
                f'print(json.dumps(_offline_router_start(_legacy_incarnation={_legacy_incarnation!r}),sort_keys=True))')
        receipt = _offline_compose_run(compose, service, container, code, _run=_run,
                                      env_file=env_file, execution_env=execution_env)
        expected = {'schema': 'router-offline-start/v1', 'offline': True, 'schema_version': 3}
        if _legacy_incarnation is not None:
            legacy = receipt.get('legacy_receipt_sha256') if type(receipt) is dict else None
            if type(legacy) is not str or not re.fullmatch('[0-9a-f]{64}',legacy):
                raise ValueError('router_legacy_custody_refused')
            expected['legacy_receipt_sha256']=legacy
        if receipt != expected:
            raise ValueError('router_offline_custody_refused')
        return receipt
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('router_offline_custody_refused') from None


@_serving_authority_mutation
def migrate_router_offline(compose, backup_out, *, env_file=None, _run=subprocess.run, journal_mode=None, maintenance_receipt=None):
    from pathlib import PurePosixPath
    target = PurePosixPath(backup_out)
    if not target.is_relative_to('/var/lib/anvil-serving/router-keys') or '..' in target.parts:
        raise ValueError('router_offline_backup_must_be_durable')
    execution_env = _compose_execution_env(compose, env_file)
    if journal_mode not in {None, 'WAL'}:
        raise ValueError('router_offline_storage_unsupported')
    legacy = None
    if maintenance_receipt is not None:
        from .control_plane.mcp.auth_file import read_private_auth_file
        from .observability.dashboard.contracts import strict_json
        from .router.maintenance import authorization, validate_legacy
        legacy = validate_legacy(strict_json(read_private_auth_file(maintenance_receipt,max_bytes=16384)))
        authorization(legacy['authorization'])
        if _container_incarnation(DEFAULT_CONTAINER,_run=_run,stopped=True) != legacy['stopped']:
            raise ValueError('router_legacy_incarnation_changed')
    code = ('import json; from anvil_serving.router.keys import _migrate_offline; '
            f'print(json.dumps(_migrate_offline({DEFAULT_INSTALLED_CONFIG!r},{backup_out!r},journal_mode={journal_mode!r},_legacy_receipt={legacy!r},_managed_worker=True),sort_keys=True))')
    result = _offline_compose_run(compose, DEFAULT_SERVICE, DEFAULT_CONTAINER, code, _run=_run,
                                 env_file=env_file, execution_env=execution_env)
    if maintenance_receipt is not None and _container_incarnation(DEFAULT_CONTAINER,_run=_run,stopped=True) != legacy['stopped']:
        raise ValueError('router_legacy_incarnation_changed')
    if (type(result) is not dict or set(result) != {'schema_version', 'migrated', 'backup_schema_version', 'offline'} | ({'journal_mode', 'checkpoint_frames'} if journal_mode else set()) | ({'legacy_receipt_sha256'} if legacy else set())
            or result['schema_version'] != 3 or type(result['migrated']) is not bool
            or result['backup_schema_version'] not in {1, 2, 3} or result['offline'] is not True
            or journal_mode and (result['journal_mode'] != 'wal' or type(result['checkpoint_frames']) is not int
                                or not 0 <= result['checkpoint_frames'] < 2**53)):
        raise ValueError('router_offline_migration_refused')
    if legacy is not None:
        from .router.container_owner import digest
        if result['legacy_receipt_sha256'] != digest(legacy):
            raise ValueError('router_legacy_receipt_changed')
    return result

def _bounded_offline_worker(roster):
    """The fixed inert, resource-bounded native worker envelope."""
    from .client_identity import _require
    rendered = roster['rendered']
    selected = rendered['services'][DEFAULT_SERVICE]
    memory = selected.get('mem_limit')
    swap = selected.get('memswap_limit', memory)
    _require(all(type(v) is int or type(v) is str and re.fullmatch('[0-9]{1,10}', v) for v in (memory, swap)))
    memory, swap = int(memory), int(swap)
    allowed = {'image', 'container_name', 'volumes', 'network_mode', 'read_only', 'init', 'cap_drop',
               'security_opt', 'user', 'cpus', 'mem_limit', 'memswap_limit', 'pids_limit', 'restart', 'entrypoint', 'command'}
    _require(not set(selected) - allowed and selected.get('network_mode') == 'none'
             and selected.get('read_only') is True and selected.get('init') is True
             and selected.get('cap_drop') == ['ALL']
             and selected.get('security_opt') == ['no-new-privileges:true']
             and re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*', str(selected.get('user', ''))) is not None
             and type(selected.get('cpus')) in {int, float} and 0 < selected['cpus'] <= 2
             and type(selected.get('pids_limit')) is int and 1 <= selected['pids_limit'] <= 128
             and 0 < memory <= 1024**3 and swap == memory
             and selected.get('restart', 'no') == 'no'
             and selected.get('entrypoint') == ['/bin/false'] and selected.get('command') == [])
    mounts = {m['target']: m for m in selected['volumes']}
    _require(len(mounts) == len(selected['volumes']))
    return selected, mounts


@_serving_authority_mutation
def create_key_offline(compose, request, *, _run=subprocess.run, _popen=subprocess.Popen):
    """Issue one unique narrow key through the existing native custody pipe."""
    import uuid
    from pathlib import Path
    from .client_identity import _require
    from .router.config import load_server_config
    from .router.keys import _secure_directory
    from .control_plane.mcp.auth_file import read_private_auth_file
    _require(type(request) is dict and set(request) == {'name', 'model', 'path', 'rpm', 'expires_days', 'out'})
    output = Path(request['out'])
    _require(output.is_absolute() and '..' not in output.parts and len(output.parent.parts) >= 6)
    helper = 'router-keys-' + uuid.uuid4().hex
    def observation():
        roster = _offline_compose_roster(compose, DEFAULT_SERVICE, DEFAULT_CONTAINER,
                                        _run=_run, helper_container=helper)
        selected, mounts = _bounded_offline_worker(roster)
        _require(set(mounts) == {DEFAULT_INSTALLED_CONFIG, '/var/lib/anvil-serving/router-keys', str(output.parent)})
        config = mounts[DEFAULT_INSTALLED_CONFIG]
        volume = mounts['/var/lib/anvil-serving/router-keys']
        material = mounts[str(output.parent)]
        _require(config['type'] == 'bind' and config.get('read_only') is True
                 and volume['type'] == 'volume' and volume.get('read_only', False) is False
                 and material['type'] == 'bind' and material['source'] == str(output.parent)
                 and material.get('read_only', False) is False)
        _secure_directory(output.parent, create=False)
        server = load_server_config(config['source'])
        _require(Path(server.api_keys_path).is_relative_to('/var/lib/anvil-serving/router-keys'))
        raw = read_private_auth_file(config['source'], max_bytes=2*1024**2)
        image = _run(['docker', 'image', 'inspect', '--format', '{{.Id}}', selected['image']],
                     capture_output=True, text=True, timeout=5)
        _require(image.returncode == 0 and re.fullmatch('sha256:[a-f0-9]{64}', image.stdout.strip()))
        actual = []
        for target, mount in mounts.items():
            source = roster['rendered']['volumes'][mount['source']]['name'] if mount['type'] == 'volume' else mount['source']
            actual.append((mount['type'], source, target, mount.get('read_only', False)))
        custody = _restart_custody(helper, _run)
        if custody.get('available') is True:
            _require(custody['image_id'] == image.stdout.strip()
                     and sorted((m['type'], m['name'] if m['type'] == 'volume' else m['source'],
                                 m['destination'], m['read_only']) for m in custody['mounts']) == sorted(actual))
        return {'roster': roster, 'image_id': image.stdout.strip(), 'mounts': sorted(actual),
                'config_sha256': hashlib.sha256(raw).hexdigest(),
                'request_sha256': hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()}
    original = observation()
    code = ('from anvil_serving.router.keys import _create_offline; '
            f'_create_offline({request!r})')
    argv = [*_compose_argv(compose), 'run', '--rm', '--no-deps', '-T', '--name', helper,
            '--entrypoint', 'python', DEFAULT_SERVICE, '-c', code]
    try:
        return _native_owner_commit(argv, observation, _popen=_popen, _keys=True)['result']
    finally:
        state, project = _container_compose_project(helper, _run=_run)
        if state != 'absent':
            owned = _restart_custody(helper, _run)
            _require(project == DEFAULT_COMPOSE_PROJECT and owned.get('available') is True
                     and owned['compose_service'] == DEFAULT_SERVICE and owned['image_id'] == original['image_id'])
            _run(['docker', 'rm', '--force', owned['container_id']], capture_output=True, text=True, timeout=10)
            _require(docker_state(owned['container_id'], _run=_run) == 'absent'
                     and _container_compose_project(helper, _run=_run)[0] == 'absent')


def _client_worker_inputs(roster, namespace, declaration_sha, *, _run=subprocess.run):
    from pathlib import Path
    from .client_identity import _read, _load, _require, WORKER_ROOT, WORKER_SCHEMA, canonical
    from .control_plane.mcp.auth_file import read_private_auth_file

    rendered = roster['rendered']
    selected, mounts = _bounded_offline_worker(roster)
    for target in (str(WORKER_ROOT / 'worker.json'), str(WORKER_ROOT / 'client-identity.json')):
        _require(target in mounts and mounts[target]['type'] == 'bind' and mounts[target].get('read_only') is True)
    job = _read(mounts[str(WORKER_ROOT / 'worker.json')]['source'])
    declaration, server = _load(mounts[str(WORKER_ROOT / 'client-identity.json')]['source'])
    _require(type(job) is dict and set(job) == {'schema', 'declaration_sha256', 'helper_sha256', 'recipients', 'material_directories'}
             and job['schema'] == WORKER_SCHEMA and job['declaration_sha256'] == declaration_sha
             and hashlib.sha256(canonical(declaration)).hexdigest() == declaration_sha)
    directories = job['material_directories']
    _require(type(directories) is list and len(directories) <= 128
             and len(set(directories)) == len(directories)
             and all(type(p) is str and Path(p).is_absolute() and '..' not in Path(p).parts
                     and len(Path(p).parts) >= 6 and not WORKER_ROOT.is_relative_to(p)
                     and not Path(declaration['router_config']).is_relative_to(p)
                     and not Path(server.api_keys_path).is_relative_to(p) for p in directories))
    readonly = {DEFAULT_INSTALLED_CONFIG, str(WORKER_ROOT / 'worker.json'), str(WORKER_ROOT / 'client-identity.json'),
                declaration['router_config'], str(Path(declaration['router_config']).with_name('client-router-origin.json'))}
    config = declaration['router_config']
    origin = str(Path(config).with_name('client-router-origin.json'))
    _require(all(p in mounts and mounts[p].get('source') == p for p in (config, origin))
             and mounts[DEFAULT_INSTALLED_CONFIG]['source'] == config)
    for kind, name in (('webui', 'webui-integrations.py'), ('recipient', 'configure-pi.py')):
        digest = job['helper_sha256'][kind]
        if digest is not None:
            target = str(WORKER_ROOT / name)
            _require(target in mounts)
            raw = read_private_auth_file(mounts[target]['source'], max_bytes=1024**2)
            _require(hashlib.sha256(raw).hexdigest() == digest)
            readonly.add(target)
    fingerprints = []
    expected_mounts = []
    volumes = []
    for target, mount in mounts.items():
        _require(target in readonly or target in directories or target == '/var/lib/anvil-serving/router-keys')
        if target == '/var/lib/anvil-serving/router-keys':
            _require(mount['type'] == 'volume' and mount.get('read_only', False) is False)
            source = rendered['volumes'][mount['source']]['name']
            volumes.append(source)
        elif target in directories:
            _require(mount['type'] == 'bind' and mount.get('read_only', False) is False
                     and mount['source'] == target)
            from .router.keys import _secure_directory
            _secure_directory(Path(target), create=False)
            source = mount['source']
        else:
            _require(mount['type'] == 'bind' and mount.get('read_only') is True)
            source = mount['source']
            raw = read_private_auth_file(source, max_bytes=2*1024**2)
            fingerprints.append((target, hashlib.sha256(raw).hexdigest()))
        expected_mounts.append((mount['type'], source, target, mount.get('read_only', False)))
    _require(len(volumes) == 1 and all(p in mounts for p in directories))
    image = _run(['docker', 'image', 'inspect', '--format', '{{.Id}}', selected['image']],
                           capture_output=True, text=True, timeout=5)
    _require(image.returncode == 0 and image.stdout.strip() == namespace['expected_image_id'])
    return {'inputs': sorted(fingerprints), 'mounts': sorted(expected_mounts),
            'job_sha256': hashlib.sha256(canonical(job)).hexdigest(), 'declaration_sha256': declaration_sha}


@_serving_authority_mutation
def enroll_clients_offline(namespace, action, declaration_sha, *, confirm=False, dry_run=False,
                           _run=subprocess.run, _popen=subprocess.Popen, _client_auth=None):
    """One fixed native enrollment worker; never activates router or clients."""
    import uuid
    from .client_identity import _require
    helper = 'router-clients-' + uuid.uuid4().hex
    compose = namespace['compose']
    def observation():
        roster = _offline_compose_roster(compose, DEFAULT_SERVICE, namespace['container'], _run=_run,
                                        helper_container=helper)
        inputs = _client_worker_inputs(roster, namespace, declaration_sha, _run=_run)
        custody = _restart_custody(helper, _run)
        if custody.get('available') is True:
            _require(custody['image_id'] == namespace['expected_image_id'])
            actual = [(m['type'], m['name'] if m['type'] == 'volume' else m['source'],
                       m['destination'], m['read_only']) for m in custody['mounts']]
            _require(sorted(actual) == inputs['mounts'])
        return {'roster': roster, 'inputs': inputs}
    code = ('from anvil_serving.client_identity import _native_offline_clients; '
            f'_native_offline_clients({action!r},{namespace["declaration_path"]!r},'
            f'confirm={confirm!r},dry_run={dry_run!r})')
    argv = [*_compose_argv(compose), 'run', '--rm', '--no-deps', '-T', '--name', helper,
            '--entrypoint', 'python', DEFAULT_SERVICE, '-c', code]
    try:
        result = _native_owner_commit(argv, observation, _popen=_popen, _clients=True, _client_auth=_client_auth)
        _require(result['run_id'] == declaration_sha)
        return result['result']
    finally:
        # EOF normally unwinds custody and --rm removes the worker. On failure
        # remove only this exact owned helper after native image/label proof.
        state, project = _container_compose_project(helper, _run=_run)
        if state != 'absent':
            owned = _restart_custody(helper, _run)
            _require(project == DEFAULT_COMPOSE_PROJECT and owned.get('available') is True
                     and owned['compose_service'] == DEFAULT_SERVICE
                     and owned['image_id'] == namespace['expected_image_id'])
            _run(['docker', 'rm', '--force', owned['container_id']],
                 capture_output=True, text=True, timeout=10)
            # --rm may finish between inspection and exact-ID cleanup. Only
            # native absence of BOTH the captured ID and its name proves STOP.
            _require(docker_state(owned['container_id'], _run=_run) == 'absent'
                     and _container_compose_project(helper, _run=_run)[0] == 'absent')
