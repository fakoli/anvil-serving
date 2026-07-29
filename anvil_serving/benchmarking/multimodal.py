"""Deterministic image/video corpus qualification for OpenAI-compatible serves."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..model_controls import validate_reasoning_control
from ..preflight import chat, resolve_api_key, response_observation
from .artifacts import atomic_write_json, path_is_within, real_path, validate_write_target

MANIFEST_SCHEMA = "multimodal-corpus/v1"
EVIDENCE_SCHEMA = "multimodal-benchmark-evidence/v1"
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 128 * 1024 * 1024
MAX_CASE_BYTES = 256 * 1024 * 1024
MAX_CASES = 256
MAX_REPETITIONS = 20
MAX_IMAGES = 4
MAX_VIDEOS = 1
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _read_json(path: str) -> tuple[dict[str, Any], bytes]:
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError("corpus must be a regular non-symlink JSON file")
    with open(path, "rb") as handle:
        raw = handle.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("corpus manifest exceeds 4 MiB")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("corpus is not valid UTF-8 JSON: %s" % exc) from None
    if not isinstance(value, dict):
        raise ValueError("corpus must be a JSON object")
    return value, raw


def _load_media(entry: Any, *, root: str, case_id: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("%s media entries must be objects" % case_id)
    relative = entry.get("path")
    expected_hash = entry.get("sha256")
    declared_mime = entry.get("mime")
    if (
        not isinstance(relative, str)
        or not relative
        or os.path.isabs(os.path.expanduser(relative))
    ):
        raise ValueError("%s media path must be a non-empty relative path" % case_id)
    if not isinstance(expected_hash, str) or not _HEX_64.fullmatch(expected_hash):
        raise ValueError("%s media sha256 must be 64 lowercase hex characters" % case_id)
    if declared_mime not in IMAGE_MIMES | VIDEO_MIMES:
        raise ValueError("%s media MIME is not allowlisted" % case_id)
    path = real_path(relative, base=root)
    if not path_is_within(path, root):
        raise ValueError("%s media path escapes the corpus directory" % case_id)
    if os.path.islink(os.path.abspath(os.path.join(root, relative))):
        raise ValueError("%s media path cannot be a symbolic link" % case_id)
    if not os.path.isfile(path):
        raise ValueError("%s media path is not a regular file: %s" % (case_id, relative))
    guessed, _encoding = mimetypes.guess_type(path)
    if guessed != declared_mime:
        raise ValueError(
            "%s media MIME mismatch: manifest=%s extension=%s"
            % (case_id, declared_mime, guessed)
        )
    limit = MAX_IMAGE_BYTES if declared_mime in IMAGE_MIMES else MAX_VIDEO_BYTES
    size = os.path.getsize(path)
    if not 0 < size <= limit:
        raise ValueError("%s media bytes are outside the allowed range" % case_id)
    with open(path, "rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) != size:
        raise ValueError("%s media changed while it was being read" % case_id)
    actual_hash = _sha256(raw)
    if actual_hash != expected_hash:
        raise ValueError(
            "%s media hash mismatch for %s: expected=%s actual=%s"
            % (case_id, relative, expected_hash, actual_hash)
        )
    kind = "image" if declared_mime in IMAGE_MIMES else "video"
    return {
        "path": relative.replace("\\", "/"),
        "kind": kind,
        "mime": declared_mime,
        "bytes": size,
        "sha256": actual_hash,
        "_data_url": "data:%s;base64,%s" % (
            declared_mime, base64.b64encode(raw).decode("ascii")
        ),
    }


def _validate_assertions(case_id: str, assertions: Any) -> list[dict[str, Any]]:
    if not isinstance(assertions, list) or not assertions:
        raise ValueError("%s assertions must be a non-empty array" % case_id)
    normalized = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            raise ValueError("%s assertions must be objects" % case_id)
        kind = assertion.get("type")
        if kind == "contains_casefold":
            value = assertion.get("value")
            if not isinstance(value, str) or not value:
                raise ValueError("%s contains assertion requires value" % case_id)
            normalized.append({"type": kind, "value": value})
        elif kind == "ordered_casefold":
            values = assertion.get("values")
            if (
                not isinstance(values, list)
                or len(values) < 2
                or any(not isinstance(value, str) or not value for value in values)
            ):
                raise ValueError("%s ordered assertion requires two or more values" % case_id)
            normalized.append({"type": kind, "values": list(values)})
        else:
            raise ValueError("%s assertion type is unsupported: %r" % (case_id, kind))
    return normalized


def load_corpus(path: str) -> dict[str, Any]:
    """Load, contain, hash, and normalize a ``multimodal-corpus/v1`` manifest."""
    manifest_path = real_path(path)
    value, raw = _read_json(manifest_path)
    if value.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("corpus schema must be %s" % MANIFEST_SCHEMA)
    cases = value.get("cases")
    if not isinstance(cases, list) or not 0 < len(cases) <= MAX_CASES:
        raise ValueError("corpus cases must contain 1 through %d entries" % MAX_CASES)
    root = os.path.dirname(manifest_path)
    seen = set()
    normalized_cases = []
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            raise ValueError("corpus cases must be objects")
        case_id = raw_case.get("id")
        modality = raw_case.get("modality")
        prompt = raw_case.get("prompt")
        repetitions = raw_case.get("repetitions")
        if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", case_id):
            raise ValueError("case id must be a lowercase stable identifier")
        if case_id in seen:
            raise ValueError("duplicate case id: %s" % case_id)
        seen.add(case_id)
        if modality not in {"image", "video", "mixed"}:
            raise ValueError("%s modality must be image, video, or mixed" % case_id)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8192:
            raise ValueError("%s prompt must be 1 through 8192 characters" % case_id)
        if (
            isinstance(repetitions, bool)
            or not isinstance(repetitions, int)
            or not 1 <= repetitions <= MAX_REPETITIONS
        ):
            raise ValueError(
                "%s repetitions must be from 1 through %d" % (case_id, MAX_REPETITIONS)
            )
        media_entries = raw_case.get("media")
        if not isinstance(media_entries, list) or not media_entries:
            raise ValueError("%s media must be a non-empty array" % case_id)
        media = [_load_media(item, root=root, case_id=case_id) for item in media_entries]
        images = sum(item["kind"] == "image" for item in media)
        videos = sum(item["kind"] == "video" for item in media)
        if images > MAX_IMAGES or videos > MAX_VIDEOS:
            raise ValueError("%s exceeds four images or one video" % case_id)
        if sum(item["bytes"] for item in media) > MAX_CASE_BYTES:
            raise ValueError("%s exceeds the 256 MiB case limit" % case_id)
        expected_modality = (
            "mixed" if images and videos
            else "image" if images
            else "video"
        )
        if modality != expected_modality:
            raise ValueError(
                "%s modality=%s does not match its media (%s)"
                % (case_id, modality, expected_modality)
            )
        sampling = raw_case.get("sampling")
        if videos:
            if not isinstance(sampling, dict) or set(sampling) not in ({"fps"}, {"num_frames"}):
                raise ValueError("%s video sampling must select exactly fps or num_frames" % case_id)
            key, sample_value = next(iter(sampling.items()))
            if (
                isinstance(sample_value, bool)
                or not isinstance(sample_value, (int, float))
                or not math.isfinite(sample_value)
                or sample_value <= 0
            ):
                raise ValueError("%s sampling %s must be positive" % (case_id, key))
            if key == "num_frames" and int(sample_value) != sample_value:
                raise ValueError("%s num_frames must be an integer" % case_id)
            sampling = {key: int(sample_value) if key == "num_frames" else float(sample_value)}
        elif sampling is not None:
            raise ValueError("%s image-only case cannot declare video sampling" % case_id)
        normalized_cases.append({
            "id": case_id,
            "modality": modality,
            "media": media,
            "prompt": prompt,
            "assertions": _validate_assertions(case_id, raw_case.get("assertions")),
            "repetitions": repetitions,
            "sampling": sampling,
        })
    return {
        "schema": MANIFEST_SCHEMA,
        "path": manifest_path,
        "sha256": _sha256(raw),
        "provenance": value.get("provenance"),
        "cases": normalized_cases,
    }


def evaluate_assertions(text: str, assertions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate deterministic casefold containment/order assertions."""
    folded = text.casefold()
    results = []
    for assertion in assertions:
        if assertion["type"] == "contains_casefold":
            passed = assertion["value"].casefold() in folded
        else:
            cursor = 0
            passed = True
            for value in assertion["values"]:
                position = folded.find(value.casefold(), cursor)
                if position < 0:
                    passed = False
                    break
                cursor = position + len(value)
        results.append({**assertion, "passed": passed})
    return results


