"""Explicit router MCP tool family."""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request

from ..arguments import (
    arg_bool as _arg_bool,
    bounded_int_arg as _bounded_int_arg,
    bounded_integer_schema as _bounded_integer_schema,
    probe_api_key_env as _probe_api_key_env,
    schema as _schema,
    str_arg as _str_arg,
)
from ..catalog import ToolFamily
from ..controller_client import (
    controller_auth_headers,
    http_error_details as _http_error_details,
    urlopen_no_proxy_no_redirect as _urlopen_no_proxy_no_redirect,
)
from ..errors import ToolError
from ..errors import ok as _ok
from ..runtime import (
    run_argv as _run_argv,
    run_argv_spooled as _run_argv_spooled,
)
from ..security import (
    redact_log_text as _redact_log_text,
    redact_secret as _redact_secret,
    safe_probe_url as _safe_probe_url,
)


def _router_manage_cli_argv(
    action: str,
    *,
    container: str = "",
    compose: str = "",
    service: str = "",
    env_file: str = "",
    dry_run: bool = False,
    no_verify: bool = False,
    recreate: bool = False,
    confirm: bool = False,
) -> list[str]:
    argv = [sys.executable, "-m", "anvil_serving.cli", "router", action]
    if container:
        argv += ["--container", container]
    if compose:
        argv += ["--compose", compose]
    if service:
        argv += ["--service", service]
    if env_file:
        argv += ["--env-file", env_file]
    if dry_run:
        argv.append("--dry-run")
    if no_verify:
        argv.append("--no-verify")
    if recreate:
        argv.append("--recreate")
    if confirm:
        argv.append("--confirm")
    return argv


def tool_router_status(args: dict) -> dict:
    from .... import router_manage

    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    return _ok(
        router_manage.status_summary(
            container,
            _open=lambda url, timeout: urllib.request.urlopen(
                _safe_probe_url(url),
                timeout=timeout,
            ),
        )
    )


def tool_router_fleet_status(args: dict) -> dict:
    """Probe the exact installed router config from the router runtime."""
    from .... import router_manage

    timeout = _bounded_int_arg(args, "timeout", 4, min_value=1, max_value=60)
    try:
        report = router_manage.installed_fleet_status(
            container=router_manage.DEFAULT_CONTAINER,
            timeout=float(timeout),
        )
    except ValueError as exc:
        raise ToolError("router_fleet_status_failed", str(exc))
    return _ok(report)


def tool_router_logs(args: dict) -> dict:
    from .... import router_manage

    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    follow = _arg_bool(args.get("follow"), False, name="follow")
    if follow:
        raise ToolError(
            "follow_not_allowed", "router_logs rejects unbounded follow mode; use a bounded tail"
        )
    tail = _bounded_int_arg(args, "tail", 200, min_value=1, max_value=5000)
    max_output_bytes = _bounded_int_arg(
        args, "max_output_bytes", 65536, min_value=1024, max_value=1048576
    )
    since = _str_arg(args, "since", "")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 60, min_value=1, max_value=600)
    argv = [
        sys.executable,
        "-m",
        "anvil_serving.cli",
        "router",
        "logs",
        "--container",
        container,
        "--tail",
        str(tail),
    ]
    if since:
        argv += ["--since", since]
    result = _run_argv_spooled(
        argv,
        timeout=timeout_seconds,
        max_output_bytes=max_output_bytes,
        redactor=_redact_log_text,
    )
    return _ok(
        {
            "bounded": True,
            "tail": tail,
            "since": since or None,
            "max_output_bytes": max_output_bytes,
            **result,
        }
    )


