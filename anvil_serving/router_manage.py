"""Bounded lifecycle and transition controls for the deployed router container."""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import guard
from .paths import config_path
from .serves import docker_state
from .transports import _is_safe_controller_ip


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_COMPOSE = os.path.join(REPO, "examples", "fakoli-dark", "docker-compose.yml")
DEFAULT_CONTAINER = "anvil-router"
DEFAULT_SERVICE = "router"
DEFAULT_ROUTER_URL = "http://127.0.0.1:8000"
TRANSITION_PATH = "/v1/admin/transition"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _safe_router_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("router_url must be an HTTP(S) URL")
    import ipaddress
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        raise ValueError("router_url must use a literal private IP address") from None
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
        return path
    return next((candidate for candidate in default_compose_candidates() if os.path.isfile(os.path.expanduser(candidate))), DEFAULT_COMPOSE)


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


def cmd_up(compose, service, env_file=None, dry_run=False, _run=subprocess.run):
    argv = ["docker", "compose"]
    if env_file:
        argv += ["--env-file", os.path.abspath(os.path.expanduser(env_file))]
    return _run_argv(argv + ["-f", compose, "up", "-d", "--no-deps", service], _run, dry_run=dry_run)


def cmd_down(compose, service, dry_run=False, _run=subprocess.run):
    return _run_argv(["docker", "compose", "-f", compose, "stop", service], _run, dry_run=dry_run)


def cmd_restart(container, dry_run=False, verify=True, _run=subprocess.run, _sleep=None):
    return _run_argv(["docker", "restart", container], _run, dry_run=dry_run)


def cmd_reload(container, dry_run=False, verify=True, _run=subprocess.run, _sleep=None):
    print("router reload restarts the container because configuration is startup-read")
    return cmd_restart(container, dry_run=dry_run, verify=verify, _run=_run, _sleep=_sleep)


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
    try:
        with _open("http://127.0.0.1:%s/" % port, timeout=3) as response:
            return getattr(response, "status", None) or response.getcode()
    except Exception:
        return None


def status_summary(container, _run=subprocess.run, _open=urllib.request.urlopen, port=8000):
    state = docker_state(container, _run=_run)
    running = state == "running"
    return {"container": container, "docker_state": state, "running": running,
            "health_status": _health(_open, port) if running else None,
            "health_url": "http://127.0.0.1:%s/" % port if running else None,
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
    return parser


def main(argv=None):
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)
    if args.action == "up": return cmd_up(resolve_compose_path(args.compose), args.service, _default_env_file() if args.env_file is None else args.env_file, args.dry_run)
    if args.action == "down": return cmd_down(resolve_compose_path(args.compose), args.service, args.dry_run)
    if args.action in {"restart", "reload"}: return (cmd_restart if args.action == "restart" else cmd_reload)(args.container, args.dry_run, not args.no_verify)
    if args.action == "status": return cmd_status(args.container)
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
