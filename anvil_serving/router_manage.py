"""Bounded lifecycle and transition controls for the deployed router container."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import guard
from .paths import config_path, runtime_url
from .serves import docker_state
from .transports import _is_safe_controller_ip


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_COMPOSE = os.path.join(REPO, "examples", "fakoli-dark", "docker-compose.yml")
DEFAULT_COMPOSE_PROJECT = "anvil-serving"
DEFAULT_CONTAINER = "anvil-router"
DEFAULT_SERVICE = "router"
DEFAULT_ROUTER_URL = "http://127.0.0.1:8000"
TRANSITION_PATH = "/v1/admin/transition"


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


def transition_request(action, *, tier_id=None, timeout=None, router_url=None,
                       confirm=False, dry_run=True, reason="operator", env=None, _open=None):
    if action not in ("status", "quiesce", "drain", "readmit"):
        raise ValueError("unsupported transition action")
    if action != "status" and not tier_id:
        raise ValueError("tier_id is required")
    base = _safe_router_url(router_url or (env or os.environ).get("ANVIL_ROUTER_URL") or DEFAULT_ROUTER_URL)
    if action in ("quiesce", "readmit") and (not confirm or dry_run):
        return {"applied": False, "dry_run": True, "action": action, "tier_id": tier_id, "router_url": base}
    token = (env or os.environ).get("ANVIL_ROUTER_TOKEN") or ""
    if not token:
        raise ValueError("ANVIL_ROUTER_TOKEN is required")
    headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
    if action == "status":
        suffix = "" if not tier_id else "?" + urllib.parse.urlencode({"tier_id": tier_id})
        request = urllib.request.Request(base + TRANSITION_PATH + suffix, headers=headers)
        request_timeout = 5.0
    else:
        body = {"action": action, "tier_id": tier_id, "confirm": bool(confirm), "dry_run": bool(dry_run), "reason": reason}
        request_timeout = 5.0
        if action == "drain":
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
                raise ValueError("timeout must be between 0 and 3600 seconds")
            body["timeout"] = float(timeout)
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
        result = json.loads(raw)
    except ValueError:
        raise ValueError("router transition response was malformed") from None
    if not isinstance(result, dict):
        raise ValueError("router transition response was malformed")
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


def _run_argv(argv, _run, *, dry_run=False):
    if dry_run:
        return 0
    try:
        result = _run(argv, capture_output=True, text=True)
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


def cmd_up(
    compose,
    service,
    env_file=None,
    dry_run=False,
    _run=subprocess.run,
    recreate=False,
    container=DEFAULT_CONTAINER,
):
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
            remove_rc = _run_argv(
                ["docker", "rm", "-f", container],
                _run,
            )
            if remove_rc:
                return remove_rc
    return _run_argv(
        _compose_up_argv(compose, service, env_file=env_file, recreate=recreate),
        _run,
        dry_run=dry_run,
    )


def cmd_down(compose, service, dry_run=False, _run=subprocess.run):
    return _run_argv(
        [*_compose_argv(compose), "stop", service],
        _run,
        dry_run=dry_run,
    )


def cmd_restart(container, dry_run=False, verify=True, _run=subprocess.run, _sleep=None):
    return _run_argv(["docker", "restart", container], _run, dry_run=dry_run)


def cmd_reload(container, dry_run=False, verify=True, _run=subprocess.run, _sleep=None):
    print("router reload restarts the container because configuration is startup-read")
    return cmd_restart(container, dry_run=dry_run, verify=verify, _run=_run, _sleep=_sleep)


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


def status_summary(container, _run=subprocess.run, _open=urllib.request.urlopen, port=8000):
    state = docker_state(container, _run=_run)
    running = state == "running"
    return {"container": container, "docker_state": state, "running": running,
            "health_status": _health(_open, port) if running else None,
            "health_url": runtime_url("http://127.0.0.1:%s/" % port) if running else None,
            "ok": state != "error"}


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


def install_config(
    config_file,
    *,
    router_url=None,
    drain_timeout=120,
    confirm=False,
    dry_run=True,
    _transition=transition_request,
    _install=None,
    _sleep=time.sleep,
):
    """Safely replace a deployed router config even when its tier set changes.

    Installation verifies that the restarted router exposes the exact desired
    tier IDs.  Per-tier readiness is reported, but an intentionally stopped or
    otherwise unavailable serve does not make a structurally successful config
    installation fail.
    """
    from .router.config import load
    from .serves import _install_router_config

    selected = os.path.abspath(os.path.expanduser(config_file))
    desired = [tier.id for tier in load(selected).tiers]
    status = _transition("status", router_url=router_url)
    rows = status.get("tiers", [])
    if not isinstance(rows, list):
        raise ValueError("router transition status was malformed")
    current = [
        row.get("tier_id") for row in rows
        if isinstance(row, dict) and isinstance(row.get("tier_id"), str)
    ]
    plan = {
        "config": selected,
        "router_url": _safe_router_url(router_url or DEFAULT_ROUTER_URL),
        "current_tiers": current,
        "desired_tiers": desired,
        "drain_timeout": drain_timeout,
    }
    if dry_run or not confirm:
        return {"applied": False, "dry_run": True, **plan}

    quiesced = []
    try:
        for tier_id in current:
            _transition(
                "quiesce", tier_id=tier_id, router_url=router_url,
                confirm=True, dry_run=False,
            )
            quiesced.append(tier_id)
        for tier_id in current:
            result = _transition(
                "drain", tier_id=tier_id, timeout=drain_timeout,
                router_url=router_url, confirm=True, dry_run=False,
            )
            payload = result.get("result", result)
            if not isinstance(payload, dict) or not payload.get("drained", False):
                raise ValueError("router tier %r did not drain" % tier_id)
    except Exception:
        for tier_id in reversed(quiesced):
            try:
                _transition(
                    "readmit", tier_id=tier_id, router_url=router_url,
                    confirm=True, dry_run=False,
                )
            except Exception:
                pass
        raise

    installer = _install or _install_router_config
    if installer(selected) != 0:
        raise ValueError("router config install failed or was rolled back")

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
            well_formed = len(tier_ids) == len(set(tier_ids))
            if well_formed and set(tier_ids) == set(desired):
                unavailable = [
                    tier_id for tier_id, row in zip(tier_ids, post_rows)
                    if row.get("ready") is not True
                ]
                return {
                    "applied": True,
                    "dry_run": False,
                    "tier_status": post_rows,
                    "unavailable_tiers": unavailable,
                    **plan,
                }
        except ValueError:
            pass
        if time.monotonic() >= deadline:
            raise ValueError("installed router config did not expose the desired tier set")
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
    fleet.add_argument("--config", help="router config TOML (default: config home).")
    fleet.add_argument("--json", action="store_true", dest="json_out",
                       help="emit the report as JSON for tooling.")
    fleet.add_argument("--timeout", type=float, default=4.0,
                       help="per-endpoint probe timeout in seconds (default: 4).")
    for name in ("restart", "reload"):
        item = actions.add_parser(name)
        item.add_argument("--container", default=DEFAULT_CONTAINER)
        item.add_argument("--dry-run", action="store_true")
        item.add_argument("--no-verify", action="store_true")
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
        item.add_argument("--tier", required=action != "transition-status")
        item.add_argument("--router-url")
        if action == "drain":
            item.add_argument("--timeout", type=float, required=True)
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
    install.add_argument("--router-url")
    install.add_argument("--drain-timeout", type=float, default=120)
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
                plan["container"], verify=not args.no_verify
            )
        print(json.dumps({"applied": rc == 0, "dry_run": False, **plan}, sort_keys=True))
        return rc
    if args.action == "status": return cmd_status(args.container)
    if args.action == "fleet-status":
        return cmd_fleet_status(args.config, as_json=args.json_out,
                                timeout=args.timeout)
    if args.action == "install-config":
        confirmed = guard.confirmation_authorized()
        try:
            result = install_config(
                args.config,
                router_url=args.router_url,
                drain_timeout=args.drain_timeout,
                confirm=confirmed,
                dry_run=args.dry_run or not confirmed,
            )
        except ValueError as exc:
            print("router config install failed: %s" % exc, file=sys.stderr)
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
                tier_id=getattr(args, "tier", None),
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


def _host_relative(url):
    """Return (probe_url, translated) for a container-relative endpoint."""
    parsed = urllib.parse.urlparse(url)
    if (parsed.hostname or "").lower() != _DOCKER_HOST_ALIAS:
        return url, False
    netloc = _HOST_RELATIVE_LOOPBACK
    if parsed.port:
        netloc += ":%d" % parsed.port
    return urllib.parse.urlunparse(parsed._replace(netloc=netloc)), True


def _endpoint_host(base_url):
    try:
        return urllib.parse.urlparse(base_url).hostname or "?"
    except ValueError:
        return "?"


def fleet_status(config, timeout=4.0, _probe=_probe_endpoint):
    """Probe every configured capability and report which are actually served.

    Reports aliases (the declared chat vocabulary), purpose models, and audio
    routes. Read-only: no Docker, no mutation, no lifecycle.
    """
    rows = []
    seen = {}

    def _check(base_url, health_path):
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        url += health_path if health_path.startswith("/") else "/" + health_path
        probe_url, translated = _host_relative(url)
        if probe_url not in seen:
            seen[probe_url] = _probe(probe_url, timeout=timeout)
        ok, detail = seen[probe_url]
        if translated:
            detail += " via host-relative loopback"
        return probe_url, ok, detail

    for alias, tier_id in sorted(dict(config.model_routes).items()):
        try:
            tier = config.tier(tier_id)
        except Exception:  # noqa: BLE001 - an unresolvable tier is the finding
            rows.append({"kind": "alias", "name": alias, "target": tier_id,
                         "host": "?", "endpoint": "", "reachable": False,
                         "detail": "alias maps to an undeclared tier"})
            continue
        url, ok, detail = _check(tier.base_url, getattr(tier, "health_path", "/health") or "/health")
        rows.append({"kind": "alias", "name": alias, "target": tier_id,
                     "host": _endpoint_host(tier.base_url), "endpoint": url,
                     "reachable": ok, "detail": detail})

    for purpose in getattr(config, "purpose_models", ()) or ():
        url, ok, detail = _check(purpose.base_url, "/health")
        rows.append({"kind": "purpose", "name": purpose.id, "target": purpose.model,
                     "host": _endpoint_host(purpose.base_url), "endpoint": url,
                     "reachable": ok, "detail": detail})

    for route in getattr(config, "audio_routes", ()) or ():
        url, ok, detail = _check(route.base_url, "/health")
        rows.append({"kind": "audio", "name": route.id, "target": route.purpose,
                     "host": _endpoint_host(route.base_url), "endpoint": url,
                     "reachable": ok, "detail": detail})

    unreachable = [r for r in rows if not r["reachable"]]
    return {
        "rows": rows,
        "checked": len(rows),
        "unreachable": len(unreachable),
        "unreachable_aliases": sorted(
            r["name"] for r in unreachable if r["kind"] == "alias"),
    }


def cmd_fleet_status(config_path_arg=None, as_json=False, timeout=4.0,
                     _probe=_probe_endpoint):
    """Report which configured capabilities have a reachable backing serve."""
    from .doctor import resolve_default_config_path
    from .router import config as router_config

    path = config_path_arg or resolve_default_config_path()
    if not path:
        print("no router config found; pass --config PATH", file=sys.stderr)
        return 2
    try:
        config = router_config.load(path)
    except Exception as exc:  # noqa: BLE001 - surface the load failure verbatim
        print("could not load router config %s: %s" % (path, exc), file=sys.stderr)
        return 2

    report = fleet_status(config, timeout=timeout, _probe=_probe)
    report["config"] = str(path)
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if report["unreachable_aliases"] else 0

    print("%-9s %-16s %-22s %-16s %s" % ("KIND", "NAME", "TARGET", "HOST", "STATE"))
    for row in report["rows"]:
        print("%-9s %-16s %-22s %-16s %s" % (
            row["kind"], row["name"], row["target"], row["host"],
            "ok (%s)" % row["detail"] if row["reachable"]
            else "UNREACHABLE (%s)" % row["detail"]))
    print("\nfleet status: %d configured, %d unreachable" % (
        report["checked"], report["unreachable"]))
    if report["unreachable_aliases"]:
        print("aliases with no reachable backing serve: %s"
              % ", ".join(report["unreachable_aliases"]))
    return 1 if report["unreachable_aliases"] else 0
