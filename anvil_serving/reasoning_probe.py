"""One isolated, pinned reasoning-boundary diagnostic; no promotion authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import threading
import time
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .router.backends.relay import (
    RelayBackend, _close_controlled_response, _controlled_stream_open, _interrupt_response,
)
from .router.config import DIALECT_OPENAI, PRIVACY_LOCAL, load_bytes
from .router.dialects.openai import OpenAIDialect
from .router.request_control import RequestControl

_MAX_BYTES = 1024 * 1024
_REQUEST_SECONDS = 120
_WINDOW_SECONDS = 300
_CLEANUP_SECONDS = 60
_CHECK_SECONDS = 5
_CHECK_BYTES = 256 * 1024
_LIMITS = {"concurrency": 1, "maximum_requests": 2, "request_timeout_s": 120,
           "total_window_s": 300, "retries": 0}
_SETTINGS = {"thinking_token_budget": 64, "reasoning_effort": "max",
             "temperature": 0.6, "top_p": 0.95}
_CONTROLS = {**_SETTINGS, "include_reasoning": True, "return_token_ids": True}
_COMMON = {"model": "llm.primary", "max_tokens": 512, "reasoning_effort": "max",
           "temperature": 0.6, "top_p": 0.95, "stream": False}
_RUNTIME = {
    "engine_revision": "8244d1c44348ae8a5bbfc9f1b607021652283ed4",
    "build_revision": "6a833b698f0b41a9c12922343553eee419b9f465",
    "image_digest": "sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29",
    "model_revision": "76348ddb2be2e35e39ecfc00e75dc07703f4f3d5",
    "template_sha256": "b5bbea09a508f00213386271fae738fb6373bf396e0cc19baaa9675a10952bb0",
    "reasoning_parser": "glm45", "model_runner": "V2", "speculative_tokens": 3,
}
_PATCH_COMMIT = "0a1276e5e3d9000488e7590cc0957edd709f2152"
_PATCH_BASE_SOURCE = "0b5fc337058e22bc28a8530ccec904d6ccd89b9548b02955c657ac429b1669d2"
_PATCH_SOURCE = "2b5305c406d8495e515f048ce0322c7ae5946a49e5f27bd5bbbf30a1e1fea0e6"


def _validate_runtime_patch(plan: Mapping[str, Any]) -> dict[str, str] | None:
    patch = plan.get("runtime_patch")
    if patch is None:
        return None
    if not isinstance(patch, dict) or set(patch) != {
            "base_image_digest", "base_source_sha256", "upstream_commit", "source_sha256"}:
        raise ValueError("unsupported runtime patch declaration")
    if (patch["base_image_digest"] != _RUNTIME["image_digest"]
            or patch["base_source_sha256"] != _PATCH_BASE_SOURCE
            or patch["upstream_commit"] != _PATCH_COMMIT
            or patch["source_sha256"] != _PATCH_SOURCE):
        raise ValueError("runtime patch differs from pinned upstream fix")
    image = plan["runtime"].get("image_digest")
    if (not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image)
            or image == _RUNTIME["image_digest"]):
        raise ValueError("runtime patch requires a distinct immutable candidate image")
    return patch
_CASES = [
    {"id": "boundary", "prompt": "Solve SEND + MORE = MONEY in decimal. Different letters are different digits and leading digits cannot be zero. Return only the three integers SEND, MORE, MONEY separated by commas.", "expected_integers": [9567, 1085, 10652]},
    {"id": "natural_end_fallback", "condition": "Only if boundary case naturally ends reasoning below 64 tokens with coherent output and enforcement remains untested.", "prompt": "Solve CROSS + ROADS = DANGER in decimal. Different letters are different digits and leading digits cannot be zero. Return only the three integers CROSS, ROADS, DANGER separated by commas.", "expected_integers": [96233, 62513, 158746]},
]
# Tier extensions remain intact, but cannot replace the pinned request contract.
_FORBIDDEN_EXTRAS = {
    "model", "messages", "max_tokens", "max_completion_tokens", "stream", "n",
    "stop", "stop_sequences", "tools", "tool_choice", "functions", "function_call",
    "response_format", "logit_bias", "allowed_token_ids", "bad_words",
    "ignore_eos", "min_tokens", "min_new_tokens", "max_new_tokens",
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("input must be a regular file")
        data = source.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise ValueError("input exceeds size limit")
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json(data: bytes) -> dict[str, Any]:
    value = json.loads(data, object_pairs_hook=_unique_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _same(actual: Any, expected: Any) -> bool:
    """JSON equality that does not equate booleans with integers."""
    return json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def _ids(value: object, name: str, *, allow_empty: bool = False) -> list[int]:
    if (not isinstance(value, list) or (not value and not allow_empty)
            or len(value) > _MAX_BYTES // 2
            or any(type(token) is not int or token < 0 for token in value)):
        raise ValueError(f"{name} must contain nonnegative integer token IDs")
    return value


def _loopback_endpoint(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username is not None or parsed.password is not None
            or parsed.path != "/v1" or parsed.query or parsed.fragment
            or parsed.port is None or not 1 <= parsed.port <= 65535
            or base_url != f"http://127.0.0.1:{parsed.port}/v1"):
        raise ValueError("probe requires a literal credential-free loopback endpoint")
    return f"http://127.0.0.1:{parsed.port}"


def _bounded_get(url: str, headers: Mapping[str, str], *, timeout_s=None, maximum_bytes=None) -> bytes:
    timeout_s = _CHECK_SECONDS if timeout_s is None else timeout_s
    maximum_bytes = _CHECK_BYTES if maximum_bytes is None else maximum_bytes
    control = RequestControl(admission_timeout_s=timeout_s,
        startup_timeout_s=timeout_s, idle_timeout_s=timeout_s,
        total_timeout_s=timeout_s)
    timer = threading.Timer(timeout_s, control.cancel)
    timer.daemon = True
    response = None
    timer.start()
    try:
        control.start_upstream()
        response = _controlled_stream_open(url, data=None, headers=headers,
            timeout=timeout_s, control=control, method="GET")
        control.set_upstream_close(lambda: _interrupt_response(response))
        raw = response.read(maximum_bytes + 1)
        control.check()
        if len(raw) > maximum_bytes:
            raise ValueError("owner check exceeds size limit")
        return raw
    finally:
        timer.cancel()
        timer.join()
        control.cancel()
        if response is not None:
            _close_controlled_response(response)


def _owner_state(tier) -> dict[str, Any]:
    origin = _loopback_endpoint(tier.base_url)
    key = (os.environ.get(tier.auth_env) or "").strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    models = _json(_bounded_get(origin + "/v1/models", headers)).get("data")
    identity_matches = (isinstance(models, list) and len(models) == 1
                        and isinstance(models[0], dict) and models[0].get("id") == tier.model)
    occupancy = {"running": None, "waiting": None}
    if identity_matches:
        metrics = _bounded_get(origin + "/metrics", headers).decode("utf-8")
        for name in occupancy:
            values = []
            for line in metrics.splitlines():
                match = re.fullmatch(r"vllm:num_requests_" + name + r"(?:\{[^\n]*\})?\s+(\S+)(?:\s+\d+)?", line)
                if match:
                    value = float(match[1])
                    if not math.isfinite(value) or value < 0 or value > 1_000_000_000 or not value.is_integer():
                        raise ValueError("invalid owner occupancy")
                    values.append(int(value))
            if values:
                occupancy[name] = sum(values)
    return {"checked_at": _timestamp(), "identity_matches": identity_matches,
            **occupancy, "idle": identity_matches and all(value == 0 for value in occupancy.values()),
            "scope": "point_in_time_owner_observation_not_admission_lease"}


def _validate(plan: Mapping[str, Any], boundary: Mapping[str, Any]) -> None:
    if plan.get("schema") != "anvil-serving.reasoning-limit-probe-plan/v1":
        raise ValueError("unsupported plan schema")
    allowed = {"schema", "status", "execution_authorized", "live_identity_verified", "ignis",
               "runtime", "limits", "isolated_tier_extra_body", "request_common", "cases",
               "required_evidence", "stop_conditions", "execution_prerequisites", "runtime_patch"}
    if set(plan) - allowed:
        raise ValueError("unsupported plan fields")
    patch = _validate_runtime_patch(plan)
    expected_runtime = {**_RUNTIME, "image_digest": plan["runtime"]["image_digest"]} if patch else _RUNTIME
    for key, expected in (("runtime", expected_runtime), ("limits", _LIMITS),
                          ("isolated_tier_extra_body", _SETTINGS),
                          ("request_common", _COMMON), ("cases", _CASES)):
        if not _same(plan.get(key), expected):
            raise ValueError(f"plan {key} differs from pinned probe")
    if plan.get("ignis") != "hold":
        raise ValueError("probe requires hold")
    if any(type(plan.get(key)) is not bool for key in ("execution_authorized", "live_identity_verified")):
        raise ValueError("plan authority metadata must be boolean")
    if set(boundary) != {"router_config_sha256", "expected_model", "start", "end", "forced_end"}:
        raise ValueError("unsupported boundary fields")
    if not isinstance(boundary["expected_model"], str) or not boundary["expected_model"].strip():
        raise ValueError("expected model is required")
    if not isinstance(boundary["router_config_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", boundary["router_config_sha256"]):
        raise ValueError("invalid router hash")
    start, end, forced = (_ids(boundary[key], key) for key in ("start", "end", "forced_end"))
    if len(start) > 32 or len(end) > 32 or end != forced or start == end:
        raise ValueError("unsupported boundary markers")
    if any(start[index:index + len(end)] == end for index in range(len(start))):
        raise ValueError("overlapping boundary markers")
    if any(end[index:index + len(start)] == start for index in range(len(end))):
        raise ValueError("overlapping boundary markers")


def _boundary(prompt: list[int], output: list[int], markers: Mapping[str, Any]) -> str:
    start, end = markers["start"], markers["end"]
    sequence = prompt + output
    starts = [index for index in range(len(sequence)) if sequence[index:index + len(start)] == start]
    ends = [index for index in range(len(sequence)) if sequence[index:index + len(end)] == end]
    if not starts or not ends:
        return "unknown"
    open_index = None
    latest_pair = None
    for index, kind in sorted([(i, "start") for i in starts] + [(i, "end") for i in ends]):
        if kind == "start":
            if open_index is not None or (latest_pair and index >= len(prompt)):
                return "invalid_repeated_or_nested_boundary"
            open_index = index
        else:
            if open_index is None:
                return "invalid_repeated_or_missing_close"
            latest_pair = (open_index, index)
            open_index = None
    if open_index is not None or latest_pair is None or latest_pair[1] < len(prompt):
        return "unknown"
    count = latest_pair[1] - latest_pair[0] - len(start)
    if count < 0:
        return "invalid_overlapping_boundary"
    if count < 64:
        return "natural_below_budget"
    if count == 64:
        return "boundary_consistent_not_forced_provenance"
    return "invalid_after_budget"


class _Artifacts:
    """Exclusive files under a pinned directory descriptor; never follow links."""

    def __init__(self, output: Path):
        output = output.absolute()
        descriptor = os.open(output.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in output.parts[1:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            os.mkdir(output.name, mode=0o700, dir_fd=descriptor)
            self.descriptor = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.fchmod(self.descriptor, 0o700)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def write(self, name: str, data: bytes) -> dict[str, Any]:
        if len(data) > _MAX_BYTES:
            raise ValueError("artifact exceeds size limit")
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.descriptor)
        with os.fdopen(descriptor, "wb") as target:
            os.fchmod(target.fileno(), 0o600)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.fsync(self.descriptor)
        return {"file": name, "sha256": _digest(data), "bytes": len(data)}

    def record(self, name: str, value: Any) -> dict[str, Any]:
        return self.write(name, json.dumps(value, indent=2, allow_nan=False).encode("utf-8"))

    def close(self) -> None:
        os.close(self.descriptor)


class _CaptureRelay(RelayBackend):
    def __init__(self, tier, *, expected_body, artifacts, prefix):
        super().__init__(tier, timeout=_REQUEST_SECONDS, max_response_bytes=_MAX_BYTES)
        self.expected_body = expected_body
        self.artifacts = artifacts
        self.prefix = prefix
        self.request_artifact = None
        self.response_artifact = None
        self.response = None

    def _build_body(self, request):
        body = super()._build_body(request)
        # This serialization exactly matches RelayBackend._generate_buffered.
        raw = json.dumps(body).encode("utf-8")
        self.request_artifact = self.artifacts.write(f"{self.prefix}-request.json", raw)
        if not _same(body, self.expected_body):
            raise ValueError("effective upstream request differs from pinned request")
        return body

    def _extract_structured(self, raw):
        self.response = raw[:_MAX_BYTES]
        self.response_artifact = self.artifacts.write(f"{self.prefix}-response.json", self.response)
        return super()._extract_structured(raw)


def _usage(raw: Any, prompt: Any, generated: Any, anomalies: list[str]) -> Any:
    """Project native counts only; opaque provider fields stay in the raw artifact."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        anomalies.append("invalid_usage")
        return None
    counts = {"prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"}
    details = {"prompt_tokens_details": {"cached_tokens", "created_cache_tokens", "multimodal_tokens"},
               "completion_tokens_details": {"reasoning_tokens"}}
    if set(raw) - counts - set(details):
        anomalies.append("unexpected_usage_fields")

    def count(value):
        if value is None:
            return None
        if type(value) is not int or not 0 <= value <= 1_000_000_000:
            anomalies.append("invalid_usage_count")
            return None
        return value

    projected = {name: count(raw[name]) for name in counts if name in raw}
    for name, allowed in details.items():
        if name not in raw:
            continue
        value = raw[name]
        projected[name] = None
        if value is None:
            continue
        if not isinstance(value, dict):
            anomalies.append("invalid_usage_details")
            continue
        if set(value) - allowed:
            anomalies.append("unexpected_usage_fields")
        projected[name] = {key: count(value[key]) for key in allowed - {"multimodal_tokens"} if key in value}
        if "multimodal_tokens" in allowed and "multimodal_tokens" in value:
            # This pinned diagnostic is text-only; modality maps are unexpected.
            projected[name]["multimodal_tokens"] = None
            if value["multimodal_tokens"] is not None:
                anomalies.append("unexpected_multimodal_usage")
    prompt_count, completion_count, total_count = (projected.get(name) for name in
        ("prompt_tokens", "completion_tokens", "total_tokens"))
    if completion_count is not None and (completion_count > 512 or
            (isinstance(generated, list) and completion_count != len(generated))):
        anomalies.append("completion_token_count_mismatch")
    if prompt_count is not None and isinstance(prompt, list) and prompt_count != len(prompt):
        anomalies.append("prompt_token_count_mismatch")
    if all(value is not None for value in (prompt_count, completion_count, total_count)) and total_count != prompt_count + completion_count:
        anomalies.append("total_token_count_mismatch")
    reasoning = [projected.get("reasoning_tokens"),
                 (projected.get("completion_tokens_details") or {}).get("reasoning_tokens")]
    completion_bound = completion_count if completion_count is not None else (
        len(generated) if isinstance(generated, list) else 512)
    if (any(value is not None and value > completion_bound for value in reasoning)
            or all(value is not None for value in reasoning) and reasoning[0] != reasoning[1]):
        anomalies.append("reasoning_token_count_mismatch")
    prompt_bound = prompt_count if prompt_count is not None else (
        len(prompt) if isinstance(prompt, list) else None)
    for name in ("cached_tokens", "created_cache_tokens"):
        cached = (projected.get("prompt_tokens_details") or {}).get(name)
        if cached is not None and prompt_bound is not None and cached > prompt_bound:
            anomalies.append("cached_token_count_mismatch")
    return projected


