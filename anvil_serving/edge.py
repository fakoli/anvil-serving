"""Own the Tailscale tailnet edge for anvil-serving (ADR-0019).

anvil-serving publishes every serving surface behind the host's single MagicDNS
name. This module renders and applies a ``tailscale serve`` configuration that
path-routes that one name to the appropriate local service:

    /v1/*     -> the existing anvil-router (127.0.0.1:8000), COMPLETELY UNCHANGED
    /comfyui  -> ComfyUI (127.0.0.1:8188), whose live queue needs WebSockets

The edge is a **pure L7 path-router in front of the unchanged router**. It adds
no proxy server in Python: ``tailscale serve`` does the WS-capable proxying
natively, which is the whole reason we use it rather than a stdlib WS proxy
(ADR-0019). Additional ``path -> local port`` mappings (future dashboards, the
anvil dashboard on :8766, ...) are driven by config, not hardcoded.

Design invariants:

* **Stdlib-only.** ``tomllib`` + ``subprocess`` + ``json``. No runtime deps, no
  in-process proxy. The router stays a pure inference gateway; the WS-capable
  proxying lives in Tailscale.
* **Never touch the router request path.** Each managed mount forwards its path
  to the target verbatim; ``tailscale serve`` appends the mount to the MagicDNS
  base URL and proxies the request through, so ``/v1/models`` reaches the router
  as ``/v1/models``. The OpenAI/Anthropic contract is preserved because the edge
  rewrites nothing.
* **Additive and idempotent.** ``up`` sets only the mounts this tool manages.
  ``down`` removes only mounts this tool manages *and* that currently point at
  our target — it never clobbers an operator-set ``tailscale serve`` mapping
  (e.g. a dashboard already parked at ``/``).
* **Dry-run first.** ``render`` and ``--dry-run`` print the exact
  ``tailscale serve`` invocations without applying them.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .guard import confirmation_authorized


SCHEMA_VERSION = 1
DEFAULT_HTTPS_PORT = 443
DEFAULT_TARGET_HOST = "127.0.0.1"
DEFAULT_TIMEOUT_SECONDS = 15.0

# The canonical route map (ADR-0019). Targets default to loopback because the
# canonical deployment binds each service to 127.0.0.1 and lets the tailnet edge
# front it; an operator whose router is bound to a tailnet interface can override
# the target host per route via config.
DEFAULT_ROUTES: tuple[tuple[str, int], ...] = (
    ("/v1", 8000),        # anvil-router: the whole OpenAI/Anthropic /v1 surface
    ("/comfyui", 8188),   # ComfyUI web UI + live-queue WebSocket
)


class EdgeConfigError(ValueError):
    """The edge route configuration is malformed."""


@dataclass(frozen=True)
class EdgeRoute:
    """One managed ``mount -> target`` mapping under the single MagicDNS name."""

    mount: str
    target: str

    def __post_init__(self) -> None:
        if not isinstance(self.mount, str) or not self.mount.startswith("/"):
            raise EdgeConfigError(f"route mount must be an absolute path, got {self.mount!r}")
        if self.mount != "/" and self.mount.endswith("/"):
            # Normalize a trailing slash so status parsing and off-removal match.
            object.__setattr__(self, "mount", self.mount.rstrip("/"))
        if not isinstance(self.target, str) or "://" not in self.target:
            raise EdgeConfigError(f"route target must be a full URL, got {self.target!r}")


@dataclass(frozen=True)
class EdgeConfig:
    """The resolved edge configuration: an HTTPS port and an ordered route map."""

    https_port: int
    routes: tuple[EdgeRoute, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.https_port, int) or not 0 < self.https_port <= 65535:
            raise EdgeConfigError(f"https_port must be 1-65535, got {self.https_port!r}")
        seen: set[str] = set()
        for route in self.routes:
            if route.mount in seen:
                raise EdgeConfigError(f"duplicate managed mount {route.mount!r}")
            seen.add(route.mount)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def _target_from_value(value: object, *, host: str) -> str:
    """Build a target URL from a config value.

    An int (or numeric string) is a local port on ``host``; a string containing
    ``://`` is used verbatim as a full target URL.
    """
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        raise EdgeConfigError(f"route target may not be a boolean, got {value!r}")
    if isinstance(value, int):
        if not 0 < value <= 65535:
            raise EdgeConfigError(f"route port must be 1-65535, got {value!r}")
        return f"http://{host}:{value}"
    if isinstance(value, str):
        text = value.strip()
        if "://" in text:
            return text
        if text.isdigit():
            return _target_from_value(int(text), host=host)
        raise EdgeConfigError(f"route target must be a port or full URL, got {value!r}")
    raise EdgeConfigError(f"route target must be a port or full URL, got {value!r}")


def default_config(*, https_port: int = DEFAULT_HTTPS_PORT, host: str = DEFAULT_TARGET_HOST) -> EdgeConfig:
    """The built-in ADR-0019 route map (``/v1`` + ``/comfyui``)."""
    return EdgeConfig(
        https_port=https_port,
        routes=tuple(EdgeRoute(mount, _target_from_value(port, host=host)) for mount, port in DEFAULT_ROUTES),
    )


def load_config(
    path: str | Path | None,
    *,
    https_port: int | None = None,
    host: str = DEFAULT_TARGET_HOST,
    extra_maps: Sequence[str] = (),
) -> EdgeConfig:
    """Resolve the edge config from defaults, an optional TOML file, and ``--map`` overrides.

    TOML shape (all keys optional; absent file -> defaults)::

        [edge]
        https_port = 443
        host = "127.0.0.1"          # default target host for port-only routes

        [edge.routes]
        "/v1" = 8000                # port on host
        "/comfyui" = 8188
        "/dashboard" = 8766         # extensible: future dashboards
        "/foo" = "http://127.0.0.1:9000/foo"   # or a full target URL

    ``--map MOUNT=TARGET`` overrides (repeatable) win over the file and defaults;
    ``MOUNT=off`` drops a route entirely (e.g. to suppress a default mount).
    """
    file_host = host
    file_https_port: int | None = None
    routes: dict[str, str] = {}

    if path is not None:
        data = _read_toml(path)
        section = data.get("edge", data)
        if not isinstance(section, Mapping):
            raise EdgeConfigError("[edge] must be a table")
        if "host" in section:
            file_host = str(section["host"])
        if "https_port" in section:
            file_https_port = _coerce_port(section["https_port"], "https_port")
        raw_routes = section.get("routes", {})
        if not isinstance(raw_routes, Mapping):
            raise EdgeConfigError("[edge.routes] must be a table of mount -> port/URL")
        for mount, value in raw_routes.items():
            routes[_normalize_mount(mount)] = _target_from_value(value, host=file_host)

    if not routes and path is None:
        for mount, port in DEFAULT_ROUTES:
            routes[mount] = _target_from_value(port, host=file_host)

    for item in extra_maps:
        if "=" not in item:
            raise EdgeConfigError(f"--map expects MOUNT=TARGET, got {item!r}")
        mount_raw, value = item.split("=", 1)
        mount = _normalize_mount(mount_raw)
        if value.strip().lower() == "off":
            routes.pop(mount, None)
            continue
        routes[mount] = _target_from_value(value, host=file_host)

    resolved_port = https_port if https_port is not None else (file_https_port if file_https_port is not None else DEFAULT_HTTPS_PORT)
    return EdgeConfig(
        https_port=resolved_port,
        routes=tuple(EdgeRoute(mount, target) for mount, target in routes.items()),
    )


def _read_toml(path: str | Path) -> Mapping[str, object]:
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise EdgeConfigError(f"config not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise EdgeConfigError(f"invalid TOML in {path}: {exc}") from exc


def _coerce_port(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EdgeConfigError(f"{name} must be an integer, got {value!r}")
    return value


def _normalize_mount(mount: object) -> str:
    text = str(mount).strip()
    if not text.startswith("/"):
        raise EdgeConfigError(f"mount must be an absolute path, got {mount!r}")
    if text != "/":
        text = text.rstrip("/")
    return text


# --------------------------------------------------------------------------- #
# render (route map -> tailscale serve invocations)
# --------------------------------------------------------------------------- #
def serve_up_argv(route: EdgeRoute, *, https_port: int) -> list[str]:
    """The ``tailscale serve`` invocation that binds one managed mount.

    ``--set-path`` appends the mount to the node's MagicDNS base URL and proxies
    the (unmodified) request path to the target. Root ("/") needs no --set-path.
    """
    argv = ["tailscale", "serve", "--bg", f"--https={https_port}"]
    if route.mount != "/":
        argv.append(f"--set-path={route.mount}")
    argv.append(route.target)
    return argv


def serve_off_argv(mount: str, *, https_port: int) -> list[str]:
    """The ``tailscale serve`` invocation that removes ONE managed mount.

    Per-path removal only — never ``tailscale serve reset`` (which would clobber
    every mapping on the node, including operator-set ones).
    """
    argv = ["tailscale", "serve", f"--https={https_port}"]
    if mount != "/":
        argv.append(f"--set-path={mount}")
    argv.append("off")
    return argv


def render_up_plan(config: EdgeConfig) -> list[list[str]]:
    """Every ``tailscale serve`` invocation that ``up`` would apply, in order."""
    return [serve_up_argv(route, https_port=config.https_port) for route in config.routes]


# --------------------------------------------------------------------------- #
# tailscale serve status parsing
# --------------------------------------------------------------------------- #
def parse_serve_status(data: Mapping[str, object], *, https_port: int) -> dict[str, str]:
    """Parse ``tailscale serve status --json`` into ``{mount: target}``.

    Collapses every Web host whose listener is on ``https_port`` (the node's own
    MagicDNS name is the only HTTPS host in practice) into one mount->target map.
    """
    result: dict[str, str] = {}
    web = data.get("Web")
    if not isinstance(web, Mapping):
        return result
    suffix = f":{https_port}"
    for host_key, host_conf in web.items():
        if not isinstance(host_key, str) or not host_key.endswith(suffix):
            continue
        if not isinstance(host_conf, Mapping):
            continue
        handlers = host_conf.get("Handlers")
        if not isinstance(handlers, Mapping):
            continue
        for mount, handler in handlers.items():
            if not isinstance(mount, str) or not isinstance(handler, Mapping):
                continue
            proxy = handler.get("Proxy")
            if isinstance(proxy, str):
                result[_normalize_mount(mount)] = proxy
    return result


def read_serve_status(*, https_port: int, run=subprocess.run, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, str]:
    """Read and parse the live ``tailscale serve status --json``.

    Returns an empty map when tailscale is absent or serve is unconfigured — an
    unconfigured node is a valid (empty) state, not an error.
    """
    try:
        completed = run(
            ["tailscale", "serve", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {}
    except subprocess.TimeoutExpired:
        return {}
    stdout = (getattr(completed, "stdout", "") or "").strip()
    if getattr(completed, "returncode", 1) != 0 or not stdout:
        return {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, Mapping):
        return {}
    return parse_serve_status(data, https_port=https_port)


# --------------------------------------------------------------------------- #
# down (remove ONLY the mounts this tool manages)
# --------------------------------------------------------------------------- #
def plan_down(config: EdgeConfig, current: Mapping[str, str]) -> list[list[str]]:
    """The ``tailscale serve`` off-invocations ``down`` would apply.

    A managed mount is removed ONLY when it is currently present AND its live
    proxy target matches this tool's configured target. That double condition is
    the "never clobber operator-set mappings" guarantee: an operator mapping that
    happens to share a path (or a mount we do not manage) is left untouched.
    Idempotent: a managed mount that is absent is skipped.
    """
    plan: list[list[str]] = []
    for route in config.routes:
        live_target = current.get(route.mount)
        if live_target is not None and live_target == route.target:
            plan.append(serve_off_argv(route.mount, https_port=config.https_port))
    return plan


def classify_status(config: EdgeConfig, current: Mapping[str, str]) -> dict[str, object]:
    """Annotate the live serve status against the configured route map."""
    managed_targets = {route.mount: route.target for route in config.routes}
    mounts: list[dict[str, object]] = []
    for mount in sorted(set(current) | set(managed_targets)):
        live = current.get(mount)
        configured = managed_targets.get(mount)
        managed = configured is not None and live == configured
        mounts.append(
            {
                "mount": mount,
                "live_target": live,
                "configured_target": configured,
                "managed": managed,
                "present": live is not None,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "https_port": config.https_port,
        "mounts": mounts,
    }


# --------------------------------------------------------------------------- #
# MagicDNS name
# --------------------------------------------------------------------------- #
def resolve_magicdns_name(*, run=subprocess.run, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str | None:
    """Read the node's single MagicDNS name from tailscale (never hardcoded).

    ``.Self.DNSName`` carries a trailing dot; strip it for URL use.
    """
    try:
        completed = run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if getattr(completed, "returncode", 1) != 0:
        return None
    try:
        data = json.loads((getattr(completed, "stdout", "") or "").strip() or "{}")
    except json.JSONDecodeError:
        return None
    name = (((data.get("Self") or {}) if isinstance(data, Mapping) else {}) or {}).get("DNSName")
    if isinstance(name, str) and name:
        return name.rstrip(".")
    return None


def _format_argv(argv: Sequence[str]) -> str:
    return " ".join(argv)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _apply_plan(plan: Sequence[Sequence[str]], *, run, timeout: float) -> tuple[int, list[dict[str, object]]]:
    results: list[dict[str, object]] = []
    exit_code = 0
    for argv in plan:
        try:
            completed = run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
        except FileNotFoundError:
            results.append({"command": list(argv), "status": "tailscale-missing"})
            return 1, results
        except subprocess.TimeoutExpired:
            results.append({"command": list(argv), "status": "timeout"})
            exit_code = 1
            continue
        rc = getattr(completed, "returncode", 1)
        results.append(
            {
                "command": list(argv),
                "status": "ok" if rc == 0 else "error",
                "returncode": rc,
                "stderr": (getattr(completed, "stderr", "") or "").strip(),
            }
        )
        if rc != 0:
            exit_code = 1
    return exit_code, results


def _emit(payload: Mapping[str, object], *, json_mode: bool) -> None:
    print(json.dumps(payload, sort_keys=True) if json_mode else _human(payload))


def _human(payload: Mapping[str, object]) -> str:
    lines: list[str] = []
    action = payload.get("action")
    endpoint = payload.get("endpoint")
    if endpoint:
        lines.append(f"MagicDNS endpoint: {endpoint}")
    plan = payload.get("plan")
    if isinstance(plan, list) and plan:
        header = "Would apply:" if payload.get("dry_run") else "Applied:"
        lines.append(header)
        lines.extend(f"  $ {_format_argv(cmd)}" for cmd in plan)
    elif action in {"up", "down", "render"}:
        lines.append("No managed mappings to change.")
    mounts = payload.get("mounts")
    if isinstance(mounts, list) and action == "status":
        if not mounts:
            lines.append("No serve mappings and no configured routes.")
        for entry in mounts:
            flag = "managed" if entry.get("managed") else ("present" if entry.get("present") else "configured (absent)")
            lines.append(f"  {entry['mount']:<12} -> {entry.get('live_target') or entry.get('configured_target')}  [{flag}]")
    note = payload.get("note")
    if note:
        lines.append(str(note))
    return "\n".join(lines) if lines else "(nothing to report)"


def _config_from_args(args: argparse.Namespace) -> EdgeConfig:
    return load_config(
        args.config,
        https_port=args.https_port,
        host=args.host,
        extra_maps=tuple(args.map or ()),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving edge",
        description="Own the Tailscale tailnet edge: path-route the single MagicDNS name to local services.",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", help="Edge route TOML ([edge]/[edge.routes]).", default=None)
        p.add_argument("--https-port", dest="https_port", type=int, default=None, help="Node HTTPS listener port (default 443).")
        p.add_argument("--host", default=DEFAULT_TARGET_HOST, help="Default target host for port-only routes.")
        p.add_argument("--map", action="append", metavar="MOUNT=TARGET", help="Override/add a route (repeatable); MOUNT=off drops one.")
        p.add_argument("--json", action="store_true", help="Emit the machine-readable envelope.")

    for name, summary in (
        ("render", "Render the tailscale serve invocations without applying."),
        ("status", "Show serve mappings, flagging which this tool manages."),
        ("up", "Apply the managed route map (additive; idempotent)."),
        ("down", "Remove ONLY the mounts this tool manages."),
    ):
        p = sub.add_parser(name, help=summary)
        add_common(p)
        if name in {"up", "down"}:
            p.add_argument("--dry-run", action="store_true", help="Print the plan; apply nothing.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    json_mode = bool(getattr(args, "json", False))
    try:
        config = _config_from_args(args)
    except EdgeConfigError as exc:
        _emit({"ok": False, "action": args.action, "error": str(exc)}, json_mode=json_mode)
        return 2

    endpoint = None
    name = resolve_magicdns_name()
    if name:
        endpoint = f"https://{name}" if config.https_port == 443 else f"https://{name}:{config.https_port}"

    if args.action == "render":
        plan = render_up_plan(config)
        _emit({"ok": True, "action": "render", "dry_run": True, "endpoint": endpoint, "plan": plan}, json_mode=json_mode)
        return 0

    if args.action == "status":
        current = read_serve_status(https_port=config.https_port)
        payload = {"ok": True, "action": "status", "endpoint": endpoint, **classify_status(config, current)}
        _emit(payload, json_mode=json_mode)
        return 0

    if args.action == "up":
        plan = render_up_plan(config)
        if args.dry_run:
            _emit({"ok": True, "action": "up", "dry_run": True, "endpoint": endpoint, "plan": plan}, json_mode=json_mode)
            return 0
        if not confirmation_authorized():
            _emit(
                {
                    "ok": False,
                    "action": "up",
                    "endpoint": endpoint,
                    "plan": plan,
                    "note": "Refusing to mutate tailscale serve without --confirm (use --dry-run to preview).",
                },
                json_mode=json_mode,
            )
            return 1
        exit_code, results = _apply_plan(plan, run=subprocess.run, timeout=DEFAULT_TIMEOUT_SECONDS)
        _emit({"ok": exit_code == 0, "action": "up", "dry_run": False, "endpoint": endpoint, "plan": plan, "results": results}, json_mode=json_mode)
        return exit_code

    if args.action == "down":
        current = read_serve_status(https_port=config.https_port)
        plan = plan_down(config, current)
        if args.dry_run:
            _emit({"ok": True, "action": "down", "dry_run": True, "endpoint": endpoint, "plan": plan}, json_mode=json_mode)
            return 0
        if not plan:
            _emit({"ok": True, "action": "down", "dry_run": False, "endpoint": endpoint, "plan": plan, "note": "No managed mappings present; nothing to remove."}, json_mode=json_mode)
            return 0
        if not confirmation_authorized():
            _emit(
                {
                    "ok": False,
                    "action": "down",
                    "endpoint": endpoint,
                    "plan": plan,
                    "note": "Refusing to mutate tailscale serve without --confirm (use --dry-run to preview).",
                },
                json_mode=json_mode,
            )
            return 1
        exit_code, results = _apply_plan(plan, run=subprocess.run, timeout=DEFAULT_TIMEOUT_SECONDS)
        _emit({"ok": exit_code == 0, "action": "down", "dry_run": False, "endpoint": endpoint, "plan": plan, "results": results}, json_mode=json_mode)
        return exit_code

    parser.error(f"unknown action {args.action!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