def _messages(case: dict[str, Any]) -> list[dict[str, Any]]:
    content = []
    for media in case["media"]:
        key = "%s_url" % media["kind"]
        content.append({"type": key, key: {"url": media["_data_url"]}})
    content.append({"type": "text", "text": case["prompt"]})
    return [{"role": "user", "content": content}]


def _endpoint_models(base_url: str, api_key: str | None, timeout: float) -> list[str]:
    url = base_url.rstrip("/") + "/models"
    headers = {}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=min(timeout, 30.0)) as response:
        payload = json.loads(response.read(1024 * 1024 + 1))
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("endpoint /models response is malformed")
    return [
        row["id"] for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]


def _public_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: value for key, value in case.items() if key != "media"},
        "media": [
            {key: value for key, value in item.items() if key != "_data_url"}
            for item in case["media"]
        ],
    }


def main(argv=None, *, prog="anvil-serving eval benchmark multimodal", chat_request=chat):
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Run a hashed deterministic image/video corpus against one serve.",
    )
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--model-revision", required=True)
    ap.add_argument("--runtime-image", required=True)
    engine_identity = ap.add_mutually_exclusive_group(required=True)
    engine_identity.add_argument("--engine-revision")
    engine_identity.add_argument("--engine-build-ref")
    ap.add_argument("--hardware", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--api-key-env")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--timeout-seconds", type=float, default=900.0)
    ap.add_argument(
        "--thinking-mode",
        choices=("default", "enabled", "disabled", "unsupported"),
        default="default",
    )
    ap.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high"),
    )
    ap.add_argument("--allowed-finish-reasons", default="stop")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not _HEX_40.fullmatch(args.model_revision):
        ap.error("--model-revision must be an exact 40-character lowercase commit")
    if args.engine_revision is not None and not _HEX_40.fullmatch(args.engine_revision):
        ap.error("--engine-revision must be an exact 40-character lowercase commit")
    if args.engine_build_ref is not None and not _HEX_40.fullmatch(args.engine_build_ref):
        ap.error("--engine-build-ref must be an exact 40-character lowercase ref")
    if not 1 <= args.concurrency <= 4:
        ap.error("--concurrency must be from 1 through 4")
    if not 1 <= args.max_tokens <= 65536:
        ap.error("--max-tokens must be from 1 through 65536")
    if not math.isfinite(args.timeout_seconds) or not 0 < args.timeout_seconds <= 3600:
        ap.error("--timeout-seconds must be greater than 0 and at most 3600")
    if args.reasoning_effort is not None and args.thinking_mode != "default":
        ap.error("--reasoning-effort cannot be combined with --thinking-mode")
    allowed_finishes = {
        item.strip() for item in args.allowed_finish_reasons.split(",") if item.strip()
    }
    if not allowed_finishes:
        ap.error("--allowed-finish-reasons cannot be empty")
    try:
        validate_reasoning_control(
            args.model,
            thinking_mode=args.thinking_mode,
            no_thinking=False,
            reasoning_effort=args.reasoning_effort,
        )
        corpus = load_corpus(args.corpus)
        validate_write_target(args.output)
        api_key = resolve_api_key(args.api_key_env)
    except (OSError, ValueError) as exc:
        ap.error(str(exc))

    public_cases = [_public_case(case) for case in corpus["cases"]]
    work = [
        (case, repetition)
        for case in corpus["cases"]
        for repetition in range(1, case["repetitions"] + 1)
    ]
    if args.dry_run:
        print(json.dumps({
            "schema": "anvil-serving.eval-plan/v1",
            "workload": "multimodal",
            "target": {
                "base_url": args.base_url,
                "model": args.model,
                "model_revision": args.model_revision,
                "runtime_image": args.runtime_image,
                "engine_revision": args.engine_revision,
                "engine_build_ref": args.engine_build_ref,
                "hardware": args.hardware,
            },
            "corpus": {
                "schema": corpus["schema"],
                "path": corpus["path"],
                "sha256": corpus["sha256"],
                "cases": public_cases,
            },
            "requests": len(work),
            "concurrency": args.concurrency,
            "output": real_path(args.output),
            "deferred": ["endpoint identity", "model requests", "artifact write"],
        }, indent=2, sort_keys=True, ensure_ascii=True))
        return 0

    try:
        served_models = _endpoint_models(args.base_url, api_key, args.timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - retained as the lowest actionable error
        print("endpoint identity failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    if args.model not in served_models:
        print(
            "endpoint identity mismatch: requested model %r not in %r"
            % (args.model, served_models),
            file=sys.stderr,
        )
        return 1

    ctk = (
        {"enable_thinking": True} if args.thinking_mode == "enabled"
        else {"enable_thinking": False} if args.thinking_mode == "disabled"
        else None
    )
    started_at = time.time()

    def run_one(case, repetition):
        started = time.perf_counter()
        try:
            response, seconds = chat_request(
                args.base_url,
                args.model,
                _messages(case),
                api_key,
                max_tokens=args.max_tokens,
                temperature=0.0,
                timeout=args.timeout_seconds,
                chat_template_kwargs=ctk,
                reasoning_effort=args.reasoning_effort,
                mm_processor_kwargs=case["sampling"],
            )
            observation = response_observation(response)
            validations = evaluate_assertions(
                observation["content"], case["assertions"]
            )
            finish_allowed = observation["finish_reason"] in allowed_finishes
            visible_answer = bool(observation["content"].strip())
            passed = (
                visible_answer
                and finish_allowed
                and all(item["passed"] for item in validations)
            )
            return {
                "case_id": case["id"],
                "modality": case["modality"],
                "repetition": repetition,
                "sampling": case["sampling"],
                "media": _public_case(case)["media"],
                "seconds": round(seconds, 6),
                "output": observation["content"],
                "finish_reason": observation["finish_reason"],
                "reasoning": {
                    "field": observation["reasoning_field"],
                    "chars": observation["reasoning_chars"],
                    "tokens": observation["reasoning_tokens"],
                },
                "usage": observation["usage"],
                "validation": {
                    "visible_answer": visible_answer,
                    "finish_reason_allowed": finish_allowed,
                    "assertions": validations,
                },
                "passed": passed,
                "failure": None,
            }
        except Exception as exc:  # noqa: BLE001 - failures are evidence, not crashes
            return {
                "case_id": case["id"],
                "modality": case["modality"],
                "repetition": repetition,
                "sampling": case["sampling"],
                "media": _public_case(case)["media"],
                "seconds": round(time.perf_counter() - started, 6),
                "output": "",
                "finish_reason": None,
                "reasoning": {"field": None, "chars": 0, "tokens": None},
                "usage": None,
                "validation": {
                    "visible_answer": False,
                    "finish_reason_allowed": False,
                    "assertions": [],
                },
                "passed": False,
                "failure": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:1000],
                },
            }

    attempts = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(run_one, case, repetition): (case["id"], repetition)
            for case, repetition in work
        }
        for future in as_completed(futures):
            attempts.append(future.result())
    attempts.sort(key=lambda item: (item["case_id"], item["repetition"]))

    aggregates = {}
    for scope, key_fn in (
        ("case", lambda attempt: attempt["case_id"]),
        ("modality", lambda attempt: attempt["modality"]),
    ):
        rows = {}
        for attempt in attempts:
            rows.setdefault(key_fn(attempt), []).append(attempt)
        aggregates[scope] = {
            key: {
                "attempts": len(group),
                "passed": sum(item["passed"] for item in group),
                "pass_rate": sum(item["passed"] for item in group) / len(group),
                "latency_p50_seconds": statistics.median(
                    item["seconds"] for item in group
                ),
                "latency_p95_seconds": _percentile(
                    [item["seconds"] for item in group], 0.95
                ),
            }
            for key, group in sorted(rows.items())
        }
    passed = all(attempt["passed"] for attempt in attempts)
    artifact = {
        "schema": EVIDENCE_SCHEMA,
        "run_id": time.strftime(
            "multimodal-%Y%m%dT%H%M%SZ", time.gmtime(started_at)
        ),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "identity": {
            "base_url": args.base_url,
            "model": args.model,
            "model_revision": args.model_revision,
            "served_models": served_models,
            "runtime_image": args.runtime_image,
            "engine_revision": args.engine_revision,
            "engine_build_ref": args.engine_build_ref,
            "hardware": args.hardware,
        },
        "configuration": {
            "concurrency": args.concurrency,
            "max_tokens": args.max_tokens,
            "timeout_seconds": args.timeout_seconds,
            "thinking_mode": args.thinking_mode,
            "chat_template_kwargs": ctk,
            "reasoning_effort": args.reasoning_effort,
            "allowed_finish_reasons": sorted(allowed_finishes),
        },
        "corpus": {
            "schema": corpus["schema"],
            "path": corpus["path"],
            "sha256": corpus["sha256"],
            "provenance": corpus["provenance"],
            "cases": public_cases,
        },
        "attempts": attempts,
        "aggregates": aggregates,
        "requests": len(attempts),
        "passed_attempts": sum(attempt["passed"] for attempt in attempts),
        "failed_attempts": sum(not attempt["passed"] for attempt in attempts),
        "passed": passed,
    }
    atomic_write_json(args.output, artifact)
    print(
        "MULTIMODAL %d/%d passed; evidence=%s"
        % (artifact["passed_attempts"], artifact["requests"], args.output)
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
