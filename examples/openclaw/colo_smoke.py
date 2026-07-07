#!/usr/bin/env python3
"""Story-driven COLO smoke/eval runner for OpenClaw -> anvil-serving.

The default mode is a deterministic fixture run that needs no OpenClaw, SSH,
router token, Docker, or model serve. Live mode can gather Fakoli Mini gateway
diagnostics over SSH and can optionally run router generation probes from the
gateway host.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


SCHEMA_VERSION = "openclaw-colo-smoke/v1"
PLUGIN_ID = "openclaw-anvil-intent-router"
DEFAULT_GATEWAY_HOST = "fakoli-mini"
DEFAULT_ROUTER_BASE_URL = "http://100.87.34.66:8000/v1"
DEFAULT_CONFIG_PATH = "examples/fakoli-dark/anvil-router.live.toml"
DEFAULT_EXPECTED_CONTEXT_WINDOW = 131072
DEFAULT_ARTIFACT = ".anvil/evidence/openclaw-colo-smoke.json"
HOMEBREW_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

PRESETS = ("planning", "quick-edit", "review", "chat", "chat-fast", "long-context")
PROOF_STATUSES = {"pass", "warn", "fail", "skipped"}
GENERATION_PROBE_HEAVY_PRESETS = frozenset({"planning", "review", "long-context"})
DEFAULT_FAST_GENERATION_MAX_TOKENS = 48
DEFAULT_HEAVY_GENERATION_MAX_TOKENS = 256
GENERATION_PROBE_PARAM = "generation_probe_max_tokens"
INTERACTION_BENCHMARK_MAX_TOKENS_PARAM = "interaction_benchmark_max_tokens"
INTERACTION_BENCHMARK_STREAM_MAX_TOKENS_PARAM = "interaction_benchmark_stream_max_tokens"
INTERACTION_BENCHMARK_REASONING_EFFORT_PARAM = "interaction_benchmark_reasoning_effort"
INTERACTION_BENCHMARK_MAX_TOKENS_BY_INTENT_PARAM = "interaction_benchmark_max_tokens_by_intent"
INTERACTION_BENCHMARK_STREAM_MAX_TOKENS_BY_INTENT_PARAM = "interaction_benchmark_stream_max_tokens_by_intent"
DEFAULT_FAST_INTERACTION_MAX_TOKENS = 192
DEFAULT_HEAVY_INTERACTION_MAX_TOKENS = 1024
DEFAULT_FAST_INTERACTION_STREAM_MAX_TOKENS = 128
DEFAULT_HEAVY_INTERACTION_STREAM_MAX_TOKENS = 512

STORIES = [
    {
        "id": "S001",
        "title": "OpenClaw provider visibility",
        "thesis": (
            "An OpenClaw user can select Anvil-owned intent presets from a single provider "
            "without hand-maintained model ids."
        ),
        "proof": "The anvil provider exists, advertises every preset, and keeps the agent allowlist in sync.",
    },
    {
        "id": "S002",
        "title": "Local-safe bounded edits",
        "thesis": (
            "Small deterministic coding edits should stay on measured local tiers when the router says "
            "the class is allowed."
        ),
        "proof": "quick-edit and chat-fast probes reach the Anvil path and deterministic edit cases pass.",
    },
    {
        "id": "S003",
        "title": "Planning-route clarity",
        "thesis": (
            "Risky planning work must reveal whether it is routed to Anvil local, native OpenClaw, "
            "or an explicit cloud tier."
        ),
        "proof": "planning probes record plugin-side and router-side decisions when available.",
    },
    {
        "id": "S004",
        "title": "Long-context completion budget",
        "thesis": (
            "OpenClaw must advertise the largest routed context window so completion budgets are not "
            "starved by a one-token clamp."
        ),
        "proof": "Every preset contextWindow is at least the expected routed-tier window.",
    },
    {
        "id": "S005",
        "title": "Installation wiring",
        "thesis": "The intent plugin must be installed, active, and allowed to inspect the turn.",
        "proof": "Plugin runtime reports loaded/activated with a before_model_resolve hook and access gate.",
    },
    {
        "id": "S006",
        "title": "Routing auditability",
        "thesis": "Operators need decision evidence that explains which model/tier actually served each intent.",
        "proof": "Router /v1/route probes produce structured decision records for representative intents.",
    },
    {
        "id": "S007",
        "title": "Performance evidence",
        "thesis": "Latency and generation rate must be measured before recommending a model or tier change.",
        "proof": "Live generation probes record latency/TTFT, and the interaction benchmark records exact usage tokens and tokens/sec where non-streaming usage is available.",
    },
    {
        "id": "S008",
        "title": "Drift repair with human gate",
        "thesis": "Config drift should be detected and repaired through product commands, not hand edits.",
        "proof": "Drift issues are explicit, and repair mode records a harness sync preview without applying.",
    },
    {
        "id": "S009",
        "title": "Explicit cloud usage",
        "thesis": "Metered cloud use must be visible and never enabled by the smoke run.",
        "proof": "The artifact distinguishes Anvil local/cloud route-probe paths and records no auto-enable; direct router probes do not exercise native OpenClaw provider dispatch.",
    },
]

CAPABILITY_CASES = [
    {
        "id": "quick_edit_python",
        "intent": "quick-edit",
        "stories": ["S002", "S006"],
        "prompt": (
            "Return only this corrected Python function:\n"
            "def inc(x):\n"
            "    return x - 1\n"
            "Change it so inc(1) == 2."
        ),
        "checks": [{"kind": "contains", "value": "return x + 1"}],
    },
    {
        "id": "json_contract",
        "intent": "chat-fast",
        "stories": ["S002", "S007"],
        "prompt": (
            "Return only JSON with keys status and items. status must be ok and items must be [1,2,3]."
        ),
        "checks": [{"kind": "json_field", "path": ["status"], "value": "ok"}],
    },
    {
        "id": "tool_call_shape",
        "intent": "chat",
        "stories": ["S006", "S009"],
        "prompt": (
            "Return only JSON for a tool call: {\"tool\":\"route_decision\",\"arguments\":{\"model\":\"chat\"}}."
        ),
        "checks": [{"kind": "json_field", "path": ["tool"], "value": "route_decision"}],
    },
    {
        "id": "review_bug",
        "intent": "review",
        "stories": ["S003", "S006"],
        "prompt": (
            "Review this function and name the concrete bug in one sentence:\n"
            "def divide(a, b):\n"
            "    return a / b"
        ),
        "checks": [{"kind": "contains_any", "values": ["zero", "ZeroDivision", "division"]}],
    },
    {
        "id": "patch_edit",
        "intent": "quick-edit",
        "stories": ["S002", "S006"],
        "prompt": (
            "Return a unified diff that changes `return false` to `return true` in file gate.py."
        ),
        "checks": [{"kind": "contains", "value": "@@"}, {"kind": "contains", "value": "return true"}],
    },
    {
        "id": "long_context_needle",
        "intent": "long-context",
        "stories": ["S004", "S007"],
        "prompt": (
            "In the following context, answer with only the needle value: "
            "ANVIL_NEEDLE_COLO_7319. Context: " + ("routing evidence " * 400)
        ),
        "checks": [{"kind": "contains", "value": "ANVIL_NEEDLE_COLO_7319"}],
    },
]

INTERACTION_CONTEXT = "routing evidence quality gate model recipe route audit " * 180
INTERACTION_BENCHMARK_CASES = [
    {
        "id": "chat_fast_summary",
        "intent": "chat-fast",
        "mode": "exact",
        "prompt": (
            "Summarize in one sentence: OpenClaw sends intent presets to Anvil "
            "Serving, and the router records the local tier decision."
        ),
    },
    {
        "id": "chat_fast_ttft",
        "intent": "chat-fast",
        "mode": "stream",
        "prompt": "Return exactly five words about fast local routing.",
    },
    {
        "id": "quick_edit_python",
        "intent": "quick-edit",
        "mode": "exact",
        "prompt": (
            "Return only the corrected Python function:\n"
            "def inc(x):\n"
            "    return x - 1\n"
            "Make inc(1) == 2."
        ),
    },
    {
        "id": "quick_edit_diff_ttft",
        "intent": "quick-edit",
        "mode": "stream",
        "prompt": "Return one-line diff changing port localhost to 127.0.0.1.",
    },
    {
        "id": "review_shell",
        "intent": "review",
        "mode": "exact",
        "prompt": (
            "Review this code for security risk in one short paragraph and give one fix:\n"
            "subprocess.run(\"git checkout \" + branch, shell=True)"
        ),
    },
    {
        "id": "review_budget_ttft",
        "intent": "review",
        "mode": "stream",
        "prompt": (
            "In one paragraph, explain why a reasoning model can fail a smoke "
            "test when max_tokens is too small."
        ),
    },
    {
        "id": "planning_rollout",
        "intent": "planning",
        "mode": "exact",
        "prompt": (
            "Create a five-step production rollout plan for OpenClaw intent "
            "routing. Keep each step under twelve words."
        ),
    },
    {
        "id": "planning_swap_ttft",
        "intent": "planning",
        "mode": "stream",
        "prompt": (
            "Give three concise bullets for safely swapping a heavy model recipe."
        ),
    },
    {
        "id": "long_context_needle",
        "intent": "long-context",
        "mode": "exact",
        "prompt": (
            "Read the context and answer with only the needle value. Context: "
            + INTERACTION_CONTEXT
            + " NEEDLE=ANVIL_COLO_91427 "
            + INTERACTION_CONTEXT
        ),
    },
    {
        "id": "long_context_ttft",
        "intent": "long-context",
        "mode": "stream",
        "prompt": (
            "Find the marker and answer with one short sentence. "
            + INTERACTION_CONTEXT
            + " MARKER=ROUTER_OK"
        ),
    },
]

SENSITIVE_KEY_RE = re.compile(
    r"(?i)^(api[-_]?key|authorization|x-api-key|secret|password|access[-_]?token|refresh[-_]?token|bearer)$"
)
TOKEN_VALUE_RE = re.compile(
    r"(?i)\b("
    r"sk-(?:proj-)?[A-Za-z0-9_-]{8,}|"
    r"hf_[A-Za-z0-9_-]{8,}|"
    r"gh[pousr]_[A-Za-z0-9_-]{8,}|"
    r"(?=[A-Za-z0-9._~+/\-]{8,}\b)(?=[A-Za-z0-9._~+/\-]*[-_])"
    r"[A-Za-z0-9._~+/\-]*secret[A-Za-z0-9._~+/\-]*|"
    r"(?=[A-Za-z0-9._~+/\-]{16,}\b)(?:api[-_]?key|token)[-_][A-Za-z0-9._~+/\-]+"
    r")\b"
)
BEARER_RE = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/\-]{8,})")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def generation_probe_fallback_max_tokens_table(fast_max_tokens: int, heavy_max_tokens: int) -> dict[str, int]:
    return {
        preset: heavy_max_tokens if preset in GENERATION_PROBE_HEAVY_PRESETS else fast_max_tokens
        for preset in PRESETS
    }


def positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def load_generation_probe_budgets(config_path: str, *, fast_default: int, heavy_default: int) -> dict[str, Any]:
    fallback_by_preset = generation_probe_fallback_max_tokens_table(fast_default, heavy_default)
    result: dict[str, Any] = {
        "source": "cli-defaults",
        "param": "params." + GENERATION_PROBE_PARAM,
        "by_tier": {},
        "by_preset": dict(fallback_by_preset),
        "defaults": {"fast": fast_default, "heavy": heavy_default},
        "warnings": [],
    }

    if not config_path:
        return result
    try:
        with open(os.path.expanduser(config_path), "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        result["warnings"].append("could not read router config generation budgets: %s" % exc)
        return result

    router = data.get("router") if isinstance(data.get("router"), dict) else {}
    raw_tiers = router.get("tiers") if isinstance(router.get("tiers"), list) else []
    by_tier: dict[str, int] = {}
    for tier in raw_tiers:
        if not isinstance(tier, dict):
            continue
        tier_id = tier.get("id")
        params = tier.get("params") if isinstance(tier.get("params"), dict) else {}
        budget = positive_int(params.get(GENERATION_PROBE_PARAM))
        if isinstance(tier_id, str) and budget is not None:
            by_tier[tier_id] = budget

    raw_presets = router.get("presets") if isinstance(router.get("presets"), dict) else {}
    by_preset = dict(fallback_by_preset)
    for preset, candidates in raw_presets.items():
        if not isinstance(preset, str) or not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, str) and candidate in by_tier:
                by_preset[preset] = by_tier[candidate]
                break

    if by_tier:
        result["source"] = "router-config"
        result["by_tier"] = by_tier
        result["by_preset"] = by_preset
    return result


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - rank) + ordered[upper] * (rank - lower)


def _default_interaction_recipe_for_tier(tier_id: str) -> dict[str, Any]:
    is_heavy = "heavy" in tier_id.lower()
    return {
        "max_tokens": DEFAULT_HEAVY_INTERACTION_MAX_TOKENS if is_heavy else DEFAULT_FAST_INTERACTION_MAX_TOKENS,
        "stream_max_tokens": (
            DEFAULT_HEAVY_INTERACTION_STREAM_MAX_TOKENS
            if is_heavy else DEFAULT_FAST_INTERACTION_STREAM_MAX_TOKENS
        ),
        "reasoning_effort": None,
        "max_tokens_by_intent": {},
        "stream_max_tokens_by_intent": {},
    }


def _positive_int_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, raw in value.items():
        parsed = positive_int(raw)
        if isinstance(key, str) and parsed is not None:
            result[key] = parsed
    return result


def _recipe_for_preset(recipe: dict[str, Any], preset: str) -> dict[str, Any]:
    resolved = dict(recipe)
    max_by_intent = recipe.get("max_tokens_by_intent") if isinstance(recipe.get("max_tokens_by_intent"), dict) else {}
    stream_by_intent = (
        recipe.get("stream_max_tokens_by_intent")
        if isinstance(recipe.get("stream_max_tokens_by_intent"), dict) else {}
    )
    if preset in max_by_intent:
        resolved["max_tokens"] = max_by_intent[preset]
    if preset in stream_by_intent:
        resolved["stream_max_tokens"] = stream_by_intent[preset]
    return resolved


def load_interaction_benchmark_recipes(config_path: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": "cli-defaults",
        "params": {
            "max_tokens": "params." + INTERACTION_BENCHMARK_MAX_TOKENS_PARAM,
            "stream_max_tokens": "params." + INTERACTION_BENCHMARK_STREAM_MAX_TOKENS_PARAM,
            "reasoning_effort": "params." + INTERACTION_BENCHMARK_REASONING_EFFORT_PARAM,
            "max_tokens_by_intent": "params." + INTERACTION_BENCHMARK_MAX_TOKENS_BY_INTENT_PARAM,
            "stream_max_tokens_by_intent": "params." + INTERACTION_BENCHMARK_STREAM_MAX_TOKENS_BY_INTENT_PARAM,
        },
        "defaults": {
            "fast": {
                "max_tokens": DEFAULT_FAST_INTERACTION_MAX_TOKENS,
                "stream_max_tokens": DEFAULT_FAST_INTERACTION_STREAM_MAX_TOKENS,
            },
            "heavy": {
                "max_tokens": DEFAULT_HEAVY_INTERACTION_MAX_TOKENS,
                "stream_max_tokens": DEFAULT_HEAVY_INTERACTION_STREAM_MAX_TOKENS,
            },
        },
        "by_tier": {},
        "by_preset": {},
        "warnings": [],
    }
    if not config_path:
        return result
    try:
        with open(os.path.expanduser(config_path), "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        result["warnings"].append("could not read router config interaction recipes: %s" % exc)
        return result

    router = data.get("router") if isinstance(data.get("router"), dict) else {}
    raw_tiers = router.get("tiers") if isinstance(router.get("tiers"), list) else []
    by_tier: dict[str, dict[str, Any]] = {}
    for tier in raw_tiers:
        if not isinstance(tier, dict):
            continue
        tier_id = tier.get("id")
        if not isinstance(tier_id, str):
            continue
        recipe = _default_interaction_recipe_for_tier(tier_id)
        params = tier.get("params") if isinstance(tier.get("params"), dict) else {}
        max_tokens = positive_int(params.get(INTERACTION_BENCHMARK_MAX_TOKENS_PARAM))
        stream_max_tokens = positive_int(params.get(INTERACTION_BENCHMARK_STREAM_MAX_TOKENS_PARAM))
        if max_tokens is not None:
            recipe["max_tokens"] = max_tokens
        if stream_max_tokens is not None:
            recipe["stream_max_tokens"] = stream_max_tokens
        reasoning_effort = params.get(INTERACTION_BENCHMARK_REASONING_EFFORT_PARAM)
        if isinstance(reasoning_effort, str) and reasoning_effort.strip():
            recipe["reasoning_effort"] = reasoning_effort.strip()
        recipe["max_tokens_by_intent"] = _positive_int_map(
            params.get(INTERACTION_BENCHMARK_MAX_TOKENS_BY_INTENT_PARAM)
        )
        recipe["stream_max_tokens_by_intent"] = _positive_int_map(
            params.get(INTERACTION_BENCHMARK_STREAM_MAX_TOKENS_BY_INTENT_PARAM)
        )
        by_tier[tier_id] = recipe

    raw_presets = router.get("presets") if isinstance(router.get("presets"), dict) else {}
    by_preset: dict[str, dict[str, Any]] = {}
    for preset, candidates in raw_presets.items():
        if not isinstance(preset, str) or not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, str) and candidate in by_tier:
                by_preset[preset] = _recipe_for_preset(by_tier[candidate], preset)
                break

    if by_tier:
        result["source"] = "router-config"
        result["by_tier"] = by_tier
        result["by_preset"] = by_preset
    return result


def story_ids() -> set[str]:
    return {story["id"] for story in STORIES}


def redacted_string(value: str) -> str:
    def token_repl(match: re.Match[str]) -> str:
        token = match.group(1)
        if ENV_NAME_RE.fullmatch(token):
            return token
        return "<redacted>"

    value = BEARER_RE.sub(lambda m: m.group(1) + "<redacted>", value)
    return TOKEN_VALUE_RE.sub(token_repl, value)


def redact(value: Any, key: str = "") -> Any:
    if SENSITIVE_KEY_RE.fullmatch(key or ""):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, tuple):
        return [redact(item, key) for item in value]
    if isinstance(value, str):
        return redacted_string(value)
    return value


def serialized_secret_findings(value: Any) -> list[str]:
    findings = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            for pattern in (BEARER_RE, TOKEN_VALUE_RE):
                for match in pattern.finditer(item):
                    candidate = match.group(2) if pattern is BEARER_RE else match.group(1)
                    if ENV_NAME_RE.fullmatch(candidate):
                        continue
                    if candidate != "<redacted>":
                        findings.append(candidate)

    visit(value)
    return findings


def api_key_shape(value: Any) -> str:
    if value is None:
        return "absent"
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "empty"
        if stripped.startswith("${") and stripped.endswith("}"):
            return "env-ref"
        return "literal"
    if isinstance(value, dict):
        source = str(value.get("source") or "").lower()
        if source == "env" or value.get("id"):
            return "object-env-ref"
        return "object"
    return type(value).__name__


def summarize_openclaw_config(config: dict[str, Any]) -> dict[str, Any]:
    models_root = config.get("models") if isinstance(config.get("models"), dict) else {}
    providers = models_root.get("providers") if isinstance(models_root.get("providers"), dict) else {}
    provider = providers.get("anvil") if isinstance(providers.get("anvil"), dict) else {}
    raw_models = provider.get("models") if isinstance(provider.get("models"), list) else []
    provider_models = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        provider_models.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "contextWindow": item.get("contextWindow"),
            "maxTokens": item.get("maxTokens"),
            "reasoning": item.get("reasoning"),
            "input": item.get("input"),
        })

    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    default_models = defaults.get("models") if isinstance(defaults.get("models"), dict) else {}
    plugin_root = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    plugin_entries = plugin_root.get("entries") if isinstance(plugin_root.get("entries"), dict) else {}
    plugin_entry = plugin_entries.get(PLUGIN_ID) if isinstance(plugin_entries.get(PLUGIN_ID), dict) else {}
    plugin_hooks = plugin_entry.get("hooks") if isinstance(plugin_entry.get("hooks"), dict) else {}
    plugin_config = plugin_entry.get("config") if isinstance(plugin_entry.get("config"), dict) else {}

    safe_plugin_config = {}
    for key in ("routeEndpoint", "routeAuthEnv", "routeTimeoutMs", "nativeProvider", "nativeModel"):
        if key in plugin_config:
            safe_plugin_config[key] = plugin_config.get(key)

    return redact({
        "provider_present": bool(provider),
        "mode": models_root.get("mode"),
        "provider": {
            "id": "anvil" if provider else None,
            "baseUrl": provider.get("baseUrl"),
            "api": provider.get("api"),
            "api_key_shape": api_key_shape(provider.get("apiKey")),
            "models": provider_models,
        },
        "agents": {
            "default_model_refs": sorted(str(k) for k in default_models.keys() if str(k).startswith("anvil/")),
        },
        "plugin_entry": {
            "id": PLUGIN_ID if plugin_entry else None,
            "hooks": {
                "allowConversationAccess": plugin_hooks.get("allowConversationAccess"),
                "allowPromptInjection": plugin_hooks.get("allowPromptInjection"),
            },
            "config": safe_plugin_config,
        },
    })


def issue(severity: str, code: str, message: str, details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "details": details or {}}


def audit_openclaw_config(
    summary: dict[str, Any],
    *,
    expected_base_url: str,
    expected_presets: Iterable[str] = PRESETS,
    expected_context_window: int = DEFAULT_EXPECTED_CONTEXT_WINDOW,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    provider = summary.get("provider") if isinstance(summary.get("provider"), dict) else {}
    if not summary.get("provider_present"):
        issues.append(issue("fail", "missing_anvil_provider", "OpenClaw config has no anvil provider."))
        return issues

    if provider.get("api") != "openai-completions":
        issues.append(issue(
            "fail",
            "bad_provider_api",
            "The anvil provider must use OpenClaw's openai-completions API mode.",
            {"actual": provider.get("api")},
        ))
    if expected_base_url and provider.get("baseUrl") != expected_base_url:
        issues.append(issue(
            "warn",
            "base_url_drift",
            "The anvil provider baseUrl differs from the expected COLO router URL.",
            {"expected": expected_base_url, "actual": provider.get("baseUrl")},
        ))
    if provider.get("api_key_shape") == "literal":
        issues.append(issue(
            "fail",
            "literal_api_key",
            "The provider uses a literal API key; use an environment-variable reference instead.",
        ))
    elif provider.get("api_key_shape") in ("absent", "empty"):
        issues.append(issue(
            "fail",
            "missing_api_key",
            "The provider has no router API key reference or literal token.",
        ))

    model_rows = provider.get("models") if isinstance(provider.get("models"), list) else []
    models = {str(row.get("id")): row for row in model_rows if isinstance(row, dict) and row.get("id")}
    expected = set(expected_presets)
    missing = sorted(expected - set(models))
    extra = sorted(set(models) - expected)
    if missing:
        issues.append(issue("fail", "missing_presets", "OpenClaw provider is missing Anvil presets.", {"missing": missing}))
    if extra:
        issues.append(issue("warn", "extra_presets", "OpenClaw provider has extra Anvil preset ids.", {"extra": extra}))

    low_context = []
    for preset, row in sorted(models.items()):
        try:
            context_window = int(row.get("contextWindow"))
        except (TypeError, ValueError):
            low_context.append({"preset": preset, "contextWindow": row.get("contextWindow")})
            continue
        if context_window < expected_context_window:
            low_context.append({"preset": preset, "contextWindow": context_window})
    if low_context:
        issues.append(issue(
            "fail",
            "context_window_drift",
            "One or more presets advertise a contextWindow below the expected routed-tier window.",
            {"expected_min": expected_context_window, "presets": low_context},
        ))

    default_refs = set(summary.get("agents", {}).get("default_model_refs", []))
    missing_refs = sorted("anvil/" + preset for preset in expected if "anvil/" + preset not in default_refs)
    if missing_refs:
        issues.append(issue(
            "fail",
            "missing_agent_allowlist",
            "OpenClaw agent defaults do not allowlist every Anvil preset.",
            {"missing": missing_refs},
        ))

    plugin_entry = summary.get("plugin_entry") if isinstance(summary.get("plugin_entry"), dict) else {}
    hooks = plugin_entry.get("hooks") if isinstance(plugin_entry.get("hooks"), dict) else {}
    if hooks.get("allowConversationAccess") is not True:
        issues.append(issue(
            "fail",
            "conversation_access_disabled",
            "The intent plugin needs allowConversationAccess for before_model_resolve.",
        ))
    if not plugin_entry.get("config", {}).get("routeEndpoint"):
        issues.append(issue(
            "warn",
            "plugin_not_authoritative",
            "No plugin routeEndpoint is configured; router decisions must be validated separately.",
        ))
    return issues


def audit_plugin_runtime(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not runtime:
        return [issue("fail", "missing_plugin_runtime", "No OpenClaw plugin runtime data was collected.")]
    plugin = runtime.get("plugin") if isinstance(runtime.get("plugin"), dict) else {}
    status = runtime.get("status") or plugin.get("status")
    activated = runtime.get("activated")
    if activated is None:
        activated = plugin.get("activated")
    hook_count = runtime.get("hookCount")
    if hook_count is None:
        hook_count = plugin.get("hookCount")

    if status not in (None, "loaded"):
        issues.append(issue("fail", "plugin_not_loaded", "Intent plugin runtime is not loaded.", {"status": status}))
    if activated is not True:
        issues.append(issue("fail", "plugin_not_activated", "Intent plugin runtime is not activated."))

    hook_names: set[str] = set()
    for key in ("hooks", "typedHooks", "registeredHooks"):
        hooks = runtime.get(key)
        if isinstance(hooks, list):
            for item in hooks:
                if isinstance(item, str):
                    hook_names.add(item)
                elif isinstance(item, dict):
                    for name_key in ("name", "type", "hook", "event"):
                        if item.get(name_key):
                            hook_names.add(str(item[name_key]))
    if isinstance(runtime.get("hookTypes"), list):
        hook_names.update(str(item) for item in runtime["hookTypes"])
    if hook_count in (None, 0) and not hook_names:
        issues.append(issue("fail", "plugin_hook_count_zero", "Intent plugin has no registered hooks."))
    elif "before_model_resolve" not in hook_names and hook_names:
        issues.append(issue(
            "fail",
            "missing_before_model_resolve",
            "Intent plugin runtime did not report a before_model_resolve hook.",
            {"hooks": sorted(hook_names)},
        ))
    return issues


def proof(
    proof_id: str,
    name: str,
    story_refs: Iterable[str],
    status: str,
    evidence: Optional[dict[str, Any]] = None,
    message: str = "",
) -> dict[str, Any]:
    if status not in PROOF_STATUSES:
        raise ValueError("bad proof status: %s" % status)
    unknown = sorted(set(story_refs) - story_ids())
    if unknown:
        raise ValueError("proof %s references unknown stories: %s" % (proof_id, ", ".join(unknown)))
    return redact({
        "id": proof_id,
        "name": name,
        "story_ids": list(story_refs),
        "status": status,
        "message": message,
        "evidence": evidence or {},
        "checked_at": utc_now(),
    })


def status_from_issues(issues: Iterable[dict[str, Any]], *, skipped_if_empty: bool = False) -> str:
    issues = list(issues)
    if skipped_if_empty and not issues:
        return "skipped"
    severities = {item.get("severity") for item in issues}
    if "fail" in severities:
        return "fail"
    if "warn" in severities:
        return "warn"
    return "pass"


def validate_story_proofs(stories: list[dict[str, Any]], proofs: list[dict[str, Any]]) -> list[str]:
    known = {story["id"] for story in stories}
    covered: set[str] = set()
    errors: list[str] = []
    for item in proofs:
        refs = item.get("story_ids")
        if not isinstance(refs, list) or not refs:
            errors.append("proof %s has no story_ids" % item.get("id"))
            continue
        for ref in refs:
            if ref not in known:
                errors.append("proof %s references unknown story %s" % (item.get("id"), ref))
            else:
                covered.add(ref)
    for missing in sorted(known - covered):
        errors.append("story %s has no proof" % missing)
    return errors


def verdict_from_proofs(proofs: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: 0 for status in PROOF_STATUSES}
    for item in proofs:
        counts[item.get("status", "fail")] = counts.get(item.get("status", "fail"), 0) + 1
    mapping_errors = validate_story_proofs(STORIES, proofs)
    secret_findings = serialized_secret_findings(proofs)
    if mapping_errors or secret_findings or counts.get("fail"):
        status = "fail"
    elif counts.get("warn") or counts.get("skipped"):
        status = "warn"
    else:
        status = "pass"
    return {
        "status": status,
        "proof_counts": counts,
        "story_proof_errors": mapping_errors,
        "secret_findings": secret_findings,
        "generated_at": utc_now(),
    }


def endpoint(base_url: str, suffix: str) -> str:
    base = base_url.rstrip("/")
    suffix = "/" + suffix.lstrip("/")
    if base.endswith("/v1"):
        return base + suffix
    return base + "/v1" + suffix


def command_result(argv: list[str], *, timeout: int, stdin: Optional[str] = None) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
        return redact({
            "argv": argv,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        })
    except FileNotFoundError as exc:
        return {"argv": argv, "returncode": None, "error": "command_not_found", "message": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {"argv": argv, "returncode": None, "error": "timeout", "timeout_seconds": exc.timeout}


def posix_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def gateway_target(host: str, user: str = "") -> str:
    for value, label in ((host, "gateway host"), (user, "gateway user")):
        if value and (value.startswith("-") or any(ch.isspace() for ch in value)):
            raise ValueError("%s must not start with '-' or contain whitespace" % label)
    return (user + "@" if user else "") + host


REMOTE_COLLECTOR = r"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PRESETS = ("planning", "quick-edit", "review", "chat", "chat-fast", "long-context")
PLUGIN_ID = "openclaw-anvil-intent-router"
ARGS = json.loads(os.environ.get("ANVIL_COLO_ARGS", "{}"))
INTERACTION_CONTEXT = "routing evidence quality gate model recipe route audit " * 180
INTERACTION_BENCHMARK_CASES = [
    {
        "id": "chat_fast_summary",
        "intent": "chat-fast",
        "mode": "exact",
        "prompt": (
            "Summarize in one sentence: OpenClaw sends intent presets to Anvil "
            "Serving, and the router records the local tier decision."
        ),
    },
    {
        "id": "chat_fast_ttft",
        "intent": "chat-fast",
        "mode": "stream",
        "prompt": "Return exactly five words about fast local routing.",
    },
    {
        "id": "quick_edit_python",
        "intent": "quick-edit",
        "mode": "exact",
        "prompt": (
            "Return only the corrected Python function:\n"
            "def inc(x):\n"
            "    return x - 1\n"
            "Make inc(1) == 2."
        ),
    },
    {
        "id": "quick_edit_diff_ttft",
        "intent": "quick-edit",
        "mode": "stream",
        "prompt": "Return one-line diff changing port localhost to 127.0.0.1.",
    },
    {
        "id": "review_shell",
        "intent": "review",
        "mode": "exact",
        "prompt": (
            "Review this code for security risk in one short paragraph and give one fix:\n"
            "subprocess.run(\"git checkout \" + branch, shell=True)"
        ),
    },
    {
        "id": "review_budget_ttft",
        "intent": "review",
        "mode": "stream",
        "prompt": (
            "In one paragraph, explain why a reasoning model can fail a smoke "
            "test when max_tokens is too small."
        ),
    },
    {
        "id": "planning_rollout",
        "intent": "planning",
        "mode": "exact",
        "prompt": (
            "Create a five-step production rollout plan for OpenClaw intent "
            "routing. Keep each step under twelve words."
        ),
    },
    {
        "id": "planning_swap_ttft",
        "intent": "planning",
        "mode": "stream",
        "prompt": "Give three concise bullets for safely swapping a heavy model recipe.",
    },
    {
        "id": "long_context_needle",
        "intent": "long-context",
        "mode": "exact",
        "prompt": (
            "Read the context and answer with only the needle value. Context: "
            + INTERACTION_CONTEXT
            + " NEEDLE=ANVIL_COLO_91427 "
            + INTERACTION_CONTEXT
        ),
    },
    {
        "id": "long_context_ttft",
        "intent": "long-context",
        "mode": "stream",
        "prompt": (
            "Find the marker and answer with one short sentence. "
            + INTERACTION_CONTEXT
            + " MARKER=ROUTER_OK"
        ),
    },
]

def generation_max_tokens(preset):
    table = ARGS.get("generation_probe_max_tokens_by_preset")
    if isinstance(table, dict):
        try:
            return int(table.get(preset, 48))
        except Exception:
            return 48
    return 48

def generation_max_tokens_for_route(preset, route_row):
    tier_table = ARGS.get("generation_probe_max_tokens_by_tier")
    if isinstance(route_row, dict) and isinstance(tier_table, dict):
        response = route_row.get("response") if isinstance(route_row.get("response"), dict) else {}
        provider = response.get("provider")
        if isinstance(provider, str) and provider in tier_table:
            try:
                return int(tier_table[provider])
            except Exception:
                pass
    return generation_max_tokens(preset)

def interaction_recipe_for_route(preset, route_row):
    tier_table = ARGS.get("interaction_benchmark_recipe_by_tier")
    preset_table = ARGS.get("interaction_benchmark_recipe_by_preset")
    recipe = None
    source = "preset"
    if isinstance(route_row, dict) and isinstance(tier_table, dict):
        response = route_row.get("response") if isinstance(route_row.get("response"), dict) else {}
        provider = response.get("provider")
        if isinstance(provider, str) and isinstance(tier_table.get(provider), dict):
            recipe = dict(tier_table[provider])
            source = "route-provider"
    if recipe is None and isinstance(preset_table, dict) and isinstance(preset_table.get(preset), dict):
        recipe = dict(preset_table[preset])
    if recipe is None:
        recipe = {"max_tokens": 192, "stream_max_tokens": 128, "reasoning_effort": None}
        source = "fallback"
    max_by_intent = recipe.get("max_tokens_by_intent") if isinstance(recipe.get("max_tokens_by_intent"), dict) else {}
    stream_by_intent = (
        recipe.get("stream_max_tokens_by_intent")
        if isinstance(recipe.get("stream_max_tokens_by_intent"), dict) else {}
    )
    if preset in max_by_intent:
        recipe["max_tokens"] = max_by_intent[preset]
    if preset in stream_by_intent:
        recipe["stream_max_tokens"] = stream_by_intent[preset]
    recipe["source"] = source
    return recipe

def key_shape(value):
    if value is None:
        return "absent"
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "empty"
        if stripped.startswith("${") and stripped.endswith("}"):
            return "env-ref"
        return "literal"
    if isinstance(value, dict):
        if str(value.get("source") or "").lower() == "env" or value.get("id"):
            return "object-env-ref"
        return "object"
    return type(value).__name__

def resolve_env(name):
    value = os.environ.get(name)
    if value:
        return value
    try:
        proc = subprocess.run(["launchctl", "getenv", name], capture_output=True, text=True, timeout=2)
    except Exception:
        return ""
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return ""

def resolve_key(value):
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("${") and stripped.endswith("}"):
            return resolve_env(stripped[2:-1])
        return stripped
    if isinstance(value, dict):
        env_name = value.get("id") or value.get("env")
        if env_name:
            return resolve_env(str(env_name))
    return ""

def redact_value(value, token):
    if not token:
        return value
    if isinstance(value, str):
        return value.replace(token, "<redacted>")
    if isinstance(value, list):
        return [redact_value(item, token) for item in value]
    if isinstance(value, dict):
        return {k: redact_value(v, token) for k, v in value.items()}
    return value

def load_openclaw_config():
    path = os.path.expanduser("~/.openclaw/openclaw.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        return {"path": path, "error": str(exc)}, {}, ""

    models_root = data.get("models") if isinstance(data.get("models"), dict) else {}
    providers = models_root.get("providers") if isinstance(models_root.get("providers"), dict) else {}
    provider = providers.get("anvil") if isinstance(providers.get("anvil"), dict) else {}
    api_key = provider.get("apiKey")
    token = resolve_key(api_key)
    provider_models = []
    raw_models = provider.get("models") if isinstance(provider.get("models"), list) else []
    for item in raw_models:
        if isinstance(item, dict):
            provider_models.append({
                "id": item.get("id"),
                "name": item.get("name"),
                "contextWindow": item.get("contextWindow"),
                "maxTokens": item.get("maxTokens"),
                "reasoning": item.get("reasoning"),
                "input": item.get("input"),
            })

    agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    default_models = defaults.get("models") if isinstance(defaults.get("models"), dict) else {}
    plugin_root = data.get("plugins") if isinstance(data.get("plugins"), dict) else {}
    plugin_entries = plugin_root.get("entries") if isinstance(plugin_root.get("entries"), dict) else {}
    plugin_entry = plugin_entries.get(PLUGIN_ID) if isinstance(plugin_entries.get(PLUGIN_ID), dict) else {}
    hooks = plugin_entry.get("hooks") if isinstance(plugin_entry.get("hooks"), dict) else {}
    plugin_config = plugin_entry.get("config") if isinstance(plugin_entry.get("config"), dict) else {}

    safe = {
        "path": path,
        "provider_present": bool(provider),
        "mode": models_root.get("mode"),
        "provider": {
            "id": "anvil" if provider else None,
            "baseUrl": provider.get("baseUrl"),
            "api": provider.get("api"),
            "api_key_shape": key_shape(api_key),
            "models": provider_models,
        },
        "agents": {
            "default_model_refs": sorted(str(k) for k in default_models.keys() if str(k).startswith("anvil/")),
        },
        "plugin_entry": {
            "id": PLUGIN_ID if plugin_entry else None,
            "hooks": {
                "allowConversationAccess": hooks.get("allowConversationAccess"),
                "allowPromptInjection": hooks.get("allowPromptInjection"),
            },
            "config": {
                k: plugin_config.get(k)
                for k in ("routeEndpoint", "routeAuthEnv", "routeTimeoutMs", "nativeProvider", "nativeModel")
                if k in plugin_config
            },
        },
    }
    return safe, provider, token

def inspect_plugin():
    cmd = ["openclaw", "plugins", "inspect", PLUGIN_ID, "--runtime", "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception as exc:
        return {"command": cmd, "error": str(exc)}
    result = {"command": cmd, "returncode": proc.returncode}
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except Exception:
            result["stdout_preview"] = proc.stdout[:1200]
        else:
            result.update(parsed if isinstance(parsed, dict) else {"payload": parsed})
    if proc.stderr.strip():
        result["stderr_preview"] = proc.stderr[:1200]
    return result

def http_json(method, url, token="", body=None, timeout=20):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
        headers["x-api-key"] = token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw or "{}")
            except Exception:
                parsed = {"raw_preview": raw[:1200]}
            return {
                "status": getattr(response, "status", response.getcode()),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "response": redact_value(parsed, token),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read(4096).decode("utf-8", "replace")
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            parsed = {"raw_preview": raw[:1200]}
        return {
            "status": exc.code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "response": redact_value(parsed, token),
        }
    except Exception as exc:
        return {
            "status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
        }

def extract_text(payload):
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content
    content = payload.get("content")
    if isinstance(content, str):
        return content
    return ""

def http_chat(url, token, body, timeout=180):
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
        headers["x-api-key"] = token
    chat_body = dict(body)
    chat_body["stream"] = False
    request = urllib.request.Request(
        url,
        data=json.dumps(chat_body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw or "{}")
            except Exception:
                parsed = {"raw_preview": raw[:1200]}
            elapsed = time.perf_counter() - started
            text = extract_text(parsed)
            usage = parsed.get("usage") if isinstance(parsed, dict) and isinstance(parsed.get("usage"), dict) else {}
            choices = parsed.get("choices") if isinstance(parsed, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            output_tokens = usage.get("completion_tokens")
            if not isinstance(output_tokens, int):
                output_tokens = len(text.split())
            return {
                "status": getattr(response, "status", response.getcode()),
                "latency_ms": round(elapsed * 1000, 1),
                "finish_reason": choice.get("finish_reason"),
                "usage": usage,
                "text": text,
                "output_tokens": output_tokens,
                "tokens_per_second": round(output_tokens / max(elapsed, 0.001), 2),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read(4096).decode("utf-8", "replace")
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            parsed = {"raw_preview": raw[:1200]}
        return {
            "status": exc.code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "finish_reason": None,
            "usage": {},
            "text": "",
            "output_tokens": 0,
            "tokens_per_second": 0.0,
            "error_response": redact_value(parsed.get("error", parsed) if isinstance(parsed, dict) else parsed, token),
        }
    except Exception as exc:
        return {
            "status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "finish_reason": None,
            "usage": {},
            "text": "",
            "output_tokens": 0,
            "tokens_per_second": 0.0,
            "error_response": str(exc),
        }

def http_stream_chat(url, token, body, timeout=120):
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
        headers["x-api-key"] = token
    stream_body = dict(body)
    stream_body["stream"] = True
    request = urllib.request.Request(
        url,
        data=json.dumps(stream_body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    first_token_at = None
    finish_reason = None
    parts = []
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line or line.startswith(":") or not line.lower().startswith("data:"):
                    continue
                payload = line.split(":", 1)[1].strip()
                if payload == "[DONE]":
                    break
                try:
                    parsed = json.loads(payload)
                except Exception:
                    continue
                choices = parsed.get("choices") if isinstance(parsed, dict) else None
                if not choices:
                    continue
                choice = choices[0] if isinstance(choices[0], dict) else {}
                if choice.get("finish_reason") is not None:
                    finish_reason = choice.get("finish_reason")
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                content = delta.get("content")
                if isinstance(content, str):
                    if content and first_token_at is None:
                        first_token_at = time.perf_counter()
                    parts.append(content)
            elapsed = time.perf_counter() - started
            text = "".join(parts)
            return {
                "status": getattr(response, "status", response.getcode()),
                "latency_ms": round(elapsed * 1000, 1),
                "ttft_ms": round((first_token_at - started) * 1000, 1) if first_token_at else None,
                "finish_reason": finish_reason,
                "text": text,
                "output_tokens": None,
                "tokens_per_second": None,
                "measurement_note": "streaming responses do not include exact usage tokens",
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read(4096).decode("utf-8", "replace")
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            parsed = {"raw_preview": raw[:1200]}
        return {
            "status": exc.code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "ttft_ms": None,
            "finish_reason": None,
            "text": "",
            "output_tokens": 0,
            "tokens_per_second": 0.0,
            "error_response": redact_value(parsed.get("error", parsed) if isinstance(parsed, dict) else parsed, token),
        }
    except Exception as exc:
        return {
            "status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "ttft_ms": None,
            "finish_reason": None,
            "text": "",
            "output_tokens": 0,
            "tokens_per_second": 0.0,
            "error_response": str(exc),
        }

def run_interaction_benchmark(chat_url, token, route_by_intent):
    records = []
    for case in INTERACTION_BENCHMARK_CASES:
        if not isinstance(case, dict):
            continue
        intent = str(case.get("intent") or "chat-fast")
        mode = str(case.get("mode") or "exact")
        prompt = str(case.get("prompt") or "")
        recipe = interaction_recipe_for_route(intent, route_by_intent.get(intent))
        response = route_by_intent.get(intent, {}).get("response") if isinstance(route_by_intent.get(intent), dict) else {}
        max_tokens_key = "stream_max_tokens" if mode == "stream" else "max_tokens"
        try:
            max_tokens = int(recipe.get(max_tokens_key) or recipe.get("max_tokens") or 128)
        except Exception:
            max_tokens = 128
        body = {
            "model": intent,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        reasoning_effort = recipe.get("reasoning_effort")
        if isinstance(reasoning_effort, str) and reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if mode == "stream":
            result = http_stream_chat(chat_url, token, body, timeout=180)
        else:
            result = http_chat(chat_url, token, body, timeout=240)
        text = result.get("text") or ""
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        records.append({
            "case_id": case.get("id"),
            "intent": intent,
            "mode": mode,
            "status": result.get("status"),
            "latency_ms": result.get("latency_ms"),
            "ttft_ms": result.get("ttft_ms"),
            "finish_reason": result.get("finish_reason"),
            "max_tokens": max_tokens,
            "reasoning_effort": body.get("reasoning_effort"),
            "route": {
                "tier": response.get("tier") if isinstance(response, dict) else None,
                "provider": response.get("provider") if isinstance(response, dict) else None,
                "model": response.get("model") if isinstance(response, dict) else None,
            },
            "recipe": {
                "source": recipe.get("source"),
                "max_tokens": recipe.get("max_tokens"),
                "stream_max_tokens": recipe.get("stream_max_tokens"),
                "reasoning_effort": recipe.get("reasoning_effort"),
            },
            "usage": usage,
            "output_tokens": result.get("output_tokens"),
            "tokens_per_second": result.get("tokens_per_second"),
            "response_preview": text[:300],
            "error_response": result.get("error_response"),
        })
    return records

def probe_router(base_url, token, run_generations):
    models_url = base_url.rstrip("/") + "/models"
    route_url = base_url.rstrip("/") + "/route"
    chat_url = base_url.rstrip("/") + "/chat/completions"
    probes = {
        "source": "gateway",
        "base_url": base_url,
        "unauthenticated_models": http_json("GET", models_url),
        "authenticated_models": None,
        "routes": [],
    }
    if not token:
        probes["authenticated_models"] = {"status": "skipped", "reason": "no router token resolved on gateway"}
        return probes, [], []
    probes["authenticated_models"] = http_json("GET", models_url, token=token)
    generations = []
    for preset in PRESETS:
        prompt = "COLO route probe for %s. Return a short acknowledgement." % preset
        body = {"model": preset, "messages": [{"role": "user", "content": prompt}], "max_tokens": 32}
        probes["routes"].append({"intent": preset, **http_json("POST", route_url, token=token, body=body)})
    route_by_intent = {row.get("intent"): row for row in probes["routes"] if isinstance(row, dict)}
    if run_generations:
        for preset in ("quick-edit", "chat-fast", "review", "long-context"):
            prompt = "COLO generation probe for %s. Return exactly: anvil-colo-ok" % preset
            max_tokens = generation_max_tokens_for_route(preset, route_by_intent.get(preset))
            body = {"model": preset, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}
            result = http_stream_chat(chat_url, token, body, timeout=120)
            text = result.get("text") or ""
            generations.append({
                "case_id": "live_" + preset,
                "intent": preset,
                "transport": "gateway-direct-router",
                "status": result.get("status"),
                "latency_ms": result.get("latency_ms"),
                "ttft_ms": result.get("ttft_ms"),
                "finish_reason": result.get("finish_reason"),
                "max_tokens": max_tokens,
                "output_tokens": result.get("output_tokens"),
                "tokens_per_second": result.get("tokens_per_second"),
                "measurement_note": result.get("measurement_note"),
                "response_preview": text[:300],
                "error_response": result.get("error_response"),
            })
    interaction_records = []
    if bool(ARGS.get("run_interaction_benchmark")):
        interaction_records = run_interaction_benchmark(chat_url, token, route_by_intent)
    return probes, generations, interaction_records

config, provider, token = load_openclaw_config()
base_url = ARGS.get("router_base_url") or provider.get("baseUrl") or ""
if not base_url.endswith("/v1"):
    base_url = base_url.rstrip("/") + "/v1"
router_probes, generations, interaction_records = (
    probe_router(base_url, token, bool(ARGS.get("run_generations"))) if base_url else ({}, [], [])
)
print(json.dumps({
    "ok": True,
    "gateway": {"host": ARGS.get("gateway_host"), "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
    "openclaw_config": config,
    "plugin_runtime": inspect_plugin(),
    "router_probes": router_probes,
    "e2e_turns": generations,
    "interaction_benchmark_turns": interaction_records,
}))
"""


