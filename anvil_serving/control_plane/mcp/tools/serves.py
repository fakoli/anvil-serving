"""Explicit serves MCP tool family."""

from __future__ import annotations

import re
import sys
import urllib.request
from typing import Optional

from ..arguments import (
    arg_bool as _arg_bool,
    bounded_int_arg as _bounded_int_arg,
    bounded_integer_schema as _bounded_integer_schema,
    schema as _schema,
    str_arg as _str_arg,
    str_list_arg as _str_list_arg,
)
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from ..runtime import (
    run_argv as _run_argv,
    run_argv_spooled as _run_argv_spooled,
)
from ..security import safe_probe_url as _safe_probe_url


_MANIFEST_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _manifest_path(args: dict) -> str:
    from .... import serves as serves_mod
    from ....paths import config_path

    manifest_arg = _str_arg(args, "manifest", "")
    from_operator_home = _arg_bool(
        args.get("manifest_from_operator_home"),
        False,
        name="manifest_from_operator_home",
    )
    if not from_operator_home:
        return serves_mod.resolve_manifest_path(manifest_arg or None)
    if not manifest_arg or not _MANIFEST_NAME_RE.fullmatch(manifest_arg):
        raise ToolError(
            "bad_argument",
            "operator-home manifest must be a safe manifest basename",
        )
    return config_path(manifest_arg)


def tool_serves_status(args: dict) -> dict:
    from .... import serves as serves_mod

    manifest = _manifest_path(args)
    names = args.get("names", [])
    if names is None:
        names = []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ToolError("bad_argument", "'names' must be an array of strings")
    try:
        serves = serves_mod.load_manifest_set(manifest)
    except FileNotFoundError:
        raise ToolError("manifest_not_found", "serves manifest not found", {"manifest": manifest})
    except Exception as exc:
        raise ToolError(
            "bad_manifest",
            "could not load serves manifest",
            {"manifest": manifest, "error": str(exc)},
        )
    return _ok(
        serves_mod.status_summary(
            serves,
            names,
            _open=lambda url, timeout: urllib.request.urlopen(
                _safe_probe_url(url),
                timeout=timeout,
            ),
        )
    )


def tool_reservation_status(args: dict) -> dict:
    from .... import serves as serves_mod

    manifest_arg = _str_arg(args, "manifest", "")
    manifest = serves_mod.resolve_manifest_path(manifest_arg or None)
    serves = _load_serves_for_tool(manifest)
    return _ok(serves_mod.reservation_summary(serves))


def _load_serves_for_tool(manifest: str):
    from .... import serves as serves_mod

    try:
        return serves_mod.load_manifest_set(manifest)
    except FileNotFoundError:
        raise ToolError("manifest_not_found", "serves manifest not found", {"manifest": manifest})
    except Exception as exc:
        raise ToolError(
            "bad_manifest",
            "could not load serves manifest",
            {"manifest": manifest, "error": str(exc)},
        )


def _serves_cli_argv(
    action: str,
    manifest: str,
    names: list[str],
    *,
    dry_run: bool = False,
    recreate: bool = False,
    keep_container: bool = False,
    compose: str = "",
    tail: Optional[int] = None,
    since: str = "",
) -> list[str]:
    argv = [sys.executable, "-m", "anvil_serving.cli", "serves", action]
    if compose:
        argv += ["--compose", compose]
    if dry_run:
        argv.append("--dry-run")
    elif action in {"up", "down", "rm", "adopt"}:
        # MCP's confirm=true + dry_run=false is the operator approval for this
        # exact declared action. Forward it to the resource-owner CLI so its
        # shared confirmation gate authorizes the lifecycle operation and all
        # declared postconditions (including lifecycle cache reclaim).
        argv.append("--confirm")
    if recreate:
        argv.append("--recreate")
    if keep_container:
        argv.append("--keep-container")
    if tail is not None:
        argv += ["--tail", str(tail)]
    if since:
        argv += ["--since", since]
    if action in ("rm", "adopt") and not dry_run:
        # The serves module additionally gates these irreversible actions behind an interactive
        # [y/N] prompt. This subprocess has no TTY (stdin is the JSON-RPC
        # pipe), and the MCP triple gate (confirm=true, dry_run=false) IS the
        # operator's consent — pass it through, or the child EOFs to "No" and
        # every confirmed rm/adopt silently aborts.
        argv.append("--yes")
    if action == "up":
        # A restricted resource-owner controller may manage only the declared
        # serve. Router lifecycle is a separate authority owned by the gateway
        # host, so never inherit the local CLI's co-located-router convenience
        # side effect in this controller subprocess.
        argv.append("--no-router")
    if not compose:
        argv += ["--manifest", manifest]
    argv += names
    return argv


