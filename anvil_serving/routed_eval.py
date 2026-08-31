"""Fail-closed routed acceptance against real local agent harnesses."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.parse
import urllib.request
import uuid

from .benchmarking.artifacts import atomic_write_json
from .guard import confirmation_authorized


SCHEMA = "routed-client-eval/v2"
MAX_CLIENT_OUTPUT_BYTES = 1024 * 1024
_CLIENTS = frozenset({"openclaw", "hermes"})
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _resolve_client_executable(name: str) -> str:
    """Resolve a managed client without depending on an interactive shell PATH."""
    resolved = shutil.which(name)
    if resolved:
        return resolved
    home = Path.home()
    candidates = (
        home / ".local" / "bin" / name,
        home / ".local" / "share" / "pnpm" / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
    )
    if os.name == "nt" and os.environ.get("APPDATA"):
        candidates += (Path(os.environ["APPDATA"]) / "npm" / f"{name}.cmd",)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return name


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an absolute http:// or https:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain credentials, a query, or a fragment")
    if parsed.path.rstrip("/") != "/v1":
        raise ValueError("base_url must end in exactly /v1")
    if (parsed.hostname or "").lower().rstrip(".") == "localhost":
        raise ValueError("base_url must use 127.0.0.1 instead of localhost")
    return value.rstrip("/")


def _validate_env_name(value: str) -> str:
    if not _ENV_RE.fullmatch(value):
        raise ValueError("api_key_env must be an environment-variable name")
    return value


def _normalize_alias(value: str) -> str:
    alias = value.strip().lower()
    if not alias or any(character.isspace() for character in alias):
        raise ValueError("model must be one non-empty router alias")
    return alias


def _normalize_clients(value: str | Sequence[str]) -> tuple[str, ...]:
    raw = value.split(",") if isinstance(value, str) else value
    clients = tuple(dict.fromkeys(str(item).strip().lower() for item in raw if str(item).strip()))
    unknown = sorted(set(clients) - _CLIENTS)
    if not clients:
        raise ValueError("clients must select openclaw, hermes, or both")
    if unknown:
        raise ValueError("unknown routed-eval clients: " + ", ".join(unknown))
    return clients


def _safe_json_bytes(response: Any) -> Mapping[str, Any]:
    raw = response.read(MAX_CLIENT_OUTPUT_BYTES + 1)
    if len(raw) > MAX_CLIENT_OUTPUT_BYTES:
        raise ValueError("router metadata response exceeded the evidence bound")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("router metadata response was not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError("router metadata response must be a JSON object")
    return value


def _router_get(
    base_url: str,
    path: str,
    *,
    token: str,
    timeout_seconds: float,
    opener: Callable[..., Any],
) -> Mapping[str, Any]:
    request = urllib.request.Request(
        base_url + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        method="GET",
    )
    try:
        response = opener(request, timeout=timeout_seconds)
        if hasattr(response, "__enter__"):
            with response as opened:
                return _safe_json_bytes(opened)
        return _safe_json_bytes(response)
    except Exception as exc:
        raise ValueError(f"router metadata request failed for {path!r}: {exc}") from exc


def _route_item(payload: Mapping[str, Any], alias: str, label: str) -> Mapping[str, Any]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError(f"router {label} response has no data list")
    matches = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        aliases = item.get("aliases")
        normalized = {
            str(candidate).strip().lower()
            for candidate in aliases
        } if isinstance(aliases, list) else set()
        if alias in normalized:
            matches.append(item)
    if len(matches) != 1:
        raise ValueError(
            f"router {label} returned {len(matches)} entries for alias {alias!r}; expected one"
        )
    return matches[0]


def _discovery_item(payload: Mapping[str, Any], alias: str) -> Mapping[str, Any]:
    data = payload.get("data")
    if payload.get("object") != "list" or not isinstance(data, list):
        raise ValueError("router model discovery response has no OpenAI-compatible data list")
    matches = [
        item for item in data
        if isinstance(item, Mapping)
        and isinstance(item.get("id"), str)
        and item["id"].strip().lower() == alias
    ]
    if len(matches) != 1:
        raise ValueError(
            f"router model discovery returned {len(matches)} entries for alias {alias!r}; "
            "expected one"
        )
    return matches[0]


def inspect_route(
    *,
    base_url: str,
    alias: str,
    token: str,
    expected_served_model: str,
    expected_config_fingerprint: str | None,
    expected_router_config_sha256: str | None,
    min_context_tokens: int,
    timeout_seconds: float,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Inspect one router alias and return independently checkable gate evidence."""
    status = _router_get(
        base_url, "/router/status", token=token,
        timeout_seconds=timeout_seconds, opener=opener,
    )
    discovery = _router_get(
        base_url, "/models", token=token,
        timeout_seconds=timeout_seconds, opener=opener,
    )
    encoded = urllib.parse.quote(alias, safe="")
    fingerprints = _router_get(
        base_url, f"/models/fingerprints?model={encoded}", token=token,
        timeout_seconds=timeout_seconds, opener=opener,
    )
    capacities = _router_get(
        base_url, f"/models/capacity?model={encoded}", token=token,
        timeout_seconds=timeout_seconds, opener=opener,
    )
    fingerprint = _route_item(fingerprints, alias, "fingerprints")
    capacity = _route_item(capacities, alias, "capacity")
    discovered = _discovery_item(discovery, alias)
    advertised = {
        str(candidate).strip().lower()
        for candidate in status.get("model_aliases", [])
    } if isinstance(status.get("model_aliases"), list) else set()
    readiness = fingerprint.get("readiness")
    served_identity = fingerprint.get("served_identity")
    fingerprint_value = fingerprint.get("fingerprint")
    capacity_value = capacity.get("capacity")
    observed_model = (
        served_identity.get("observed") if isinstance(served_identity, Mapping) else None
    )
    observed_fingerprint = (
        fingerprint_value.get("config_fingerprint")
        if isinstance(fingerprint_value, Mapping) else None
    )
    context_limit = (
        capacity_value.get("context_limit_tokens")
        if isinstance(capacity_value, Mapping) else None
    )
    router_config_sha256 = status.get("config_sha256")
    discovered_context = discovered.get("context_window")
    checks = {
        "alias_advertised": alias in advertised,
        "readiness_ready": (
            isinstance(readiness, Mapping) and readiness.get("state") == "ready"
        ),
        "served_model_exact": observed_model == expected_served_model,
        "config_fingerprint_exact": (
            True
            if expected_config_fingerprint is None
            else observed_fingerprint == expected_config_fingerprint
        ),
        "router_config_sha256_valid": (
            isinstance(router_config_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", router_config_sha256) is not None
        ),
        "router_config_sha256_exact": (
            True
            if expected_router_config_sha256 is None
            else router_config_sha256 == expected_router_config_sha256
        ),
        "context_minimum": (
            isinstance(context_limit, int)
            and not isinstance(context_limit, bool)
            and context_limit >= min_context_tokens
        ),
        "discovery_context_exact": discovered_context == context_limit,
        "capacity_loaded": capacity.get("loaded") is True,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "package_version": status.get("package_version"),
            "config_sha256": router_config_sha256,
            "tier_id": fingerprint.get("id"),
            "served_model": observed_model,
            "config_fingerprint": observed_fingerprint,
            "readiness": dict(readiness) if isinstance(readiness, Mapping) else readiness,
            "context_limit_tokens": context_limit,
            "discovery_context_window": discovered_context,
            "configured_max_concurrency": (
                capacity_value.get("configured_max_concurrency")
                if isinstance(capacity_value, Mapping) else None
            ),
            "engine": capacity.get("engine"),
        },
    }