def run_gateway_collector(args: argparse.Namespace) -> dict[str, Any]:
    budgets = load_generation_probe_budgets(
        args.config,
        fast_default=args.fast_generation_max_tokens,
        heavy_default=args.heavy_generation_max_tokens,
    )
    interaction_recipes = load_interaction_benchmark_recipes(args.config)
    payload = {
        "gateway_host": args.gateway_host,
        "router_base_url": args.router_base_url,
        "run_generations": bool(args.run_generations),
        "run_interaction_benchmark": bool(args.run_interaction_benchmark),
        "generation_probe_max_tokens_by_tier": budgets.get("by_tier", {}),
        "generation_probe_max_tokens_by_preset": budgets.get("by_preset", {}),
        "interaction_benchmark_recipe_by_tier": interaction_recipes.get("by_tier", {}),
        "interaction_benchmark_recipe_by_preset": interaction_recipes.get("by_preset", {}),
    }
    remote = (
        "export PATH=" + HOMEBREW_PATH + "; "
        "ANVIL_COLO_ARGS=" + posix_quote(json.dumps(payload, separators=(",", ":"))) + " "
        "python3 -"
    )
    target = gateway_target(args.gateway_host, args.gateway_user)
    argv = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=%d" % args.timeout_seconds,
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=1",
        "--",
        target,
        remote,
    ]
    collector_timeout = args.timeout_seconds + 30
    if args.run_generations:
        collector_timeout += 120
    if args.run_interaction_benchmark:
        collector_timeout += 420
    result = command_result(argv, timeout=collector_timeout, stdin=REMOTE_COLLECTOR)
    if result.get("returncode") != 0:
        return {"ok": False, "command": result}
    try:
        parsed = json.loads(result.get("stdout") or "{}")
    except ValueError as exc:
        return {"ok": False, "command": result, "error": "bad_json", "message": str(exc)}
    parsed["command"] = {
        "argv": argv,
        "returncode": result.get("returncode"),
        "duration_ms": result.get("duration_ms"),
    }
    return redact(parsed)