def _apply_gate(
    args: dict,
    *,
    eligible: bool = True,
    requires_human: bool = False,
    human_message: str = "",
) -> tuple[bool, bool, bool]:
    """Parse dry_run/confirm (and human_approved when required) and gate apply.

    Returns ``(dry_run, confirm, apply_requested)``. Raises
    ``human_approval_required`` when an apply is requested (``eligible`` and
    ``confirm`` and not ``dry_run``) but human approval was not given.
    """
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    apply_requested = eligible and confirm and not dry_run
    if requires_human:
        human_approved = _arg_bool(args.get("human_approved"), False, name="human_approved")
        if apply_requested and not human_approved:
            raise ToolError("human_approval_required", human_message)
    return dry_run, confirm, apply_requested


def _dedupe_serves(serves_list: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for serve in serves_list:
        key = serve.get("name"), serve.get("container")
        if key in seen:
            continue
        seen.add(key)
        out.append(serve)
    return out


def _resolve_manifest_serves(
    serves_mod, manifest_serves: list[dict], names: list[str], *, caller: str = "serves_manage"
) -> list[dict]:
    if not names:
        raise ToolError("missing_argument", "%s requires explicit manifest serve names" % caller)
    selected = []
    for name in names:
        matched = serves_mod._select(manifest_serves, [name])
        if not matched:
            raise ToolError(
                "no_matching_serve", "no serve in manifest matches %r" % name, {"name": name}
            )
        if len(matched) > 1:
            raise ToolError(
                "ambiguous_serve",
                "%r matches multiple serves; use an exact container name" % name,
                {"name": name, "matches": [item.get("name") for item in matched]},
            )
        selected.append(matched[0])
    return _dedupe_serves(selected)


def _serves_manage_plan(
    action: str,
    manifest_serves: list[dict],
    names: list[str],
    *,
    compose: str = "",
    recreate: bool = False,
    keep_container: bool = False,
    allow_literal: bool = False,
) -> tuple[list[dict], dict]:
    from .... import serves as serves_mod

    if compose:
        if not names:
            raise ToolError(
                "missing_argument", "compose up through MCP requires explicit service names"
            )
        command = ["docker", "compose", "-f", compose, "up", "-d", *names]
        return [], {
            "mode": "compose",
            "commands": [{"kind": "compose_up", "argv": command}],
            "services": names,
        }

    if action == "rm":
        commands = []
        targets = []
        literal_names = []
        for name in names:
            matched = serves_mod._select(manifest_serves, [name])
            if len(matched) > 1:
                raise ToolError(
                    "ambiguous_serve",
                    "%r matches multiple serves; use an exact container name" % name,
                    {"name": name, "matches": [item.get("name") for item in matched]},
                )
            if matched:
                target = matched[0]
                targets.append(target)
                container = target["container"]
                target_name = target.get("name")
            else:
                if not allow_literal:
                    literal_names.append(name)
                    continue
                container = name
                target_name = name
            commands.append(
                {
                    "kind": "docker_rm",
                    "target": target_name,
                    "argv": ["docker", "rm", "-f", container],
                }
            )
        if literal_names:
            raise ToolError(
                "literal_container_requires_allow",
                "rm of a container not recognized in the manifest requires allow_literal=true",
                {"literal_names": literal_names},
            )
        targets = _dedupe_serves(targets)
        return targets, {
            "mode": "manifest",
            "targets": [
                {
                    "name": item.get("name"),
                    "container": item.get("container"),
                    "manifest_up": item.get("up"),
                }
                for item in targets
            ],
            "commands": commands,
        }

    targets = _resolve_manifest_serves(serves_mod, manifest_serves, names)
    plan = {
        "mode": "manifest",
        "targets": [
            {
                "name": item.get("name"),
                "container": item.get("container"),
                "manifest_up": item.get("up"),
            }
            for item in targets
        ],
        "commands": [],
    }
    if action == "down":
        for item in targets:
            plan["commands"].append(
                {
                    "kind": "docker_stop",
                    "target": item.get("name"),
                    "argv": ["docker", "stop", item["container"]],
                }
            )
            if not keep_container:
                plan["commands"].append(
                    {
                        "kind": "docker_rm_after_stop",
                        "target": item.get("name"),
                        "argv": ["docker", "rm", "-f", item["container"]],
                    }
                )
    elif action == "adopt":
        for item in targets:
            plan["commands"].append(
                {
                    "kind": "docker_rm_before_adopt",
                    "target": item.get("name"),
                    "argv": ["docker", "rm", "-f", item["container"]],
                }
            )
            if item.get("up"):
                plan["commands"].append(
                    {
                        "kind": "manifest_up_after_adopt",
                        "target": item.get("name"),
                        "argv": item["up"],
                    }
                )
    elif action == "up":
        for item in targets:
            if recreate:
                plan["commands"].append(
                    {
                        "kind": "docker_rm_before_recreate",
                        "target": item.get("name"),
                        "argv": ["docker", "rm", "-f", item["container"]],
                    }
                )
            if item.get("up"):
                plan["commands"].append(
                    {
                        "kind": "manifest_up_when_absent_or_compose_reconcile",
                        "target": item.get("name"),
                        "argv": item["up"],
                    }
                )
            plan["commands"].extend(
                [
                    {
                        "kind": "docker_start_when_existing_script_serve_stopped",
                        "target": item.get("name"),
                        "argv": ["docker", "start", item["container"]],
                    },
                    {
                        "kind": "docker_unpause_when_paused",
                        "target": item.get("name"),
                        "argv": ["docker", "unpause", item["container"]],
                    },
                ]
            )
    return targets, plan


def tool_serves_manage(args: dict) -> dict:
    action = _str_arg(args, "action", required=True)
    if action not in {"up", "down", "rm", "adopt"}:
        raise ToolError(
            "bad_action", "action must be one of: up, down, rm, adopt", {"action": action}
        )
    manifest = _manifest_path(args)
    names = _str_list_arg(args, "names")
    compose = _str_arg(args, "compose", "")
    recreate = _arg_bool(args.get("recreate"), False, name="recreate")
    keep_container = _arg_bool(
        args.get("keep_container"), False, name="keep_container"
    )
    allow_literal = _arg_bool(args.get("allow_literal"), False, name="allow_literal")
    dry_run, confirm, _ = _apply_gate(args)
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 300, min_value=1, max_value=7200)

    if compose and action != "up":
        raise ToolError("bad_argument", "'compose' is only valid with action='up'")
    if compose and recreate:
        raise ToolError("bad_argument", "'recreate' has no meaning with compose up")
    if keep_container and action != "down":
        raise ToolError(
            "bad_argument", "'keep_container' is only valid with action='down'"
        )
    if action == "rm" and not names:
        raise ToolError("missing_argument", "rm requires at least one name")

    manifest_serves = []
    if not compose:
        manifest_serves = _load_serves_for_tool(manifest)
    _, plan = _serves_manage_plan(
        action,
        manifest_serves,
        names,
        compose=compose,
        recreate=recreate,
        keep_container=keep_container,
        allow_literal=allow_literal,
    )

    preview = dry_run or not confirm
    argv = _serves_cli_argv(
        action,
        manifest,
        names,
        dry_run=preview,
        recreate=recreate,
        keep_container=keep_container,
        compose=compose,
    )
    target = {
        "action": action,
        "manifest": None if compose else manifest,
        "names": names,
        "compose": compose or None,
        "recreate": recreate,
        "keep_container": keep_container,
        "allow_literal": allow_literal,
        "timeout_seconds": timeout_seconds,
    }
    if preview:
        return _ok(
            {"applied": False, "dry_run": True, "target": target, "command": argv, "plan": plan}
        )
    result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
    return _ok({"applied": True, "dry_run": False, "target": target, "plan": plan, **result})