def _analyze(raw: bytes, case: Mapping[str, Any], boundary: Mapping[str, Any]) -> dict[str, Any]:
    response = _json(raw)
    anomalies = []
    if response.get("model") != boundary["expected_model"]:
        anomalies.append("response_model_mismatch")
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ValueError("response must have exactly one choice")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("response message must be an object")
    if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
        anomalies.append("unexpected_response_payload")
    finish = choice.get("finish_reason")
    if not isinstance(finish, str) or finish not in {"stop", "length", "tool_calls", "function_call", "content_filter"}:
        finish = "unknown"
    if finish != "stop":
        anomalies.append("finish_reason_not_stop")
    answer = message.get("content")
    match = re.fullmatch(r"\s*([1-9][0-9]*)\s*,\s*([1-9][0-9]*)\s*,\s*([1-9][0-9]*)\s*", answer) if isinstance(answer, str) else None
    oracle = bool(match and [int(value) for value in match.groups()] == case["expected_integers"])
    classification = "unknown"
    prompt, generated = response.get("prompt_token_ids"), choice.get("token_ids")
    if prompt is not None and generated is not None:
        try:
            prompt = _ids(prompt, "prompt_token_ids", allow_empty=True)
            generated = _ids(generated, "token_ids", allow_empty=True)
            if len(generated) > 512:
                anomalies.append("output_token_limit_exceeded")
            classification = _boundary(prompt, generated, boundary)
        except ValueError:
            anomalies.append("invalid_token_ids")
    usage = _usage(response.get("usage"), prompt, generated, anomalies)
    span_tokens = generated_reasoning_tokens = None
    if classification in {"natural_below_budget", "boundary_consistent_not_forced_provenance"}:
        sequence = prompt + generated
        start, end = boundary["start"], boundary["end"]
        opening = max(index for index in range(len(sequence)) if sequence[index:index + len(start)] == start)
        closing = next(index for index in range(opening + len(start), len(sequence))
                       if sequence[index:index + len(end)] == end)
        span_tokens = closing - opening - len(start)
        generated_reasoning_tokens = closing - max(len(prompt), opening + len(start))
        if usage is not None:
            reported = [usage.get("reasoning_tokens"),
                        (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")]
            if any(value is not None and value != generated_reasoning_tokens for value in reported):
                anomalies.append("reasoning_marker_count_mismatch")
    return {"finish_reason": finish, "usage": usage,
            "boundary": classification, "reasoning_span_tokens": span_tokens,
            "generated_reasoning_token_ids_count": generated_reasoning_tokens,
            "oracle": oracle, "response_anomalies": sorted(set(anomalies))}


def run(router_config: Path, plan_path: Path, boundary_path: Path, output: Path, confirm: bool) -> Mapping[str, Any]:
    started = time.monotonic()
    plan_bytes, boundary_bytes, config_bytes = (_read_bytes(path) for path in (plan_path, boundary_path, router_config))
    plan, boundary = _json(plan_bytes), _json(boundary_bytes)
    _validate(plan, boundary)
    config_hash = _digest(config_bytes)
    if config_hash != boundary["router_config_sha256"]:
        raise ValueError("router config hash mismatch")
    config = load_bytes(config_bytes)
    tier_id = config.model_routes.get(_COMMON["model"])
    tier = next((candidate for candidate in config.tiers if candidate.id == tier_id), None)
    if (tier is None or tier.privacy != PRIVACY_LOCAL or tier.dialect != DIALECT_OPENAI
            or tier.replicas or not tier.base_url or tier.model != boundary["expected_model"]):
        raise ValueError("alias must select one direct local OpenAI tier with the expected model")
    _loopback_endpoint(tier.base_url)
    if tier.max_output_tokens is not None and tier.max_output_tokens < 512:
        raise ValueError("tier output ceiling is below the pinned probe")
    for extras in (tier.extra_body or {}, tier.extra_body_defaults or {}):
        if set(extras) & _FORBIDDEN_EXTRAS:
            raise ValueError("tier extras override the pinned request contract")
    isolated = replace(tier, extra_body={**(tier.extra_body or {}), **_CONTROLS})
    preview = {"status": "preview", "network": False, "tier": tier.id,
               "controls": _CONTROLS, "limits": _LIMITS, "runtime_verified": False,
               "runtime_identity_authority": "operator_attested" if plan["live_identity_verified"] else "unverified",
               "router_config_sha256": config_hash, "ignis": "hold"}
    if not confirm:
        return preview
    if not plan["execution_authorized"] or not plan["live_identity_verified"]:
        raise ValueError("execution requires an authorized operator-attested private packet")
    artifacts = _Artifacts(output)
    records = []
    try:
        artifacts.record("start.json", {**preview, "status": "started", "network": True, "started_at": _timestamp(),
                         "authorization": "explicit_confirm", "runtime": plan["runtime"],
                         "runtime_patch": plan.get("runtime_patch"),
                         "plan_sha256": _digest(plan_bytes), "boundary_sha256": _digest(boundary_bytes)})
        for index, case in enumerate(plan["cases"]):
            if time.monotonic() - started + 2 * _CHECK_SECONDS + _REQUEST_SECONDS + _CLEANUP_SECONDS > _WINDOW_SECONDS:
                break
            if records and not (records[-1].get("boundary") == "natural_below_budget"
                                and records[-1].get("oracle") is True
                                and records[-1].get("finish_reason") == "stop"
                                and records[-1].get("response_anomalies") == []):
                break
            prefix = str(index)
            record = {"case": case["id"], "started_at": _timestamp(), "started": True,
                      "owner_state": {"idle": None}}
            artifacts.record(f"{prefix}-start.json", record)
            relay = None
            timer = None
            control = None
            request_started = time.monotonic()
            try:
                if _digest(_read_bytes(router_config)) != config_hash:
                    raise ValueError("router configuration changed before dispatch")
                record["owner_state"] = _owner_state(tier)
                artifacts.record(f"{prefix}-owner.json", record["owner_state"])
                if not record["owner_state"]["idle"]:
                    raise ValueError("owner identity or idle state is unverified")
                if _digest(_read_bytes(router_config)) != config_hash:
                    raise ValueError("router configuration changed during owner checks")
                body = {**_COMMON, "messages": [{"role": "user", "content": case["prompt"]}]}
                expected = {**(tier.extra_body_defaults or {}), **body,
                            "model": tier.model, **isolated.extra_body}
                request = OpenAIDialect().parse_request(body)
                control = RequestControl(admission_timeout_s=_REQUEST_SECONDS,
                    startup_timeout_s=_REQUEST_SECONDS, idle_timeout_s=_REQUEST_SECONDS,
                    total_timeout_s=_REQUEST_SECONDS)
                request.raw["_anvil_control"] = control
                relay = _CaptureRelay(isolated, expected_body=expected, artifacts=artifacts, prefix=prefix)
                timer = threading.Timer(_REQUEST_SECONDS, control.cancel)
                timer.daemon = True
                timer.start()
                for _ in relay.generate(request):
                    pass
                control.check()
                record.update(_analyze(relay.response, case, boundary))
                record["terminal"] = "response"
            except Exception as error:
                record.update(terminal="error", error=type(error).__name__)
            finally:
                if timer is not None:
                    timer.cancel()
                    timer.join()
                if control is not None:
                    control.cancel()
                record.update(ended_at=_timestamp(), elapsed_seconds=time.monotonic() - request_started,
                              engine_drain="unknown")
                record["request_artifact"] = relay.request_artifact if relay is not None else None
                record["response_artifact"] = relay.response_artifact if relay is not None else None
                artifacts.record(f"{prefix}-terminal.json", record)
                records.append(record)
            if record["terminal"] == "error":
                break
        try:
            post_hash = _digest(_read_bytes(router_config))
        except Exception:
            post_hash = None
        changed = post_hash != config_hash
        failed = changed or not records or any(record["terminal"] == "error" for record in records)
        result = {"status": "error" if failed else "executed", "records": records,
                  "router_config_sha256_before": config_hash, "router_config_sha256_after": post_hash,
                  "shared_configuration_changed": changed, "engine_drain": "unknown",
                  "runtime_verified": False, "ignis": "hold", "promoted": False,
                  "runtime_identity_authority": "operator_attested",
                  "ended_at": _timestamp(), "elapsed_seconds": time.monotonic() - started}
        artifacts.record("result.json", result)
        return result
    finally:
        artifacts.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--router-config", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--boundary-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args.router_config, args.plan, args.boundary_config, args.output, args.confirm)
    except Exception as error:
        result = {"status": "error", "error": type(error).__name__}
    print(json.dumps(result, indent=2))
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
