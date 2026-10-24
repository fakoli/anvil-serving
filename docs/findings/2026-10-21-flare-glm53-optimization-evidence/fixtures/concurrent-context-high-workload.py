"""Bounded C4 context diagnostic using Anvil's existing independent oracle."""
import concurrent.futures
import datetime
import hashlib
import json
import os
import pathlib
import sys
import threading
import time
import urllib.request

sys.path.insert(0, "/workspace/anvil-serving")
from anvil_serving.benchmarking.context import build_native_context_case, score_context_response
from anvil_serving.benchmarking.artifacts import atomic_write_json

BASE = "http://127.0.0.1:39130"
MODEL = "glm53-flare-47581cca-tp2-c4-mtp3"
ROOT = pathlib.Path(__file__).parent


def metrics():
    with urllib.request.urlopen(BASE + "/metrics?format=json", timeout=5) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("oversized metrics response")
    return json.loads(raw)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def reserve(path):
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600))


def post(messages):
    body = {"model": MODEL, "messages": messages, "max_tokens": 8192, "temperature": 1.0,
            "top_p": .95, "reasoning_effort": "high", "stream": False}
    request = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=300) as response:
        raw = response.read(2_000_001)
    require(len(raw) <= 2_000_000, "oversized chat response")
    return {"latency_s": time.monotonic() - started, "response": json.loads(raw)}


def main():
    output, nonce = pathlib.Path(sys.argv[1]), sys.argv[2]
    require(output.parent == ROOT and not output.exists(), "new output in fixture directory required")
    require(len(nonce) < 80 and nonce.isascii() and nonce.replace("-", "").isalnum(), "invalid nonce")
    require(datetime.datetime.now(datetime.timezone.utc) < datetime.datetime(2026, 10, 21, 14, 55, tzinfo=datetime.timezone.utc), "start cutoff reached")
    before = metrics()
    require(before["active"] == before["pending"] == 0, "endpoint must be idle")
    reserve(output)
    journal_path = output.with_suffix(".journal.jsonl")
    descriptor = os.open(journal_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    journal = os.fdopen(descriptor, "w")
    journal_lock = threading.Lock()

    def emit(value):
        with journal_lock:
            journal.write(json.dumps(value) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

    emit({"type": "started", "at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "before": before})
    # Calibration is an estimate from the pinned checkpoint; response usage is authoritative.
    ratio = 4.832231202547146
    cases = []
    for index, (kind, position) in enumerate([( "native-needle", .1), ("native-order", .5), ("native-needle", .9), ("native-order", .1)]):
        case, expected = build_native_context_case(kind, requested_tokens=319488, position=position,
            repetition=index, token_counter=lambda text: round(len(text) / ratio), seed=91)
        case["token_measurement"] = "estimated-from-prior-calibration"
        messages = [{"role": "system", "content": f"Independent context case {nonce}-{index}. Answer the user's question exactly."},
                    {"role": "user", "content": case["prompt"]}]
        cases.append((case, expected, messages))
    plan = {"schema": "flare-concurrent-context-diagnostic/v1", "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "Four independent cold prefixes; exact-answer quality plus sampled occupancy, not a full-context capacity guarantee.",
        "controls": {"temperature": 1.0, "top_p": .95, "reasoning_effort": "high", "rendered_effort": "High", "chat_template_kwargs": None,
                     "max_tokens": 8192, "timeout_seconds": 300, "inference_seed": None},
        "prior_calibration": {"chars_per_token": ratio, "source": "native-cap16-fixed2-host32-context/evidence/0-context.json"},
        "script_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "source_hashes": {name: hashlib.sha256((pathlib.Path(sys.path[0]) / "anvil_serving/benchmarking" / name).read_bytes()).hexdigest() for name in ["context.py", "artifacts.py"]},
        "actual_prompt_gate": {"minimum": 300000, "maximum": 319488, "reason": "at least 93.9% of nominal input; output headroom stays within327680"},
        "cases": [{"case": {k: v for k, v in c.items() if k != "prompt"}, "expected": e, "messages": m} for c, e, m in cases]}
    plan_path = output.with_suffix(".plan.json")
    reserve(plan_path)
    atomic_write_json(plan_path, plan)
    samples, sample_errors, results = [], [], []
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            try:
                row = {"at_ns": time.monotonic_ns(), "metrics": metrics()}
                samples.append(row)
                emit({"type": "sample", **row})
            except Exception as exc:
                row = {"at_ns": time.monotonic_ns(), "error": str(exc)[:512]}
                sample_errors.append(row)
                emit({"type": "sample_error", **row})
            stop.wait(.25)

    def run(item):
        case, expected, messages = item
        started = time.monotonic_ns()
        response = None
        try:
            result = post(messages)
            response = result["response"]
            choice = response["choices"][0]
            usage = response.get("usage", {})
            require(type(usage.get("prompt_tokens")) is int and 300000 <= usage["prompt_tokens"] <= 319488, "actual prompt usage missing or outside300000..319488")
            observation = score_context_response(case, expected, choice["message"].get("content") or "",
                observed_prompt_tokens=usage.get("prompt_tokens"), latency_ms=result["latency_s"] * 1000,
                finish_reason=choice.get("finish_reason"))
            row = {"started_ns": started, "finished_ns": time.monotonic_ns(), "observation": observation, "response": response}
        except Exception as exc:
            row = {"started_ns": started, "finished_ns": time.monotonic_ns(), "observation": score_context_response(case, expected, "",
                failure={"code": type(exc).__name__, "message": str(exc)[:1024]}), "response": response}
        emit({"type": "completed_request", "result": row})
        return row

    sampler = threading.Thread(target=sample)
    sampler.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, cases))
    finally:
        stop.set()
        sampler.join(timeout=7)
    try:
        after = metrics()
        final_error = None
    except Exception as exc:
        after = None
        final_error = str(exc)[:1024]
    passed = sum(r["observation"]["passed"] for r in results)
    payload = {"schema": "flare-concurrent-context-diagnostic-result/v1", "plan": plan_path.name,
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(), "before": before, "after": after,
        "attempted": 4, "passed": passed, "observations": results, "samples": samples, "sample_errors": sample_errors,
        "sampled_active_max": max((s["metrics"]["active"] for s in samples), default=0),
        "client_overlap": max(r["started_ns"] for r in results) < min(r["finished_ns"] for r in results),
        "final_metrics_error": final_error, "actual_prompt_tokens": [(r.get("response") or {}).get("usage", {}).get("prompt_tokens") for r in results], "promoted": False}
    cached = [(r.get("response") or {}).get("usage", {}).get("prompt_tokens_details", {}).get("cached_tokens") for r in results]
    payload["gates"] = {"quality": passed == 4, "sampled_active_four": payload["sampled_active_max"] == 4,
        "client_overlap": payload["client_overlap"], "metrics_complete": not sample_errors and after is not None,
        "idle_after": after is not None and after["active"] == after["pending"] == 0,
        "no_engine_failure_delta": after is not None and after["failed"] == before["failed"],
        "cold_prefixes": all(isinstance(c, int) and c < 100 for c in cached)}
    emit({"type": "finished", "after": after, "gates": payload["gates"]})
    journal.close()
    require(not output.is_symlink(), "output became symlink")
    atomic_write_json(output, payload)
    print(json.dumps({k: payload[k] for k in ["attempted", "passed", "sampled_active_max", "client_overlap"]}), flush=True)
    return 0 if all(payload["gates"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