def tool_serves_promote(args: dict) -> dict:
    from .... import serves as serves_mod

    manifest_arg = _str_arg(args, "manifest", "")
    manifest = serves_mod.resolve_manifest_path(manifest_arg or None)
    plan_name = _str_arg(args, "plan", required=True)
    rollback = _arg_bool(args.get("rollback"), False, name="rollback")
    resume = _arg_bool(args.get("resume"), False, name="resume")
    dry_run, confirm, apply_requested = _apply_gate(
        args,
        requires_human=True,
        human_message=(
            "serve promotion apply requires confirm=true, dry_run=false, and human_approved=true"
        ),
    )
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 7200, min_value=1, max_value=14400)
    argv = [
        sys.executable,
        "-m",
        "anvil_serving.cli",
        "serves",
        "promote",
        plan_name,
        "--manifest",
        manifest,
    ]
    if rollback:
        argv.append("--rollback")
    if resume:
        argv.append("--resume")
    if not apply_requested:
        argv.append("--dry-run")
        # Execute the canonical dry-run on the serving host.  This validates
        # manifest resolution and topology and returns the same ordered plan a
        # local operator reviews, while remaining non-mutating.
        result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
        return _ok(
            {
                "applied": False,
                "dry_run": True,
                "human_gate_required": True,
                "manifest": manifest,
                "plan": plan_name,
                **result,
            }
        )
    argv.append("--confirm")
    result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
    return _ok(
        {
            "applied": True,
            "dry_run": False,
            "human_approved": True,
            "manifest": manifest,
            "plan": plan_name,
            **result,
        }
    )