def tool_router_manage(args: dict) -> dict:
    from .... import router_manage

    action = _str_arg(args, "action", required=True)
    if action not in {"up", "down", "restart", "reload"}:
        raise ToolError(
            "bad_action", "action must be one of: up, down, restart, reload", {"action": action}
        )
    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    compose_arg = _str_arg(args, "compose", "")
    service = _str_arg(args, "service", router_manage.DEFAULT_SERVICE)
    env_file_arg = _str_arg(args, "env_file", "")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    no_verify = _arg_bool(args.get("no_verify"), False, name="no_verify")
    recreate = _arg_bool(args.get("recreate"), False, name="recreate")
    if recreate and action != "up":
        raise ToolError("bad_argument", "'recreate' is only valid with action='up'")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 300, min_value=1, max_value=7200)
    preview = dry_run or not confirm
    lifecycle = router_manage.lifecycle_plan(
        action,
        compose=compose_arg or None,
        service=service,
        env_file=env_file_arg or None,
        container=container,
        recreate=recreate,
    )
    argv = _router_manage_cli_argv(
        action,
        container=container if action in {"restart", "reload"} else "",
        compose=lifecycle["compose"] or "",
        service=lifecycle["service"] or "",
        env_file=lifecycle["env_file"] or "",
        dry_run=preview,
        no_verify=no_verify if action in {"restart", "reload"} else False,
        recreate=recreate,
        confirm=confirm,
    )
    target = {
        "action": action,
        "container": container,
        "compose": lifecycle["compose"],
        "compose_project": lifecycle["compose_project"],
        "service": lifecycle["service"],
        "env_file": lifecycle["env_file"],
        "recreate": recreate,
        "timeout_seconds": timeout_seconds,
        "no_verify": no_verify if action in {"restart", "reload"} else False,
    }
    if preview:
        return _ok(
            {
                "applied": False,
                "dry_run": True,
                "target": target,
                "command": argv,
                "lifecycle_command": lifecycle["command"],
            }
        )
    result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
    return _ok(
        {
            "applied": True,
            "dry_run": False,
            "target": target,
            "lifecycle_command": lifecycle["command"],
            **result,
        }
    )