def _bounded_process_text(value: str | bytes | None) -> tuple[str, bool]:
    raw = value if isinstance(value, bytes) else (value or "").encode("utf-8", "replace")
    truncated = len(raw) > MAX_CLIENT_OUTPUT_BYTES
    return raw[:MAX_CLIENT_OUTPUT_BYTES].decode("utf-8", "replace"), truncated


def _default_runner(argv: Sequence[str], timeout_seconds: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, timeout=timeout_seconds, check=False,
    )


def _run_client(
    argv: Sequence[str], *, timeout_seconds: float,
    runner: Callable[[Sequence[str], float], Any],
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = runner(tuple(argv), timeout_seconds)
    except FileNotFoundError as exc:
        return {
            "returncode": None, "stdout": "", "stderr": str(exc), "timed_out": False,
            "launch_error": "executable_not_found", "output_truncated": False,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _bounded_process_text(exc.stdout)
        stderr, stderr_truncated = _bounded_process_text(exc.stderr)
        return {
            "returncode": None, "stdout": stdout, "stderr": stderr, "timed_out": True,
            "launch_error": None, "output_truncated": stdout_truncated or stderr_truncated,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
    stdout, stdout_truncated = _bounded_process_text(getattr(completed, "stdout", ""))
    stderr, stderr_truncated = _bounded_process_text(getattr(completed, "stderr", ""))
    return {
        "returncode": int(getattr(completed, "returncode", 1)),
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": False,
        "launch_error": None,
        "output_truncated": stdout_truncated or stderr_truncated,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }


def _client_failure(
    *, checks: Mapping[str, bool], parse_error: str | None, process: Mapping[str, Any],
) -> str | None:
    """Return a bounded failure summary without retaining untrusted client output."""
    if all(checks.values()):
        return None
    if process["timed_out"]:
        return "client process timed out"
    if process.get("launch_error") == "executable_not_found":
        return "client executable was not found on PATH or in standard install locations"
    if process["returncode"] != 0:
        return f"client process exited with status {process['returncode']}"
    if process["output_truncated"]:
        return "client output exceeded the evidence bound"
    if parse_error:
        return parse_error
    return "client identity or response validation failed"


def evaluate_openclaw(
    *, alias: str, provider: str, marker: str, probe_path: str, run_id: str,
    expected_context_tokens: int,
    timeout_seconds: float, runner: Callable[[Sequence[str], float], Any] = _default_runner,
    executable: str = "openclaw",
) -> dict[str, Any]:
    model = f"{provider}/{alias}"
    prompt = (
        f"Use the exec tool exactly once to read the UTF-8 file at {json.dumps(probe_path)}. "
        "Then reply with exactly the file contents and nothing else."
    )
    argv = (
        executable, "agent", "--agent", "main", "--session-key",
        f"agent:main:anvil-routed-eval-{run_id}", "--model", model,
        "--thinking", "off", "--message", prompt,
        "--timeout", str(int(timeout_seconds)), "--json",
    )
    process = _run_client(argv, timeout_seconds=timeout_seconds, runner=runner)
    parsed: Mapping[str, Any] = {}
    parse_error = None
    try:
        value = json.loads(process["stdout"])
        if isinstance(value, Mapping):
            parsed = value
        else:
            parse_error = "OpenClaw output was not a JSON object"
    except json.JSONDecodeError as exc:
        parse_error = f"OpenClaw output was not valid JSON: {exc}"
    result = parsed.get("result") if isinstance(parsed.get("result"), Mapping) else {}
    payloads = result.get("payloads") if isinstance(result, Mapping) else None
    texts = [
        item.get("text") for item in payloads
        if isinstance(item, Mapping) and isinstance(item.get("text"), str)
    ] if isinstance(payloads, list) else []
    meta = result.get("meta") if isinstance(result, Mapping) else {}
    meta = meta if isinstance(meta, Mapping) else {}
    agent_meta = meta.get("agentMeta") if isinstance(meta.get("agentMeta"), Mapping) else {}
    trace = meta.get("executionTrace") if isinstance(meta.get("executionTrace"), Mapping) else {}
    checks = {
        "process_succeeded": process["returncode"] == 0 and not process["timed_out"],
        "output_bounded": not process["output_truncated"],
        "json_valid": parse_error is None,
        "status_ok": parsed.get("status") == "ok",
        "marker_exact": texts == [marker],
        "provider_exact": agent_meta.get("provider") == provider,
        "model_exact": agent_meta.get("model") == alias,
        "winner_provider_exact": trace.get("winnerProvider") == provider,
        "winner_model_exact": trace.get("winnerModel") == alias,
        "fallback_forbidden": trace.get("fallbackUsed") is False,
        "context_exact": agent_meta.get("contextTokens") == expected_context_tokens,
    }
    return {
        "client": "openclaw", "passed": all(checks.values()), "checks": checks,
        "command": {
            "provider": provider, "model": model, "thinking": "off",
            "tool_probe": "exec/read-temporary-nonce",
        },
        "observed": {
            "provider": agent_meta.get("provider"), "model": agent_meta.get("model"),
            "context_tokens": agent_meta.get("contextTokens"),
            "winner_provider": trace.get("winnerProvider"),
            "winner_model": trace.get("winnerModel"),
            "fallback_used": trace.get("fallbackUsed"),
            "stop_reason": meta.get("stopReason"),
            "payload_count": len(texts),
            "response_chars": sum(len(text) for text in texts),
            "returncode": process["returncode"], "latency_ms": process["latency_ms"],
        },
        "failure": _client_failure(
            checks=checks, parse_error=parse_error, process=process,
        ),
    }


def evaluate_hermes(
    *, alias: str, provider: str, expected_observed_provider: str, marker: str,
    probe_path: str,
    timeout_seconds: float, runner: Callable[[Sequence[str], float], Any] = _default_runner,
    executable: str = "hermes",
) -> dict[str, Any]:
    usage_error = None
    usage: Mapping[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="anvil-routed-eval-") as temporary:
        usage_path = str(Path(temporary) / "hermes-usage.json")
        prompt = (
            f"Use the terminal tool exactly once to read the UTF-8 file at "
            f"{json.dumps(probe_path)}. Then reply with exactly the file contents "
            "and nothing else."
        )
        argv = (
            executable, "--provider", provider, "--model", alias,
            "--reasoning", "none", "--usage-file", usage_path,
            "--toolsets", "terminal", "-z", prompt,
        )
        process = _run_client(argv, timeout_seconds=timeout_seconds, runner=runner)
        try:
            value = json.loads(Path(usage_path).read_text(encoding="utf-8"))
            if isinstance(value, Mapping):
                usage = value
            else:
                usage_error = "Hermes usage evidence was not a JSON object"
        except FileNotFoundError:
            usage_error = "Hermes usage evidence was not created"
        except OSError:
            usage_error = "Hermes usage evidence could not be read"
        except json.JSONDecodeError:
            usage_error = "Hermes usage evidence was not valid JSON"
    visible = process["stdout"].strip()
    checks = {
        "process_succeeded": process["returncode"] == 0 and not process["timed_out"],
        "output_bounded": not process["output_truncated"],
        "marker_exact": visible == marker,
        "usage_valid": usage_error is None,
        "provider_exact": usage.get("provider") == expected_observed_provider,
        "model_exact": usage.get("model") == alias,
        "completed": usage.get("completed") is True,
        "not_failed": usage.get("failed") is False,
        "api_call_recorded": isinstance(usage.get("api_calls"), int) and usage["api_calls"] >= 1,
    }
    return {
        "client": "hermes", "passed": all(checks.values()), "checks": checks,
        "command": {
            "provider_selector": provider, "expected_observed_provider": expected_observed_provider,
            "model": alias, "reasoning": "none",
            "tool_probe": "terminal/read-temporary-nonce",
        },
        "observed": {
            "provider": usage.get("provider"), "model": usage.get("model"),
            "api_calls": usage.get("api_calls"), "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "response_chars": len(visible),
            "returncode": process["returncode"], "latency_ms": process["latency_ms"],
        },
        "failure": _client_failure(
            checks=checks, parse_error=usage_error, process=process,
        ),
    }


def run_routed_eval(
    *, base_url: str, alias: str, api_key_env: str, expected_served_model: str,
    expected_config_fingerprint: str | None,
    expected_router_config_sha256: str | None = None,
    min_context_tokens: int,
    clients: str | Sequence[str], openclaw_provider: str, hermes_provider: str,
    hermes_expected_provider: str, timeout_seconds: float, output: str | None,
    run_id: str | None = None, dry_run: bool = False,
    sync_harnesses: bool = True,
    openclaw_config: str = "~/.openclaw/openclaw.json",
    pi_models: str = "~/.pi/agent/models.json",
    pi_settings: str = "~/.pi/agent/settings.json",
    client_state_path: str = "~/.anvil-serving/state/client-catalog.json",
    client_backup_root: str = "~/.anvil-serving/backups/client-catalog",
    environment: Mapping[str, str] | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    catalog_opener: Any = None,
    runner: Callable[[Sequence[str], float], Any] = _default_runner,
    syncer: Callable[..., dict[str, Any]] | None = None,
    restart_openclaw: Callable[[], int] | None = None,
    refresh_openclaw_service: Callable[[], int] | None = None,
) -> dict[str, Any]:
    base_url = _validate_base_url(base_url)
    alias = _normalize_alias(alias)
    api_key_env = _validate_env_name(api_key_env)
    selected_clients = _normalize_clients(clients)
    if not expected_served_model.strip():
        raise ValueError("expected_served_model is required")
    if expected_router_config_sha256 is not None and not re.fullmatch(
        r"[0-9a-f]{64}", expected_router_config_sha256
    ):
        raise ValueError("expected_router_config_sha256 must be 64 lowercase hex characters")
    if not isinstance(min_context_tokens, int) or min_context_tokens < 1:
        raise ValueError("min_context_tokens must be a positive integer")
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError("timeout_seconds must be between 1 and 3600")
    run_id = run_id or datetime.now(timezone.utc).strftime("routed-%Y%m%dT%H%M%SZ")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id must be portable and at most 128 characters")
    env = os.environ if environment is None else environment
    token = env.get(api_key_env, "")
    plan = {
        "schema": SCHEMA,
        "run_id": run_id,
        "dry_run": dry_run,
        "target": {
            "base_url": base_url, "alias": alias,
            "expected_served_model": expected_served_model,
            "expected_config_fingerprint": expected_config_fingerprint,
            "expected_router_config_sha256": expected_router_config_sha256,
            "min_context_tokens": min_context_tokens,
        },
        "clients": list(selected_clients),
        "credentials": {"api_key_env": api_key_env, "available": bool(token)},
        "client_overrides": {
            "openclaw": {"provider": openclaw_provider, "model": f"{openclaw_provider}/{alias}"},
            "hermes": {
                "provider_selector": hermes_provider,
                "expected_observed_provider": hermes_expected_provider,
                "model": alias,
            },
        },
        "output": output,
        "harness_reconciliation": {
            "enabled": sync_harnesses,
            "openclaw_config": openclaw_config,
            "pi_models": pi_models,
            "pi_settings": pi_settings,
            "state_path": client_state_path,
            "backup_root": client_backup_root,
            "restart_openclaw_on_router_change": sync_harnesses,
        },
        "persistent_changes": sync_harnesses,
    }
    if dry_run:
        return {
            **plan, "passed": None, "router": None, "router_before_sync": None,
            "harness_sync": None, "results": [],
        }
    if not output:
        raise ValueError("output is required for a live routed evaluation")
    if not token:
        raise ValueError(f"router credential environment variable {api_key_env!r} is unset")
    started_at = _utc_now()
    try:
        router_before = inspect_route(
            base_url=base_url, alias=alias, token=token,
            expected_served_model=expected_served_model,
            expected_config_fingerprint=expected_config_fingerprint,
            expected_router_config_sha256=expected_router_config_sha256,
            min_context_tokens=min_context_tokens, timeout_seconds=timeout_seconds,
            opener=opener,
        )
    except ValueError as exc:
        router_before = {
            "passed": False,
            "checks": {},
            "observed": {},
            "failure": str(exc),
        }
    harness_sync: dict[str, Any] = {
        "enabled": sync_harnesses,
        "passed": not sync_harnesses,
        "checks": {},
        "receipt": None,
        "failure": None,
    }
    router = router_before
    if router_before["passed"] and sync_harnesses:
        if syncer is None:
            from .client_catalog_sync import sync_clients

            syncer = sync_clients
        if restart_openclaw is None:
            from . import harness

            def restart_openclaw() -> int:
                return harness.cmd_restart_openclaw(
                    timeout_seconds=harness.DEFAULT_TRANSPORT_TIMEOUT_SECONDS
                )
        if refresh_openclaw_service is None:
            from . import harness

            def refresh_openclaw_service() -> int:
                return harness.cmd_refresh_openclaw_service_environment(
                    timeout_seconds=harness.DEFAULT_TRANSPORT_TIMEOUT_SECONDS
                )
        try:
            receipt = syncer(
                base_url=base_url,
                api_key_env=api_key_env,
                openclaw_config=openclaw_config,
                pi_models=pi_models,
                pi_settings=pi_settings,
                state_path=client_state_path,
                backup_root=client_backup_root,
                restart_openclaw_on_change=True,
                dry_run=False,
                confirm=True,
                timeout_seconds=max(1, min(300, int(timeout_seconds))),
                environ=env,
                opener=catalog_opener,
                restart=restart_openclaw,
                refresh_openclaw_service=refresh_openclaw_service,
            )
            rows = {
                row.get("id"): row
                for row in receipt.get("models", [])
                if isinstance(row, Mapping) and isinstance(row.get("id"), str)
            }
            alias_row = rows.get(alias, {})
            sync_checks = {
                "applied": receipt.get("dry_run") is False,
                "router_config_sha256_exact": (
                    receipt.get("config_sha256")
                    == router_before["observed"].get("config_sha256")
                ),
                "alias_present": alias in rows,
                "context_exact": (
                    alias_row.get("context_window")
                    == router_before["observed"].get("context_limit_tokens")
                ),
            }
            harness_sync = {
                "enabled": True,
                "passed": all(sync_checks.values()),
                "checks": sync_checks,
                "receipt": receipt,
                "failure": None if all(sync_checks.values()) else (
                    "client catalog receipt did not match the inspected router snapshot"
                ),
            }
        except (OSError, ValueError) as exc:
            harness_sync = {
                "enabled": True,
                "passed": False,
                "checks": {},
                "receipt": None,
                "failure": str(exc),
            }
        if harness_sync["passed"]:
            try:
                router = inspect_route(
                    base_url=base_url, alias=alias, token=token,
                    expected_served_model=expected_served_model,
                    expected_config_fingerprint=expected_config_fingerprint,
                    expected_router_config_sha256=(
                        router_before["observed"].get("config_sha256")
                    ),
                    min_context_tokens=min_context_tokens,
                    timeout_seconds=timeout_seconds, opener=opener,
                )
            except ValueError as exc:
                router = {
                    "passed": False, "checks": {}, "observed": {},
                    "failure": str(exc),
                }
    results = []
    can_run_clients = router["passed"] and harness_sync["passed"]
    if can_run_clients:
        expected_context_tokens = router["observed"]["context_limit_tokens"]
        client_executables = {
            client: (
                _resolve_client_executable(client)
                if runner is _default_runner
                else client
            )
            for client in selected_clients
        }
        for client in selected_clients:
            marker = "ANVIL_" + client.upper() + "_ROUTED_EVAL_" + uuid.uuid4().hex.upper()
            with tempfile.TemporaryDirectory(prefix="anvil-routed-tool-probe-") as temporary:
                probe_path = Path(temporary) / "nonce.txt"
                probe_path.write_text(marker, encoding="utf-8")
                os.chmod(temporary, 0o755)
                os.chmod(probe_path, 0o644)
                if client == "openclaw":
                    results.append(evaluate_openclaw(
                        alias=alias, provider=openclaw_provider, marker=marker,
                        probe_path=str(probe_path), run_id=run_id,
                        expected_context_tokens=expected_context_tokens,
                        timeout_seconds=timeout_seconds, runner=runner,
                        executable=client_executables[client],
                    ))
                else:
                    results.append(evaluate_hermes(
                        alias=alias, provider=hermes_provider,
                        expected_observed_provider=hermes_expected_provider,
                        marker=marker, probe_path=str(probe_path),
                        timeout_seconds=timeout_seconds, runner=runner,
                        executable=client_executables[client],
                    ))
    artifact = {
        **plan, "dry_run": False, "started_at": started_at, "finished_at": _utc_now(),
        "router_before_sync": router_before, "harness_sync": harness_sync,
        "router": router, "results": results,
        "clients_skipped": [] if can_run_clients else list(selected_clients),
        "passed": can_run_clients and len(results) == len(selected_clients)
        and all(result["passed"] for result in results),
    }
    atomic_write_json(output, artifact)
    return artifact


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving eval routed",
        description="Run fail-closed router and real-client acceptance for one existing alias.",
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True, help="Public router alias to evaluate.")
    parser.add_argument("--api-key-env", default="ANVIL_ROUTER_TOKEN")
    parser.add_argument("--expected-served-model", required=True)
    parser.add_argument("--expected-config-fingerprint")
    parser.add_argument("--expected-router-config-sha256")
    parser.add_argument("--min-context-tokens", type=int, default=1)
    parser.add_argument("--clients", default="openclaw,hermes")
    parser.add_argument("--openclaw-provider", default="anvil")
    parser.add_argument("--hermes-provider", default="anvil")
    parser.add_argument("--hermes-expected-provider", default="custom")
    parser.add_argument("--no-harness-sync", action="store_true")
    parser.add_argument("--openclaw-config", default="~/.openclaw/openclaw.json")
    parser.add_argument("--pi-models", default="~/.pi/agent/models.json")
    parser.add_argument("--pi-settings", default="~/.pi/agent/settings.json")
    parser.add_argument(
        "--client-state-path", default="~/.anvil-serving/state/client-catalog.json"
    )
    parser.add_argument(
        "--client-backup-root", default="~/.anvil-serving/backups/client-catalog"
    )
    parser.add_argument("--timeout-seconds", type=float, default=600)
    parser.add_argument("--run-id")
    parser.add_argument("--output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.dry_run and not (args.confirm or confirmation_authorized()):
        print("confirmation required; rerun with --confirm", file=sys.stderr)
        return 2
    try:
        artifact = run_routed_eval(
            base_url=args.base_url, alias=args.model, api_key_env=args.api_key_env,
            expected_served_model=args.expected_served_model,
            expected_config_fingerprint=args.expected_config_fingerprint,
            expected_router_config_sha256=args.expected_router_config_sha256,
            min_context_tokens=args.min_context_tokens, clients=args.clients,
            openclaw_provider=args.openclaw_provider, hermes_provider=args.hermes_provider,
            hermes_expected_provider=args.hermes_expected_provider,
            timeout_seconds=args.timeout_seconds, output=args.output,
            run_id=args.run_id, dry_run=args.dry_run,
            sync_harnesses=not args.no_harness_sync,
            openclaw_config=args.openclaw_config,
            pi_models=args.pi_models,
            pi_settings=args.pi_settings,
            client_state_path=args.client_state_path,
            client_backup_root=args.client_backup_root,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps(artifact, indent=2, sort_keys=True))
        return 0
    print(
        f"ROUTED EVAL {'PASS' if artifact['passed'] else 'FAIL'} "
        f"alias={args.model} clients={len(artifact['results'])}/{len(artifact['clients'])} "
        f"evidence={args.output}"
    )
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