def tool_serves_mode(args: dict) -> dict:
    """Structured status/preview plus separately gated mode mutation."""
    from .... import serves as serves_mod

    action = _str_arg(args, "action", required=True)
    if action not in {"status", "preview", "enter", "leave"}:
        raise ToolError(
            "bad_action",
            "action must be one of: status, preview, enter, leave",
            {"action": action},
        )
    preserve_on_failure = _arg_bool(
        args.get("preserve_on_failure"),
        False,
        name="preserve_on_failure",
    )
    if preserve_on_failure and action != "enter":
        raise ToolError(
            "bad_argument",
            "preserve_on_failure is only valid with action='enter'",
        )
    manifest_arg = _str_arg(args, "manifest", "")
    manifest = serves_mod.resolve_manifest_path(manifest_arg or None)
    manifest_serves = _load_serves_for_tool(manifest)
    if action == "status":
        if args.get("target") or args.get("restore_group"):
            raise ToolError(
                "bad_argument", "status does not accept target or restore_group"
            )
        states = {}

        def state_of(container):
            if container not in states:
                states[container] = serves_mod.docker_state(container)
            return states[container]

        return _ok({
            "operating_mode": serves_mod.operating_mode_summary(
                manifest_serves, state_of
            ),
            "reservations": serves_mod.reservation_summary(
                manifest_serves, _states=states
            ),
        })

    target = _str_arg(args, "target", required=True)
    restore_group = _str_arg(args, "restore_group", required=True)
    timeout_seconds = _bounded_int_arg(
        args, "timeout_seconds", 1800, min_value=1, max_value=7200
    )
    drain_timeout = _bounded_int_arg(
        args, "drain_timeout", 120, min_value=1, max_value=3600
    )
    try:
        plan = serves_mod.operating_mode_plan(
            manifest_serves,
            target,
            restore_group,
            lambda container: serves_mod.docker_state(container),
        )
    except ValueError as exc:
        raise ToolError("mode_plan_refused", str(exc))

    _dry_run, _confirm, apply_requested = _apply_gate(
        args,
        eligible=action in {"enter", "leave"},
        requires_human=True,
        human_message=(
            "live mode mutation requires confirm=true, dry_run=false, and "
            "human_approved=true"
        ),
    )
    if action == "preview" or not apply_requested:
        return _ok({
            "applied": False,
            "dry_run": True,
            "human_gate_required": action in {"enter", "leave"},
            "preserve_on_failure": preserve_on_failure,
            "manifest": manifest,
            "plan": plan,
        })

    argv = [
        sys.executable,
        "-m",
        "anvil_serving.cli",
        "serves",
        "mode",
        action,
        target,
        "--manifest",
        manifest,
        "--restore-group",
        restore_group,
        "--drain-timeout",
        str(drain_timeout),
        "--confirm",
    ]
    if preserve_on_failure:
        argv.append("--preserve-on-failure")
    result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
    return _ok({
        "applied": True,
        "dry_run": False,
        "human_approved": True,
        "preserve_on_failure": preserve_on_failure,
        "manifest": manifest,
        "plan": plan,
        **result,
    })


