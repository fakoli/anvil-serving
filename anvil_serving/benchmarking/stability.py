"""Bounded mixed-load replay. Client overlap is not scheduler-batch proof."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error

from .artifacts import atomic_write_json, validate_write_target
from .requests import resolve_api_key, stream_chat, validate_stream_result

SCENARIO_SCHEMA = "anvil-serving.stability-scenario/v1"
EVIDENCE_SCHEMA = "anvil-serving.stability/v1"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def failure_class(exc, fallback):
    if isinstance(exc, urllib.error.HTTPError):
        return {401: "authentication", 403: "authorization"}.get(exc.code, "http_error")
    if isinstance(exc, TimeoutError):
        return "client_timeout"
    if isinstance(exc, (OSError, urllib.error.URLError)):
        return "transport_error"
    return fallback


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate scenario key: {key}")
        result[key] = value
    return result


def validate_scenario(value):
    required = {"schema", "base_url", "model", "configuration", "context_limit",
                "anchor_tokens", "contender_tokens", "anchor_max_tokens",
                "contender_max_tokens", "rounds", "mode", "prefix_mode",
                "timeout_seconds", "run_timeout_seconds", "managed_container"}
    optional = {"api_key_env", "chat_template_kwargs", "reasoning_effort", "temperature"}
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("scenario has missing or unknown fields")
    if value["schema"] != SCENARIO_SCHEMA:
        raise ValueError("unsupported stability scenario schema")
    for field in ("base_url", "model", "managed_container"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    url = urllib.parse.urlsplit(value["base_url"])
    if (url.scheme not in {"http", "https"} or url.hostname != "127.0.0.1" or url.username
            or url.password or url.query or url.fragment or url.hostname == "localhost"
            or not url.path.rstrip("/").endswith("/v1")):
        raise ValueError("base_url must be a local managed HTTP(S) 127.0.0.1 /v1 URL without credentials")
    identity = value["configuration"]
    if not isinstance(identity, dict) or set(identity) != {"label", "recipe_sha256", "registry_sha256", "image_digest", "model_revision"}:
        raise ValueError("configuration requires label, recipe_sha256, registry_sha256, image_digest, model_revision")
    if not all(isinstance(v, str) and v for v in identity.values()):
        raise ValueError("configuration identities must be nonempty strings")
    for field in ("recipe_sha256", "registry_sha256"):
        if len(identity[field]) != 64 or any(c not in "0123456789abcdef" for c in identity[field]):
            raise ValueError(f"{field} must be a SHA-256")
    for field, prefix, length in (("image_digest", "sha256:", 64), ("model_revision", "", 40)):
        digest = identity[field].removeprefix(prefix)
        if not identity[field].startswith(prefix) or len(digest) != length or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"{field} must be an immutable content identity")
    for field, low, high in (("context_limit", 256, 2_000_000),
                            ("anchor_tokens", 128, 2_000_000),
                            ("contender_tokens", 128, 2_000_000),
                            ("anchor_max_tokens", 1, 65536),
                            ("contender_max_tokens", 1, 65536),
                            ("rounds", 1, 20), ("timeout_seconds", 1, 1800),
                            ("run_timeout_seconds", 1, 7200)):
        number = value[field]
        if type(number) is not int or not low <= number <= high:
            raise ValueError(f"{field} must be an integer in [{low}, {high}]")
    for role in ("anchor", "contender"):
        if value[role + "_tokens"] + value[role + "_max_tokens"] + 128 > value["context_limit"]:
            raise ValueError(f"{role} lacks context/output headroom")
    if value["mode"] not in {"serial", "overlap"} or value["prefix_mode"] not in {"unique", "repeat"}:
        raise ValueError("invalid mode or prefix_mode")
    kwargs = value.get("chat_template_kwargs", {})
    if not isinstance(kwargs, dict):
        raise ValueError("chat_template_kwargs must be an object")
    for field in ("api_key_env", "reasoning_effort"):
        if field in value and (not isinstance(value[field], str) or not value[field]):
            raise ValueError(f"{field} must be a nonempty string")
    strings = [value[field] for field in ("base_url", "model", "managed_container", "api_key_env", "reasoning_effort") if field in value]
    if any(any(ord(c) < 32 or ord(c) == 127 for c in s) or s != s.strip()
           for s in strings + list(identity.values())):
        raise ValueError("scenario identities must not contain controls or surrounding whitespace")
    temp = value.get("temperature", 0.0)
    if type(temp) not in (int, float) or not math.isfinite(temp) or not 0 <= temp <= 2:
        raise ValueError("temperature must be finite and between 0 and 2")
    return value


def api_json(url, key, timeout, body=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(url, headers=headers,
        data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("metadata response exceeds 16 MiB")
        return json.loads(raw)


def make_prompt(size, salt, role):
    """Synthetic retrieval plus natural decode, with values absent from the question."""
    values = [hashlib.sha256(f"{salt}:{i}".encode()).hexdigest()[:12] for i in range(3)]
    filler = "This record describes ordinary software components and their tests.\n"
    block = (filler * (size // len(filler) + 1))[:size]
    sections = [f"KEY_{i} = VALUE_{v}\n{block}" for i, v in enumerate(values)]
    tail = "\nReturn the values of KEY_0, KEY_1 and KEY_2 in order."
    if role == "anchor":
        tail += (" Then write a detailed engineering guide with 40 distinct numbered sections,"
                 " explaining implementation, examples, edge cases and tests in each section.")
    return f"Document {salt}\n" + "\n".join(sections) + tail, ["VALUE_" + v for v in values]


def calibrated_prompt(config, role, salt, key):
    target = config[role + "_tokens"]
    size = max(1, target)
    url = config["base_url"].rstrip("/")[:-3] + "/tokenize"
    for _ in range(8):
        prompt, expected = make_prompt(size, salt, role)
        body = {"model": config["model"], "messages": [{"role": "user", "content": prompt}],
                "add_generation_prompt": True,
                "chat_template_kwargs": config.get("chat_template_kwargs", {})}
        if "reasoning_effort" in config:
            body["reasoning_effort"] = config["reasoning_effort"]
        result = api_json(url, key, config["timeout_seconds"], body)
        count = result.get("count")
        if type(count) is not int or count <= 0:
            raise ValueError("tokenizer did not return a positive count")
        if abs(count - target) <= max(8, target * .005):
            if count + config[role + "_max_tokens"] > config["context_limit"]:
                raise ValueError("actual tokenized request exceeds context")
            return prompt, expected, count
        size = max(1, int(size * target / count))
        # Three filler sections: at most 24 MiB total even for a faulty tokenizer.
        if size > 8 * 1024 * 1024:
            raise ValueError("tokenizer calibration exceeds the 24 MiB prompt bound")
    raise ValueError("could not calibrate synthetic prompt within 0.5% or eight tokens")


def managed_identity(config):
    """Reuse the managed recipe inventory; never collect container environments."""
    from ..serve_recipes import discover_recipe_containers, select_recipe_container

    def bounded_command(*args, **kwargs):
        return subprocess.run(*args, timeout=15, **kwargs)

    row = select_recipe_container(discover_recipe_containers(_run=bounded_command),
                                  container=config["managed_container"])
    return {k: row.get(k) for k in ("container_id", "image_digest", "revision", "recipe_digest",
                                    "registry_digest", "served_identity", "bound_port", "running")}


def expected_identity(config):
    url = urllib.parse.urlsplit(config["base_url"])
    return {"image_digest": config["configuration"]["image_digest"],
            "revision": config["configuration"]["model_revision"],
            "recipe_digest": config["configuration"]["recipe_sha256"],
            "registry_digest": config["configuration"]["registry_sha256"],
            "served_identity": config["model"], "running": True,
            "bound_port": url.port or (443 if url.scheme == "https" else 80)}


def new_evidence(config, output):
    validate_scenario(config)
    validate_write_target(output)
    # Reserve a new artifact before contacting the endpoint; never erase an old trial.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    evidence = {"schema": EVIDENCE_SCHEMA, "scenario": config,
                "scenario_sha256": fingerprint(config), "started_at": utc_now(),
                "status": "running", "rounds": [], "promoted": False,
                "configuration_identity": "unverified", "identity_observations": [],
                "scheduler_overlap": "not_measured", "performance_eligible": False}
    atomic_write_json(output, evidence)
    return evidence


def run(config, output, *, stream=stream_chat, calibrate=calibrated_prompt, metadata=api_json,
        identity=managed_identity, _evidence=None):
    """Execute in bounded_run for a hard deadline; direct callers own cancellation."""
    evidence = _evidence if _evidence is not None else new_evidence(config, output)
    lock = threading.RLock()

    def save():
        with lock:
            atomic_write_json(output, evidence)

    def check_identity(phase):
        evidence["stage"] = "identity_discovery"
        row = identity(config)
        evidence["stage"] = "identity_check"
        evidence["identity_observations"].append({"phase": phase, "observed_at": utc_now(), **row})
        save()
        expected = expected_identity(config)
        if (not row.get("container_id") or any(row.get(k) != v for k, v in expected.items())
                or row["container_id"] != evidence["identity_observations"][0]["container_id"]):
            raise ValueError("managed configuration identity changed or mismatched")
        evidence["configuration_identity"] = "managed_container_observed_labels_matched"

    def request(role, prompt, expected, count, pair, event, key):
        row = pair[role]
        with lock:
            row.update(started_at=utc_now(), start=time.monotonic(), prompt_tokens_tokenized=count,
                       prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                       expected_values=expected, status="running", partial_visible="")
            save()

        def observe(kind, data):
            with lock:
                if kind == "response":
                    row["request_id"] = data.get("request_id")
                    row["response_headers_at"] = time.monotonic()
                elif kind == "stream_id":
                    if row.setdefault("stream_id", data["request_id"]) != data["request_id"]:
                        raise ValueError("SSE response identity changed within a request")
                elif kind == "server_error":
                    row["server_error"] = data["error"]
                elif kind == "malformed":
                    row["malformed_chunks"] = row.get("malformed_chunks", 0) + 1
                elif kind == "delta":
                    now = time.monotonic()
                    first = "first_output" not in row
                    row.setdefault("first_output", now)
                    row["last_output"] = now
                    content = data.get("content") or ""
                    row["partial_visible"] = (row["partial_visible"] + content)[:8192]
                    if role == "anchor":
                        contender = pair["contender"]
                        if "response_headers_at" in contender and "first_output" not in contender and "end" not in contender:
                            pair["anchor_output_during_contender_response_wait"] = True
                            pair.setdefault("overlap_output_at", now)
                    event.set()
                    if first:
                        save()

        try:
            result = stream(config["base_url"], config["model"], prompt, key,
                            config[role + "_max_tokens"], timeout=config["timeout_seconds"],
                            chat_template_kwargs=config.get("chat_template_kwargs"),
                            reasoning_effort=config.get("reasoning_effort"),
                            temperature=config.get("temperature", 0.0), observer=observe,
                            deadline_seconds=config["timeout_seconds"])
            validate_stream_result(result)
            if row.get("malformed_chunks") or not result.get("finish_reasons"):
                raise ValueError("malformed or unterminated protocol stream")
            if any(reason not in {"stop", "length"} for reason in result["finish_reasons"]):
                raise ValueError("unexpected finish reason")
            usage = result.get("usage") or {}
            actual = usage.get("prompt_tokens")
            with lock:
                row.update(status="completed", result=result,
                           usage_verified=type(actual) is int and abs(actual - count) <= max(8, count * .005),
                           retrieval_passed=all(v in result["visible_content"] for v in expected))
                row["failure_classes"] = ([] if row["usage_verified"] else ["token_usage_mismatch"]) + (
                    [] if row["retrieval_passed"] else ["retrieval_failure"])
        except Exception as exc:
            with lock:
                row.update(status="failed", error_type=type(exc).__name__,
                           http_status=getattr(exc, "code", None), error=str(exc)[:1024],
                           failure_classes=["server_stream_error" if "server_error" in row else
                                            failure_class(exc, "protocol_error" if isinstance(exc, ValueError) else "harness_error")])
        finally:
            with lock:
                row.update(end=time.monotonic(), finished_at=utc_now())
                save()
        return row

    save()
    try:
        evidence["stage"] = "authentication"
        key = resolve_api_key(config.get("api_key_env"))
        check_identity("start")
        evidence["stage"] = "served_identity_check"
        observed = metadata(config["base_url"].rstrip("/") + "/models", key, 15)
        matches = [r for r in observed.get("data", []) if isinstance(r, dict) and r.get("id") == config["model"]]
        if len(matches) != 1:
            raise ValueError("exact served model missing or ambiguous")
        evidence["served_model_observed"] = config["model"]
        run_salt = evidence["started_at"]
        for index in range(config["rounds"]):
            check_identity(f"round_{index}_before")
            salt = f"{run_salt}:{0 if config['prefix_mode'] == 'repeat' else index}"
            pair = {"index": index, "anchor": {}, "contender": {},
                    "anchor_output_during_contender_response_wait": False}
            evidence["rounds"].append(pair)
            evidence["stage"] = "tokenization"
            save()
            inputs = {role: calibrate(config, role, salt + role, key)
                      for role in ("anchor", "contender")}
            evidence["stage"] = "requests"
            first_output = threading.Event()
            with ThreadPoolExecutor(max_workers=2) as pool:
                anchor = pool.submit(request, "anchor", *inputs["anchor"], pair, first_output, key)
                if config["mode"] == "serial":
                    anchor.result()
                else:
                    deadline = time.monotonic() + config["timeout_seconds"]
                    while not first_output.wait(.05) and not anchor.done():
                        if time.monotonic() >= deadline:
                            break
                can_launch = (pair["anchor"].get("status") == "completed" if config["mode"] == "serial"
                              else first_output.is_set() and not anchor.done())
                if can_launch:
                    request("contender", *inputs["contender"], pair, threading.Event(), key)
                else:
                    pair["contender"] = {"status": "not_run", "reason": "anchor did not provide required trigger"}
                anchor.result()
            pair["runtime_passed"] = all(pair[r].get("status") == "completed" for r in ("anchor", "contender"))
            pair["coverage_passed"] = (pair["runtime_passed"]
                                       and all(pair[r].get("usage_verified") for r in ("anchor", "contender"))
                                       and (config["mode"] == "serial" or pair["anchor_output_during_contender_response_wait"]))
            pair["retrieval_passed"] = all(pair[r].get("retrieval_passed") is True for r in ("anchor", "contender"))
            pair["failure_classes"] = sorted({c for r in ("anchor", "contender")
                                               for c in pair[r].get("failure_classes", [])})
            if not pair["coverage_passed"]:
                pair["failure_classes"].append("coverage_not_exercised")
            check_identity(f"round_{index}_after")
            save()
            if not pair["runtime_passed"] or not pair["coverage_passed"] or not pair["retrieval_passed"]:
                break
        complete = len(evidence["rounds"]) == config["rounds"]
        evidence["runtime_passed"] = complete and all(p.get("runtime_passed") for p in evidence["rounds"])
        evidence["coverage_passed"] = complete and all(p.get("coverage_passed") for p in evidence["rounds"])
        evidence["retrieval_passed"] = complete and all(p.get("retrieval_passed") for p in evidence["rounds"])
        evidence["status"] = ("completed" if evidence["runtime_passed"] and evidence["coverage_passed"]
                              and evidence["retrieval_passed"] else "incomplete")
    except BaseException as exc:
        evidence.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:1024])
        evidence["failure_class"] = failure_class(exc, {
            "authentication": "authentication", "identity_discovery": "identity_unavailable",
            "identity_check": "identity_mismatch", "served_identity_check": "identity_mismatch",
            "tokenization": "tokenization_failure"}.get(evidence.get("stage"), "harness_error"))
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        evidence["finished_at"] = utc_now()
        save()
    return evidence


def bounded_run(config, output):
    """Kill the isolated client at the total deadline, preserving its last checkpoint."""
    from ..control_plane.mcp.runtime import _process_group_options, _terminate_process_tree

    evidence = new_evidence(config, output)
    worker = None
    failure = None
    try:
        worker = subprocess.Popen([sys.executable, "-c",
            "import json,sys; from anvil_serving.benchmarking.stability import run; run(**json.load(sys.stdin))"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **_process_group_options())
        packet = {"config": config, "output": str(output), "_evidence": evidence}
        worker.communicate(json.dumps(packet).encode(), timeout=config["run_timeout_seconds"])
    except subprocess.TimeoutExpired:
        failure = "client_timeout"
    except KeyboardInterrupt:
        failure = "client_cancellation"
    except OSError:
        failure = "harness_error"
    finally:
        if worker is not None and (failure or worker.poll() is None):
            _terminate_process_tree(worker)
        evidence = json.loads(Path(output).read_text())
        if failure or evidence["status"] == "running":
            evidence.update(status="failed", finished_at=utc_now(),
                            failure_class=failure or "harness_error",
                            server_compute_stopped="unknown")
            atomic_write_json(output, evidence)
    return evidence


def read_scenario(path):
    # O_NONBLOCK prevents a FIFO from blocking before fstat can reject it.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("scenario must be a regular file")
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValueError("scenario exceeds 64 KiB")
    return validate_scenario(json.loads(raw, object_pairs_hook=unique_object))


def main(argv=None, *, prog="anvil-serving eval benchmark stability"):
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = read_scenario(args.scenario)
        validate_write_target(args.output)
        if Path(args.output).exists():
            raise ValueError("output already exists; retain it and use a new trial path")
        if not args.confirm or args.dry_run:
            print(json.dumps({"schema": SCENARIO_SCHEMA, "dry_run": True, "scenario": config,
                              "scenario_sha256": fingerprint(config),
                              "maximum_generation_requests": config["rounds"] * 2,
                              "maximum_tokenization_requests": config["rounds"] * 16,
                              "maximum_model_reads": 1,
                              "maximum_managed_identity_reads": 1 + config["rounds"] * 2,
                              "maximum_http_requests": 1 + config["rounds"] * 18,
                              "hard_run_deadline_seconds": config["run_timeout_seconds"],
                              "network_requests": 0, "output": args.output}, indent=2))
            return 0
        result = bounded_run(config, args.output)
        print(json.dumps({"status": result["status"], "output": args.output,
                          "rounds": len(result["rounds"]), "scheduler_overlap": "not_measured"}))
        return 0 if result["status"] == "completed" else 1
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