def tool_router_transition(args: dict) -> dict:
    from .... import router_manage

    action = _str_arg(args, "action", required=True)
    tier_id = _str_arg(args, "tier", "")
    member_scope = {}
    if "member" in args:
        member = args["member"]
        if type(member) is not str or not member:
            raise ToolError("bad_argument", "member must be a nonempty replica member ID")
        member_scope["member_id"] = member
    router_url = _str_arg(args, "router_url", "")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout = args.get("timeout")
    if timeout is not None:
        timeout = _bounded_int_arg(args, "timeout", 60, min_value=1, max_value=3600)
    try:
        result = router_manage.transition_request(
            action,
            tier_id=tier_id or None,
            **member_scope,
            timeout=timeout,
            router_url=router_url or None,
            confirm=confirm,
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise ToolError("transition_failed", str(exc))
    return _ok(result)


_TIER_SETTING_LIMITS = {
    "max_concurrency": (1, 4096),
    "max_output_tokens": (1, 1048576),
}


def _toml_known_key_pattern(key: str) -> str:
    """Match bare or simply quoted spellings of a fixed, known TOML key."""
    escaped = re.escape(key)
    return r"(?:" + escaped + '|"' + escaped + '"|\'' + escaped + "')"


def _tier_candidate(raw: bytes, tier_id: str, values: dict) -> tuple[bytes, dict]:
    """Edit only two scalar fields in one canonical [[router.tiers]] block."""
    try:
        text = raw.decode("utf-8")
        parsed = tomllib.loads(text)
        tiers = parsed["router"]["tiers"]
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError):
        raise ToolError("bad_config", "router config is not canonical UTF-8 TOML") from None
    matches = [index for index, row in enumerate(tiers) if isinstance(row, dict) and row.get("id") == tier_id]
    if len(matches) != 1:
        raise ToolError("tier_not_found", "configuration requires one exact router tier")
    current = {key: tiers[matches[0]].get(key) for key in _TIER_SETTING_LIMITS}
    if not values:
        return raw, current
    if set(values) - set(_TIER_SETTING_LIMITS):
        raise ToolError("bad_argument", "unsupported router tier setting")
    for key, value in values.items():
        low, high = _TIER_SETTING_LIMITS[key]
        if type(value) is not int or value < low or value > high:
            raise ToolError("bad_argument", "%s is outside its supported range" % key)
    lines = text.splitlines(keepends=True)
    header = (r"^\s*\[\[\s*" + _toml_known_key_pattern("router") + r"\s*\.\s*" +
              _toml_known_key_pattern("tiers") + r"\s*\]\]\s*(?:#.*)?$")
    starts = [i for i, line in enumerate(lines) if re.match(header, line)]
    if len(starts) != len(tiers):
        raise ToolError("unsupported_config_layout", "router tier table layout is not safely editable")
    start = starts[matches[0]] + 1
    end = next((i for i in range(start, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
    for key, value in values.items():
        assignment = r"^(\s*" + _toml_known_key_pattern(key) + r"\s*=\s*)"
        positions = [i for i in range(start, end) if re.match(assignment, lines[i])]
        replacement = "%s = %d%s" % (key, value, "\r\n" if "\r\n" in text else "\n")
        if len(positions) > 1:
            raise ToolError("unsupported_config_layout", "router tier setting is duplicated")
        if positions:
            original = lines[positions[0]]
            prefix = re.match(assignment, original).group(1)
            suffix = re.search(r"[ \t]*(?:#[^\r\n]*)?(?:\r?\n)?$", original[len(prefix):]).group()
            lines[positions[0]] = prefix + str(value) + suffix
        else:
            lines.insert(end, replacement)
            end += 1
    candidate = "".join(lines).encode("utf-8")
    try:
        checked = tomllib.loads(candidate.decode("utf-8"))
    except (tomllib.TOMLDecodeError, KeyError, TypeError, IndexError):
        raise ToolError("bad_candidate", "typed router candidate did not validate") from None
    # Text resembling a table or key can occur inside a multiline value.
    # Require the entire parsed document to differ only in the intended fields.
    tiers[matches[0]].update(values)
    if checked != parsed:
        raise ToolError("bad_candidate", "typed router candidate changed outside its requested settings")
    configured = checked["router"]["tiers"][matches[0]]
    return candidate, {key: configured.get(key) for key in _TIER_SETTING_LIMITS}


def _tool_router_configuration(args: dict) -> dict:
    """Read/preview/apply two bounded tier settings with drift protection."""
    from .... import router_manage

    action = _str_arg(args, "action", required=True)
    if action not in {"status", "preview", "apply"}:
        raise ToolError("bad_action", "action must be one of: status, preview, apply")
    config = _str_arg(args, "config", required=True)
    tier = _str_arg(args, "tier", required=True)
    expected = _str_arg(args, "expected_baseline_sha256", "")
    values = args.get("values", {})
    if not isinstance(values, dict):
        raise ToolError("bad_argument", "values must be an object")
    source = os.path.abspath(os.path.expanduser(config))
    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    installed_config = _str_arg(args, "installed_config", router_manage.DEFAULT_INSTALLED_CONFIG)
    try:
        inspected = _run_argv(
            ["docker", "inspect", "--format", "{{json .Mounts}}", container],
            confirm=True,
            timeout=30,
        )
        mounts = json.loads(inspected.get("stdout", "[]"))
    except (ToolError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ToolError("mount_unavailable", "router configuration mount could not be verified") from exc
    bound = [row for row in mounts if isinstance(row, dict) and row.get("Destination") == installed_config]
    if (len(bound) != 1 or bound[0].get("Type") != "bind" or bound[0].get("RW") is not False or
            os.path.realpath(str(bound[0].get("Source", ""))) != os.path.realpath(source)):
        raise ToolError("mount_mismatch", "declared config is not the installed router bind source")
    try:
        with open(source, "rb") as handle:
            raw = handle.read(router_manage.MAX_ROUTER_CONFIG_BYTES + 1)
    except OSError as exc:
        raise ToolError("config_unavailable", "configured router source is unavailable") from exc
    if len(raw) > router_manage.MAX_ROUTER_CONFIG_BYTES:
        raise ToolError("config_too_large", "router config exceeds the 1 MiB limit")
    baseline = hashlib.sha256(raw).hexdigest()
    try:
        installed = router_manage.installed_fleet_status(
            container=container, installed_config=installed_config, timeout=4
        )
    except ValueError as exc:
        raise ToolError("installed_config_unavailable", str(exc)) from exc
    if installed.get("config_sha256") != baseline:
        raise ToolError("config_drift", "configured source does not match the installed router config")
    if expected and expected != baseline:
        raise ToolError("config_conflict", "installed router config changed after preview")
    candidate, configured = _tier_candidate(raw, tier, values)
    candidate_digest = hashlib.sha256(candidate).hexdigest()
    if action in {"status", "preview"}:
        return _ok({"applied": False, "dry_run": True, "tier": tier,
                    "configured": configured, "baseline_sha256": baseline,
                    "candidate_sha256": candidate_digest})
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    human = _arg_bool(args.get("human_approved"), False, name="human_approved")
    if dry_run or not confirm or not human:
        raise ToolError("human_approval_required", "configuration apply requires the confirmed human gate")
    topology = _str_arg(args, "topology", "")
    overlay = _str_arg(args, "topology_overlay", "")
    router_url = _str_arg(args, "router_url", "")
    compose = _str_arg(args, "compose", required=True)
    service = _str_arg(args, "service", required=True)
    env_file = _str_arg(args, "env_file", required=True)
    drain_timeout = _bounded_int_arg(args, "drain_timeout", 120, min_value=1, max_value=3600)
    original_mode = os.stat(source).st_mode
    initial_status = router_manage.transition_request("status", router_url=router_url or None)
    original_admissions = {row["tier_id"]: row["state"] for row in initial_status.get("tiers", [])
                           if isinstance(row, dict) and isinstance(row.get("tier_id"), str)
                           and row.get("state") in {"admitting", "quiesced"}}
    if not original_admissions:
        raise ToolError("admission_unavailable", "current router admission state could not be captured")

    def replace_source(content: bytes) -> None:
        directory = os.path.dirname(source)
        fd, candidate_path = tempfile.mkstemp(prefix=".anvil-router-", suffix=".toml", dir=directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(candidate_path, original_mode)
            os.replace(candidate_path, source)
        finally:
            if os.path.exists(candidate_path):
                os.unlink(candidate_path)

    changed = False
    with tempfile.NamedTemporaryFile(prefix="anvil-router-candidate-", suffix=".toml") as handle:
        handle.write(candidate)
        handle.flush()

        def install_bound(_snapshot):
            nonlocal changed
            try:
                with open(source, "rb") as current_handle:
                    current = current_handle.read(router_manage.MAX_ROUTER_CONFIG_BYTES + 1)
                current_installed = router_manage.installed_fleet_status(
                    container=container, installed_config=installed_config, timeout=4
                )
            except (OSError, ValueError):
                return 1
            if (hashlib.sha256(current).hexdigest() != baseline or
                    current_installed.get("config_sha256") != baseline):
                return 1
            replace_source(candidate)
            changed = True
            return router_manage.cmd_up(
                compose, service, env_file=env_file, recreate=True, container=container
            )

        try:
            result = router_manage.install_config(
                handle.name, topology_path=topology or None,
                topology_overlay_path=overlay or None, router_url=router_url or None,
                drain_timeout=drain_timeout, confirm=True, dry_run=False,
                _install=install_bound,
            )
            observed = router_manage.installed_fleet_status(
                container=container, installed_config=installed_config, timeout=4
            )
            if observed.get("config_sha256") != candidate_digest:
                raise ValueError("recreated router did not load the candidate digest")
            # A recreated router may start with admitting defaults. Preserve
            # intentional maintenance even when installation itself succeeds.
            for tier_id, admission in original_admissions.items():
                if admission == "quiesced":
                    router_manage.transition_request(
                        "quiesce", tier_id=tier_id, router_url=router_url or None,
                        confirm=True, dry_run=False,
                    )
            final_admission = router_manage.transition_request("status", router_url=router_url or None)
            final_states = {row.get("tier_id"): row.get("state")
                            for row in final_admission.get("tiers", []) if isinstance(row, dict)}
            if any(final_states.get(tier_id) != admission for tier_id, admission in original_admissions.items()):
                raise ValueError("installed router did not preserve prior admission intent")
            result["tier_status"] = final_admission.get("tiers", [])
        except (OSError, ValueError) as exc:
            recovery = "failed"
            try:
                if changed:
                    replace_source(raw)
                    if router_manage.cmd_up(
                        compose,
                        service,
                        env_file=env_file,
                        recreate=True,
                        container=container,
                    ) != 0:
                        raise ValueError("previous router could not be recreated")
                    restored = router_manage.installed_fleet_status(
                        container=container,
                        installed_config=installed_config,
                        timeout=4,
                    )
                    if restored.get("config_sha256") != baseline:
                        raise ValueError("previous installed configuration could not be verified")
                # install_config may already have quiesced tiers before its
                # installer rejects a stale baseline. Its best-effort
                # compensation deliberately swallows readmission failures, so
                # verify and restore the captured intent even when this owner
                # never replaced the bind source.
                for tier_id, admission in original_admissions.items():
                    router_manage.transition_request(
                        "readmit" if admission == "admitting" else "quiesce",
                        tier_id=tier_id,
                        router_url=router_url or None,
                        confirm=True,
                        dry_run=False,
                    )
                final = router_manage.transition_request(
                    "status", router_url=router_url or None
                )
                states = {
                    row.get("tier_id"): row.get("state")
                    for row in final.get("tiers", [])
                    if isinstance(row, dict)
                }
                if any(
                    states.get(tier_id) != admission
                    for tier_id, admission in original_admissions.items()
                ):
                    raise ValueError("previous admissions could not be verified")
                recovery = "restored"
            except (OSError, ValueError):
                recovery = "failed"
            raise ToolError("configuration_apply_failed", "router configuration apply failed",
                            {"recovery": recovery}) from exc
    return _ok({"tier": tier, "configured": configured,
                "baseline_sha256": baseline, "candidate_sha256": candidate_digest, **result})


def tool_router_configuration(args: dict) -> dict:
    """Serialize live config changes with every serve/profile/mode transaction."""
    if args.get("action") != "apply":
        return _tool_router_configuration(args)
    from .... import serves as serves_mod

    with serves_mod._switch_role_lock("promotion"):
        return _tool_router_configuration(args)


def _decision_records_from_path(path: str, *, max_input_bytes: int) -> list[dict]:
    if not os.path.isfile(path):
        raise ToolError(
            "decision_log_not_found", "decision summary source not found", {"path": path}
        )
    if os.path.getsize(path) > max_input_bytes:
        raise ToolError(
            "decision_log_too_large",
            "decision summary source exceeds max_input_bytes",
            {"path": path, "max_input_bytes": max_input_bytes},
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        records = []
        for lineno, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise ToolError(
                    "bad_decision_log",
                    "bad JSONL decision record",
                    {"path": path, "line": lineno, "error": str(exc)},
                )
            if isinstance(item, dict):
                records.append(item)
        return records
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        if isinstance(parsed.get("records"), list):
            return [item for item in parsed["records"] if isinstance(item, dict)]
        return [parsed]
    raise ToolError(
        "bad_decision_log",
        "decision summary source must be JSON array, JSONL, or object with records[]",
    )


def _decisions_url(base_url: str, limit: int) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/decisions"):
        url = base
    elif base.endswith("/v1"):
        url = base + "/decisions"
    else:
        url = base + "/v1/decisions"
    return url + "?" + urllib.parse.urlencode({"limit": str(limit)})


def tool_decision_summary(args: dict) -> dict:
    from ....router.decision_log import summarize_decisions

    limit = _bounded_int_arg(args, "limit", 20, min_value=1, max_value=500)
    max_input_bytes = _bounded_int_arg(
        args, "max_input_bytes", 1048576, min_value=1024, max_value=10485760
    )
    timeout = _bounded_int_arg(args, "timeout_seconds", 5, min_value=1, max_value=60)
    base_url = _str_arg(args, "base_url", "http://127.0.0.1:8000/v1")
    api_key_env = _probe_api_key_env(args)
    path = _str_arg(args, "path", "")
    records_arg = args.get("records", [])
    if records_arg is None:
        records_arg = []
    if not isinstance(records_arg, list) or not all(isinstance(item, dict) for item in records_arg):
        raise ToolError("bad_argument", "'records' must be an array of objects")
    records = list(records_arg)
    source = "inline"
    if path:
        records = _decision_records_from_path(path, max_input_bytes=max_input_bytes)
        source = "path"
    if not path and not records:
        base_url = _safe_probe_url(base_url)
        token = ""
        headers = {"Accept": "application/json"}
        if api_key_env:
            token = os.environ.get(api_key_env)
            if token:
                headers.update(controller_auth_headers(token))
        req = urllib.request.Request(_decisions_url(base_url, limit), headers=headers, method="GET")
        try:
            with _urlopen_no_proxy_no_redirect(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw or "{}")
        except urllib.error.HTTPError as exc:
            details, _ = _http_error_details(exc, token)
            raise ToolError(
                "decision_summary_http_error",
                "decision summary returned HTTP %s" % exc.code,
                details,
            )
        except Exception as exc:
            raise ToolError(
                "decision_summary_failed", _redact_secret(str(exc), token), {"base_url": base_url}
            )
        if not isinstance(parsed, dict):
            raise ToolError(
                "bad_decision_summary", "decision summary response must be a JSON object"
            )
        parsed = _redact_secret(parsed, token)
        parsed["source"] = "router"
        parsed["base_url"] = base_url
        return _ok(parsed)
    summary = summarize_decisions(records, limit=limit)
    summary["source"] = source
    summary["path"] = path or None
    return _ok(summary)


FAMILY = ToolFamily(
    name="router",
    tools={
        "router_status": {
            "description": "Inspect the deployed anvil router container and loopback health.",
            "inputSchema": _schema({"container": {"type": "string"}}),
            "handler": tool_router_status,
        },
        "router_fleet_status": {
            "description": "Probe the installed router configuration from the live router runtime without returning private endpoint identities.",
            "inputSchema": _schema(
                {
                    "timeout": _bounded_integer_schema(1, 60, 4),
                }
            ),
            "handler": tool_router_fleet_status,
        },
        "router_logs": {
            "description": "Read bounded, redacted docker logs for the deployed router; follow mode is not allowed.",
            "inputSchema": _schema(
                {
                    "container": {"type": "string"},
                    "tail": _bounded_integer_schema(1, 5000, 200),
                    "max_output_bytes": _bounded_integer_schema(1024, 1048576, 65536),
                    "since": {"type": "string"},
                    "follow": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 600, 60),
                }
            ),
            "handler": tool_router_logs,
        },
        "router_manage": {
            "description": "Preview or run guarded deployed-router lifecycle actions: up, down, restart, or reload.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string"},
                    "container": {"type": "string"},
                    "compose": {"type": "string"},
                    "service": {"type": "string"},
                    "env_file": {"type": "string"},
                    "recreate": {"type": "boolean"},
                    "no_verify": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 7200, 300),
                },
                required=["action"],
            ),
            "handler": tool_router_manage,
        },
        "router_transition": {
            "description": "Inspect, quiesce, drain, or safely readmit a router tier or declared member through the authenticated router boundary.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string"},
                    "tier": {"type": "string"},
                    "member": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$"},
                    "router_url": {"type": "string"},
                    "timeout": _bounded_integer_schema(1, 3600, 60),
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                },
                required=["action"],
            ),
            "handler": tool_router_transition,
        },
        "router_configuration": {
            "description": "Read, preview, or transactionally apply bounded router tier settings.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string", "enum": ["status", "preview", "apply"]},
                    "config": {"type": "string"}, "tier": {"type": "string"},
                    "values": {"type": "object", "additionalProperties": False,
                               "properties": {
                                   "max_concurrency": _bounded_integer_schema(1, 4096, 1),
                                   "max_output_tokens": _bounded_integer_schema(1, 1048576, 1),
                               }},
                    "expected_baseline_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                    "topology": {"type": "string"}, "topology_overlay": {"type": "string"},
                    "router_url": {"type": "string"},
                    "container": {"type": "string"}, "installed_config": {"type": "string"},
                    "compose": {"type": "string"}, "service": {"type": "string"},
                    "env_file": {"type": "string"},
                    "drain_timeout": _bounded_integer_schema(1, 3600, 120),
                    "dry_run": {"type": "boolean"}, "confirm": {"type": "boolean"},
                    "human_approved": {"type": "boolean"},
                }, required=["action", "config", "tier"],
            ),
            "handler": tool_router_configuration,
        },
        "decision_summary": {
            "description": "Summarize recent router decisions without prompts or secrets; defaults to GET /v1/decisions.",
            "inputSchema": _schema(
                {
                    "base_url": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "records": {"type": "array", "items": {"type": "object"}},
                    "path": {"type": "string"},
                    "limit": _bounded_integer_schema(1, 500, 20),
                    "max_input_bytes": _bounded_integer_schema(1024, 10485760, 1048576),
                    "timeout_seconds": _bounded_integer_schema(1, 60, 5),
                }
            ),
            "handler": tool_decision_summary,
        },
    },
)