def tool_serves_logs(args: dict) -> dict:
    from .... import serves as serves_mod

    manifest = _manifest_path(args)
    names = _str_list_arg(args, "names")
    if len(names) != 1:
        raise ToolError(
            "bad_argument", "serves_logs requires exactly one serve name", {"names": names}
        )
    follow = _arg_bool(args.get("follow"), False, name="follow")
    if follow:
        raise ToolError(
            "follow_not_allowed", "serves_logs rejects unbounded follow mode; use a bounded tail"
        )
    tail = _bounded_int_arg(args, "tail", 200, min_value=1, max_value=5000)
    max_output_bytes = _bounded_int_arg(
        args, "max_output_bytes", 65536, min_value=1024, max_value=1048576
    )
    since = _str_arg(args, "since", "")
    manifest_serves = _load_serves_for_tool(manifest)
    _resolve_manifest_serves(serves_mod, manifest_serves, names, caller="serves_logs")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 60, min_value=1, max_value=600)
    argv = _serves_cli_argv("logs", manifest, names, tail=tail, since=since)
    result = _run_argv_spooled(argv, timeout=timeout_seconds, max_output_bytes=max_output_bytes)
    return _ok(
        {
            "bounded": True,
            "tail": tail,
            "since": since or None,
            "max_output_bytes": max_output_bytes,
            **result,
        }
    )


FAMILY = ToolFamily(
    name="serves",
    tools={
        "serves_status": {
            "description": "Inspect model serves from a serves.toml manifest.",
            "inputSchema": _schema(
                {
                    "manifest": {"type": "string"},
                    "manifest_from_operator_home": {"type": "boolean"},
                    "names": {"type": "array", "items": {"type": "string"}},
                }
            ),
            "handler": tool_serves_status,
        },
        "reservation_status": {
            "description": "Return the read-only per-gpu_role VRAM reservation ledger (ADR-0017) derived from the serves manifest and docker state.",
            "inputSchema": _schema(
                {
                    "manifest": {"type": "string"},
                }
            ),
            "handler": tool_reservation_status,
        },
        "serves_manage": {
            "description": "Preview or run guarded serve lifecycle actions: up, down, rm, or adopt.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string"},
                    "manifest": {"type": "string"},
                    "manifest_from_operator_home": {"type": "boolean"},
                    "names": {"type": "array", "items": {"type": "string"}},
                    "compose": {"type": "string"},
                    "recreate": {"type": "boolean"},
                    "keep_container": {"type": "boolean"},
                    "allow_literal": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 7200, 300),
                },
                required=["action"],
            ),
            "handler": tool_serves_manage,
        },
        "serves_promote": {
            "description": "Preview or execute the complete guarded serve promotion/rollback transaction.",
            "inputSchema": _schema(
                {
                    "manifest": {"type": "string"},
                    "plan": {"type": "string"},
                    "rollback": {"type": "boolean"},
                    "resume": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "human_approved": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 14400, 7200),
                },
                required=["plan"],
            ),
            "handler": tool_serves_promote,
        },
        "serves_mode": {
            "description": "Report, preview, or execute the exclusive TP=2 operating-mode transaction.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string"},
                    "manifest": {"type": "string"},
                    "target": {"type": "string"},
                    "restore_group": {"type": "string"},
                    "preserve_on_failure": {"type": "boolean"},
                    "drain_timeout": _bounded_integer_schema(1, 3600, 120),
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "human_approved": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 7200, 1800),
                },
                required=["action"],
            ),
            "handler": tool_serves_mode,
        },
        "serves_logs": {
            "description": "Read bounded docker logs for one manifest serve; follow mode is not allowed.",
            "inputSchema": _schema(
                {
                    "manifest": {"type": "string"},
                    "manifest_from_operator_home": {"type": "boolean"},
                    "names": {"type": "array", "items": {"type": "string"}},
                    "tail": _bounded_integer_schema(1, 5000, 200),
                    "max_output_bytes": _bounded_integer_schema(1024, 1048576, 65536),
                    "since": {"type": "string"},
                    "follow": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 600, 60),
                },
                required=["names"],
            ),
            "handler": tool_serves_logs,
        },
    },
)