def check_output(text: str, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for item in checks:
        kind = item.get("kind")
        if kind == "contains":
            if str(item.get("value")) not in text:
                failures.append({"check": item, "reason": "substring not found"})
        elif kind == "contains_any":
            values = [str(v) for v in item.get("values", [])]
            lower = text.lower()
            if not any(value.lower() in lower for value in values):
                failures.append({"check": item, "reason": "none of the substrings were found"})
        elif kind == "json_field":
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                failures.append({"check": item, "reason": "not valid JSON: %s" % exc})
                continue
            current: Any = parsed
            for part in item.get("path", []):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    failures.append({"check": item, "reason": "missing path"})
                    break
            else:
                if current != item.get("value"):
                    failures.append({"check": item, "reason": "value mismatch", "actual": current})
        else:
            failures.append({"check": item, "reason": "unknown check kind"})
    return failures


def summarize_benchmarks(turns: list[dict[str, Any]], min_tps: float) -> dict[str, Any]:
    completed = [
        turn for turn in turns
        if turn.get("status") in ("pass", 200)
    ]
    tps_values = [float(turn["tokens_per_second"]) for turn in completed if isinstance(turn.get("tokens_per_second"), (int, float))]
    latencies = [float(turn["latency_ms"]) for turn in completed if isinstance(turn.get("latency_ms"), (int, float))]
    output_tokens = [int(turn["output_tokens"]) for turn in completed if isinstance(turn.get("output_tokens"), int)]
    aggregate = {
        "requests": len(turns),
        "completed": len(completed),
        "latency_ms_avg": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "output_tokens_total": sum(output_tokens),
        "tokens_per_second_avg": round(sum(tps_values) / len(tps_values), 2) if tps_values else None,
        "token_measurement": "exact when usage tokens are present; unavailable for streaming probes",
    }
    warnings = []
    if min_tps and aggregate["tokens_per_second_avg"] is None:
        warnings.append(issue(
            "warn",
            "throughput_unavailable",
            "Exact generation throughput is unavailable for these streaming probes.",
            {"expected_min_tokens_per_second": min_tps},
        ))
    elif min_tps and aggregate["tokens_per_second_avg"] is not None and aggregate["tokens_per_second_avg"] < min_tps:
        warnings.append(issue(
            "warn",
            "throughput_below_expectation",
            "Measured generation throughput is below the requested expectation.",
            {"expected_min_tokens_per_second": min_tps, "actual": aggregate["tokens_per_second_avg"]},
        ))
    return {
        "expectations": {
            "min_tokens_per_second": min_tps or None,
            "source": "operator-supplied" if min_tps else "not configured",
        },
        "aggregate": aggregate,
        "warnings": warnings,
    }


def summarize_interaction_benchmarks(records: list[dict[str, Any]], *, requested: bool) -> dict[str, Any]:
    completed = [
        record for record in records
        if record.get("status") == 200 and isinstance(record.get("output_tokens"), int)
    ]
    exact = [record for record in completed if record.get("mode") == "exact"]
    stream = [record for record in completed if record.get("mode") == "stream"]
    latencies = [
        float(record["latency_ms"]) for record in completed
        if isinstance(record.get("latency_ms"), (int, float))
    ]
    ttfts = [
        float(record["ttft_ms"]) for record in stream
        if isinstance(record.get("ttft_ms"), (int, float))
    ]
    tps = [
        float(record["tokens_per_second"]) for record in exact
        if isinstance(record.get("tokens_per_second"), (int, float))
    ]
    output_tokens = [
        int(record["output_tokens"]) for record in exact
        if isinstance(record.get("output_tokens"), int)
    ]
    status_counts: dict[str, int] = {}
    finish_counts: dict[str, int] = {}
    by_intent: dict[str, dict[str, Any]] = {}
    for record in records:
        status_counts[str(record.get("status"))] = status_counts.get(str(record.get("status")), 0) + 1
        if record.get("status") == 200:
            finish_key = str(record.get("finish_reason"))
            finish_counts[finish_key] = finish_counts.get(finish_key, 0) + 1
        intent = str(record.get("intent") or "unknown")
        row = by_intent.setdefault(intent, {"requests": 0, "completed": 0, "finish_reasons": {}, "statuses": {}})
        row["requests"] += 1
        row["statuses"][str(record.get("status"))] = row["statuses"].get(str(record.get("status")), 0) + 1
        if record.get("status") == 200:
            row["completed"] += 1
            finish_key = str(record.get("finish_reason"))
            row["finish_reasons"][finish_key] = row["finish_reasons"].get(finish_key, 0) + 1

    warnings = []
    if requested and not records:
        warnings.append(issue(
            "fail",
            "interaction_benchmark_not_recorded",
            "Interaction benchmark was requested but no records were returned.",
        ))
    for record in records:
        if record.get("status") != 200:
            warnings.append(issue(
                "fail",
                "interaction_benchmark_request_failed",
                "An interaction benchmark request did not return HTTP 200.",
                {"case_id": record.get("case_id"), "intent": record.get("intent"), "status": record.get("status")},
            ))
        if record.get("finish_reason") == "length":
            warnings.append(issue(
                "fail",
                "interaction_benchmark_truncated",
                "An interaction benchmark request exhausted its configured completion budget.",
                {"case_id": record.get("case_id"), "intent": record.get("intent"), "max_tokens": record.get("max_tokens")},
            ))
        if record.get("mode") == "exact" and record.get("status") == 200 and not record.get("usage"):
            warnings.append(issue(
                "warn",
                "interaction_benchmark_usage_missing",
                "An exact interaction benchmark response did not include usage token counts.",
                {"case_id": record.get("case_id"), "intent": record.get("intent")},
            ))

    return {
        "requested": requested,
        "cases": len(INTERACTION_BENCHMARK_CASES),
        "aggregate": {
            "requests": len(records),
            "completed": len(completed),
            "status_counts": status_counts,
            "finish_reasons": finish_counts,
            "latency_ms_p50": round(percentile(latencies, 50), 1) if latencies else None,
            "latency_ms_p95": round(percentile(latencies, 95), 1) if latencies else None,
            "ttft_ms_p50": round(percentile(ttfts, 50), 1) if ttfts else None,
            "ttft_ms_p95": round(percentile(ttfts, 95), 1) if ttfts else None,
            "exact_output_tokens_total": sum(output_tokens),
            "exact_tokens_per_second_p50": round(percentile(tps, 50), 2) if tps else None,
            "exact_tokens_per_second_p95": round(percentile(tps, 95), 2) if tps else None,
        },
        "by_intent": by_intent,
        "warnings": warnings,
        "records": records,
    }


def fixture_openclaw_config(args: argparse.Namespace) -> dict[str, Any]:
    models = []
    max_tokens = {
        "planning": 32000,
        "quick-edit": 8192,
        "review": 16000,
        "chat": 8192,
        "chat-fast": 8192,
        "long-context": 16000,
    }
    for preset in PRESETS:
        models.append({
            "id": preset,
            "name": "Anvil · " + preset.replace("-", " ").title(),
            "reasoning": True,
            "input": ["text", "image"] if preset == "review" else ["text"],
            "contextWindow": args.expected_context_window,
            "maxTokens": max_tokens[preset],
        })
    return {
        "provider_present": True,
        "mode": "merge",
        "provider": {
            "id": "anvil",
            "baseUrl": args.router_base_url,
            "api": "openai-completions",
            "api_key_shape": "env-ref",
            "models": models,
        },
        "agents": {
            "default_model_refs": ["anvil/" + preset for preset in PRESETS],
        },
        "plugin_entry": {
            "id": PLUGIN_ID,
            "hooks": {"allowConversationAccess": True, "allowPromptInjection": None},
            "config": {"routeEndpoint": args.router_base_url.rstrip("/") + "/route", "routeAuthEnv": "ANVIL_ROUTER_TOKEN"},
        },
    }


def fixture_router_probes(args: argparse.Namespace) -> dict[str, Any]:
    routes = []
    for preset in PRESETS:
        provider = "fast-local" if preset == "chat-fast" else "heavy-local"
        routes.append({
            "intent": preset,
            "status": 200,
            "latency_ms": 18.0,
            "response": {
                "tier": "local",
                "provider": provider,
                "model": "qwen-fast" if provider == "fast-local" else "gpt-oss-120b",
                "work_class": preset,
                "reason": "fixture route decision for preset='%s'" % preset,
                "confidence": 1.0,
                "session_id": "rte_fixture_%s" % preset.replace("-", "_"),
            },
        })
    return {
        "source": "fixture",
        "base_url": args.router_base_url,
        "unauthenticated_models": {"status": 401, "response": {"error": {"type": "authentication_error"}}},
        "authenticated_models": {
            "status": 200,
            "response": {"data": [{"id": preset, "object": "model"} for preset in PRESETS]},
        },
        "routes": routes,
    }


def fixture_e2e_turns() -> list[dict[str, Any]]:
    outputs = {
        "quick_edit_python": "def inc(x):\n    return x + 1",
        "json_contract": "{\"status\":\"ok\",\"items\":[1,2,3]}",
        "tool_call_shape": "{\"tool\":\"route_decision\",\"arguments\":{\"model\":\"chat\"}}",
        "review_bug": "The function can raise a division by zero error when b is 0.",
        "patch_edit": "--- a/gate.py\n+++ b/gate.py\n@@\n-return false\n+return true",
        "long_context_needle": "ANVIL_NEEDLE_COLO_7319",
    }
    turns = []
    for index, case in enumerate(CAPABILITY_CASES, 1):
        text = outputs[case["id"]]
        output_tokens = max(1, len(text.split()))
        latency_ms = 90 + index * 11
        failures = check_output(text, case["checks"])
        turns.append({
            "case_id": case["id"],
            "intent": case["intent"],
            "status": "pass" if not failures else "fail",
            "latency_ms": latency_ms,
            "ttft_ms": 24 + index,
            "output_tokens": output_tokens,
            "tokens_per_second": round(output_tokens / (latency_ms / 1000.0), 2),
            "checks": [{"status": "pass", **check} for check in case["checks"]],
            "failures": failures,
            "response_preview": text[:300],
        })
    return turns


def build_repair_plan(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        "anvil-serving",
        "harness",
        "sync",
        "openclaw",
        "--config",
        args.config,
        "--base-url",
        args.router_base_url,
        "--gateway-host",
        args.gateway_host,
        "--restart",
    ]
    if args.gateway_user:
        command.extend(["--gateway-user", args.gateway_user])
    return {
        "requested": bool(args.repair),
        "human_gate_required": True,
        "applied": False,
        "preview_command": command if args.repair else [],
        "note": (
            "Repair mode records the product command to run; it does not apply or restart from this smoke runner."
            if args.repair else
            "Pass --repair to include a human-gated harness sync preview."
        ),
    }


def router_probe_issues(router_probes: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    unauth = router_probes.get("unauthenticated_models") if isinstance(router_probes.get("unauthenticated_models"), dict) else {}
    if unauth.get("status") != 401:
        issues.append(issue(
            "fail",
            "router_auth_not_enforced",
            "Unauthenticated /v1/models did not return 401.",
            {"status": unauth.get("status")},
        ))
    auth_models = router_probes.get("authenticated_models")
    if not isinstance(auth_models, dict) or auth_models.get("status") not in (200, "skipped"):
        issues.append(issue("fail", "models_probe_failed", "Authenticated /v1/models did not succeed."))
    if isinstance(auth_models, dict) and auth_models.get("status") == "skipped":
        issues.append(issue("warn", "models_probe_skipped", "Authenticated /v1/models was skipped.", auth_models))

    route_rows = router_probes.get("routes") if isinstance(router_probes.get("routes"), list) else []
    by_intent = {row.get("intent"): row for row in route_rows if isinstance(row, dict)}
    missing = sorted(set(PRESETS) - set(by_intent))
    if missing:
        issues.append(issue("warn", "missing_route_probes", "No /v1/route probe was recorded for some presets.", {"missing": missing}))
    for preset, row in sorted(by_intent.items()):
        status = row.get("status")
        if status != 200:
            issues.append(issue(
                "fail",
                "route_probe_failed",
                "A /v1/route probe did not return 200.",
                {"intent": preset, "status": status},
            ))
            continue
        response = row.get("response") if isinstance(row.get("response"), dict) else {}
        if response.get("tier") not in ("local", "cloud"):
            issues.append(issue(
                "fail",
                "bad_route_shape",
                "A /v1/route probe did not report a local/cloud tier.",
                {"intent": preset, "response": response},
            ))
    return issues


def e2e_issues(turns: list[dict[str, Any]], *, live_requested: bool) -> list[dict[str, Any]]:
    if not turns:
        severity = "warn" if live_requested else "fail"
        return [issue(severity, "generation_not_run", "No deterministic generation cases were recorded.")]
    issues: list[dict[str, Any]] = []
    if all(turn.get("status") == "skipped" for turn in turns):
        return [issue("warn", "generation_not_run", "Live generation probes were not requested.")]
    by_case = {turn.get("case_id"): turn for turn in turns}
    missing = sorted(case["id"] for case in CAPABILITY_CASES if case["id"] not in by_case and not str(next((t.get("case_id") for t in turns), "")).startswith("live_"))
    if missing:
        issues.append(issue("warn", "missing_capability_cases", "Some deterministic capability cases were not recorded.", {"missing": missing}))
    for turn in turns:
        if turn.get("status") == "skipped":
            issues.append(issue("warn", "generation_case_skipped", "A deterministic generation case was skipped.", {"case_id": turn.get("case_id")}))
            continue
        if turn.get("status") not in ("pass", 200):
            issues.append(issue("fail", "generation_case_failed", "A deterministic generation case failed.", {"case_id": turn.get("case_id"), "status": turn.get("status")}))
            continue
        if str(turn.get("case_id") or "").startswith("live_"):
            if turn.get("transport") != "gateway-direct-router":
                issues.append(issue(
                    "warn",
                    "generation_transport_unlabeled",
                    "A live generation case did not label its transport.",
                    {"case_id": turn.get("case_id")},
                ))
            if turn.get("finish_reason") == "length":
                issues.append(issue(
                    "fail",
                    "generation_truncated",
                    "A live generation case exhausted its completion budget.",
                    {"case_id": turn.get("case_id"), "max_tokens": turn.get("max_tokens")},
                ))
            if "anvil-colo-ok" not in str(turn.get("response_preview") or ""):
                issues.append(issue(
                    "fail",
                    "generation_sentinel_missing",
                    "A live generation case did not return the expected deterministic sentinel.",
                    {"case_id": turn.get("case_id")},
                ))
    return issues


def cloud_usage_summary(router_probes: dict[str, Any]) -> dict[str, Any]:
    routes = router_probes.get("routes") if isinstance(router_probes.get("routes"), list) else []
    path_counts = {"anvil_local": 0, "anvil_cloud": 0, "native_openclaw": 0, "unknown": 0}
    observed = []
    for row in routes:
        response = row.get("response") if isinstance(row, dict) and isinstance(row.get("response"), dict) else {}
        tier = response.get("tier")
        provider = str(response.get("provider") or "")
        if tier == "local":
            path = "anvil_local"
        elif tier == "cloud" or provider == "cloud":
            path = "anvil_cloud"
        else:
            path = "unknown"
        path_counts[path] += 1
        observed.append({"intent": row.get("intent"), "path": path, "tier": tier, "provider": provider})
    return {
        "auto_cloud_enabled_by_runner": False,
        "measurement_scope": "router route probes only; native OpenClaw provider dispatch is not exercised",
        "path_counts": path_counts,
        "observed_paths": observed,
    }


def build_proofs(artifact: dict[str, Any], *, args: argparse.Namespace) -> list[dict[str, Any]]:
    config_issues = artifact.get("drift", {}).get("openclaw_config_issues", [])
    plugin_issues = artifact.get("drift", {}).get("plugin_runtime_issues", [])
    route_issues = artifact.get("drift", {}).get("router_probe_issues", [])
    e2e = artifact.get("e2e_turns", [])
    e2e_problem_list = e2e_issues(e2e, live_requested=bool(args.live))
    benchmark_warnings = artifact.get("benchmarks", {}).get("warnings", [])
    interaction_warnings = artifact.get("interaction_benchmarks", {}).get("warnings", [])
    generation_metrics = [
        turn for turn in e2e
        if turn.get("status") in ("pass", 200)
    ]
    interaction_completed = (
        artifact.get("interaction_benchmarks", {})
        .get("aggregate", {})
        .get("completed", 0)
    )
    context_issues = [item for item in config_issues if item.get("code") == "context_window_drift"]
    drift_issues = artifact.get("drift", {}).get("issues", [])
    cloud_summary = artifact.get("cloud_usage", {})

    proofs = [
        proof(
            "P001",
            "OpenClaw provider catalog and allowlist",
            ["S001", "S004"],
            status_from_issues([i for i in config_issues if i.get("severity") == "fail" or i.get("code") in ("literal_api_key", "plugin_not_authoritative")]),
            {"config_issues": config_issues, "model_count": len(artifact.get("openclaw_config", {}).get("provider", {}).get("models", []))},
        ),
        proof(
            "P002",
            "Intent plugin installation and hook gate",
            ["S005"],
            status_from_issues(plugin_issues),
            {"plugin_runtime_issues": plugin_issues, "plugin_runtime": artifact.get("plugin_runtime", {})},
        ),
        proof(
            "P003",
            "Router auth, discovery, and route decisions",
            ["S003", "S006", "S009"],
            status_from_issues(route_issues),
            {"router_probe_issues": route_issues, "route_count": len(artifact.get("router_probes", {}).get("routes", []))},
        ),
        proof(
            "P004",
            "Context-window drift guard",
            ["S004"],
            "fail" if context_issues else "pass",
            {"expected_context_window": args.expected_context_window, "issues": context_issues},
        ),
        proof(
            "P005",
            "Deterministic capability cases",
            ["S002", "S003", "S004", "S006"],
            status_from_issues(e2e_problem_list),
            {"case_count": len(e2e), "issues": e2e_problem_list},
        ),
        proof(
            "P006",
            "Latency and token generation evidence",
            ["S007"],
            "skipped" if not generation_metrics and not interaction_completed else status_from_issues([*benchmark_warnings, *interaction_warnings]),
            {
                "benchmarks": artifact.get("benchmarks", {}),
                "interaction_benchmarks": artifact.get("interaction_benchmarks", {}),
            },
        ),
        proof(
            "P007",
            "Drift detection and repair gate",
            ["S008"],
            status_from_issues(drift_issues),
            {"drift_issue_count": len(drift_issues), "repair": artifact.get("repair", {})},
        ),
        proof(
            "P008",
            "Anvil cloud path explicitness",
            ["S009"],
            "pass" if cloud_summary.get("auto_cloud_enabled_by_runner") is False else "fail",
            cloud_summary,
        ),
    ]
    return proofs


def base_environment(args: argparse.Namespace, mode: str) -> dict[str, Any]:
    budgets = load_generation_probe_budgets(
        args.config,
        fast_default=args.fast_generation_max_tokens,
        heavy_default=args.heavy_generation_max_tokens,
    )
    interaction_recipes = load_interaction_benchmark_recipes(args.config)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "generated_at": utc_now(),
        "host": platform.node(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cwd": str(Path.cwd()),
        "gateway_host": args.gateway_host,
        "router_base_url": args.router_base_url,
        "config": args.config,
        "generation_probe_max_tokens": budgets,
        "interaction_benchmark_recipe": interaction_recipes,
    }


def assemble_artifact(args: argparse.Namespace, *, live_payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    mode = "live" if args.live else "fixture"
    if live_payload:
        openclaw_config = live_payload.get("openclaw_config") or {}
        plugin_runtime = live_payload.get("plugin_runtime") or {}
        router_probes = live_payload.get("router_probes") or {}
        e2e_turns = live_payload.get("e2e_turns") or []
        interaction_turns = live_payload.get("interaction_benchmark_turns") or []
        live_collection = {
            "ok": live_payload.get("ok"),
            "gateway": live_payload.get("gateway"),
            "command": live_payload.get("command"),
            "error": live_payload.get("error"),
            "message": live_payload.get("message"),
        }
    else:
        openclaw_config = fixture_openclaw_config(args)
        plugin_runtime = {
            "status": "loaded",
            "activated": True,
            "hookCount": 1,
            "hookTypes": ["before_model_resolve"],
            "runtime": "fixture",
        }
        router_probes = fixture_router_probes(args)
        e2e_turns = fixture_e2e_turns()
        interaction_turns = []
        live_collection = None

    if not e2e_turns and args.live and not args.run_generations:
        e2e_turns = [{
            "case_id": "live_generation_skipped",
            "intent": None,
            "status": "skipped",
            "reason": "live mode did not pass --run-generations",
        }]

    config_issues = audit_openclaw_config(
        openclaw_config,
        expected_base_url=args.router_base_url,
        expected_context_window=args.expected_context_window,
    )
    plugin_issues = audit_plugin_runtime(plugin_runtime)
    route_issues = router_probe_issues(router_probes)
    benchmark = summarize_benchmarks(
        [turn for turn in e2e_turns if turn.get("status") != "skipped"],
        args.expect_min_tokens_per_second,
    )
    interaction_benchmark = summarize_interaction_benchmarks(
        interaction_turns,
        requested=bool(args.run_interaction_benchmark),
    )
    drift_issues = [*config_issues, *plugin_issues, *route_issues]
    if live_payload and live_payload.get("ok") is False:
        drift_issues.append(issue("fail", "live_collection_failed", "Live gateway collection failed.", live_payload))

    artifact = redact({
        "schema_version": SCHEMA_VERSION,
        "environment": base_environment(args, mode),
        "stories": STORIES,
        "capability_cases": CAPABILITY_CASES,
        "openclaw_config": openclaw_config,
        "plugin_runtime": plugin_runtime,
        "router_probes": router_probes,
        "e2e_turns": e2e_turns,
        "benchmarks": benchmark,
        "interaction_benchmarks": interaction_benchmark,
        "cloud_usage": cloud_usage_summary(router_probes),
        "drift": {
            "issues": drift_issues,
            "openclaw_config_issues": config_issues,
            "plugin_runtime_issues": plugin_issues,
            "router_probe_issues": route_issues,
            "needs_repair": any(item.get("severity") in ("warn", "fail") for item in drift_issues),
        },
        "repair": build_repair_plan(args),
        "live_collection": live_collection,
    })
    artifact["proofs"] = build_proofs(artifact, args=args)
    artifact["verdict"] = verdict_from_proofs(artifact["proofs"])
    artifact = redact(artifact)
    artifact["verdict"] = verdict_from_proofs(artifact["proofs"])
    return artifact


def write_artifact(path: Path, artifact: dict[str, Any], *, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"indent": 2, "sort_keys": True} if pretty else {"separators": (",", ":"), "sort_keys": True}
    path.write_text(json.dumps(artifact, ensure_ascii=False, **kwargs) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a story-driven COLO smoke/eval for OpenClaw on Fakoli Mini talking to "
            "an anvil-serving router. Defaults to deterministic fixture mode."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fixture", action="store_true", help="run without live OpenClaw, SSH, router token, Docker, or model serve (default)")
    mode.add_argument("--live", action="store_true", help="collect live gateway/router diagnostics over SSH")
    parser.add_argument("--artifact", default=DEFAULT_ARTIFACT, help="JSON evidence artifact path")
    parser.add_argument("--pretty", action="store_true", help="write indented JSON")
    parser.add_argument("--gateway-host", default=DEFAULT_GATEWAY_HOST, help="OpenClaw gateway SSH host")
    parser.add_argument("--gateway-user", default="", help="optional SSH user for the gateway host")
    parser.add_argument("--router-base-url", default=DEFAULT_ROUTER_BASE_URL, help="expected router base URL ending in /v1")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="router config path used in repair preview")
    parser.add_argument("--expected-context-window", type=int, default=DEFAULT_EXPECTED_CONTEXT_WINDOW, help="minimum contextWindow expected for every preset")
    parser.add_argument("--timeout-seconds", type=int, default=30, help="SSH and probe timeout")
    parser.add_argument("--run-generations", action="store_true", help="in live mode, run bounded chat/completions probes from the gateway")
    parser.add_argument("--run-interaction-benchmark", action="store_true", help="in live mode, run repeatable intent benchmark cases using recipe dimensions from router config")
    parser.add_argument("--fast-generation-max-tokens", type=int, default=DEFAULT_FAST_GENERATION_MAX_TOKENS, help="max_tokens for fast live generation probes")
    parser.add_argument("--heavy-generation-max-tokens", type=int, default=DEFAULT_HEAVY_GENERATION_MAX_TOKENS, help="max_tokens for heavy live generation probes")
    parser.add_argument("--expect-min-tokens-per-second", type=float, default=0.0, help="optional warning threshold for aggregate generation throughput")
    parser.add_argument("--repair", action="store_true", help="include a human-gated harness sync preview command; never applies automatically")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if not args.live:
        args.fixture = True
    if args.run_generations and not args.live:
        parser.error("--run-generations requires --live")
    if args.run_interaction_benchmark and not args.live:
        parser.error("--run-interaction-benchmark requires --live")
    if args.fast_generation_max_tokens <= 0:
        parser.error("--fast-generation-max-tokens must be positive")
    if args.heavy_generation_max_tokens <= 0:
        parser.error("--heavy-generation-max-tokens must be positive")
    if "localhost" in args.router_base_url.lower():
        parser.error("use 127.0.0.1 or a private/tailnet host, not localhost")

    live_payload = None
    if args.live:
        live_payload = run_gateway_collector(args)
    artifact = assemble_artifact(args, live_payload=live_payload)
    write_artifact(Path(args.artifact), artifact, pretty=args.pretty)
    print("wrote %s" % args.artifact)
    print("verdict: %s" % artifact["verdict"]["status"])
    return 0 if artifact["verdict"]["status"] in ("pass", "warn") else 1


if __name__ == "__main__":
    raise SystemExit(main())
