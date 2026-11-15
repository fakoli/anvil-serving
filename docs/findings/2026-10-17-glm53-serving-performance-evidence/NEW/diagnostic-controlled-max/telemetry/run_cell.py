#!/usr/bin/env python3
"""Retained campaign validation harness; executes only frozen capacity argv."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request

import collector as C

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_SHA = "addcd9a4145e09c918282d939a6bd44860f690ec5ee24419a65dd0eb8ea0806e"
PLAN_SHA = "8d594ec0d5c5d431068ab41c26d3682b83205b9bef4b360ef1fef86a7e5f24da"
ENV_HOME = "[PRIVATE_OPERATOR_ROOT]"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def load_plan():
    path = ROOT / "plans/run-plan.json"
    if sha(path) != PLAN_SHA:
        raise ValueError("frozen plan changed")
    return json.loads(path.read_text())


def validate_native(row, native):
    argv = row["argv"]
    def option(name):
        return argv[argv.index(name) + 1]
    expected = {"schema": "anvil-serving.benchmark/v1", "model": option("--model"),
                "gpu": option("--gpu"),
                "unique_prefix_namespace": "capacity-v1:seed-request-index",
                "engine": option("--engine"), "base_url": option("--base-url"),
                "context_tokens": row["planned_context_tokens"], "concurrency": row["concurrency"],
                "requests": row["requests"], "context_seed": row["seed"],
                "max_context_tokens": int(option("--max-model-len")),
                "max_tokens": int(option("--max-tokens")), "prompt_cache_mode": option("--prompt-cache-mode"),
                "request_canaries": "--request-canaries" in argv,
                "response_words": int(option("--response-words")),
                "controlled_output_policy": option("--controlled-output-policy")}
    if any(native.get(key) != value for key, value in expected.items()):
        raise ValueError("native identity/control mismatch")
    if native.get("measurement_protocol") not in {"capacity-v3", "capacity-v4-reasoning"}:
        raise ValueError("native measurement protocol mismatch")
    workload = "synthetic-repeated-code" if expected["response_words"] else "synthetic-summary"
    if native.get("workload_identity", {}).get("workload_kind") != workload:
        raise ValueError("native workload mismatch")
    if native.get("serve_flags", {}).get("reasoning_effort") != option("--reasoning-effort"):
        raise ValueError("native reasoning control mismatch")
    for key in ("temperature", "top_p"):
        observed = native.get("sampling", {}).get(key, {})
        if observed.get("sent") is not True or observed.get("effective_request") != float(option("--" + key.replace("_", "-"))):
            raise ValueError("native sampling mismatch")
    completed, failed, received = (native.get(key) for key in ("completed", "failed", "responses_received"))
    if any(type(value) is not int or value < 0 for value in (completed, failed, received)):
        raise ValueError("invalid native population")
    if completed + failed != row["requests"] or not completed <= received <= row["requests"]:
        raise ValueError("incoherent native population")
    if len(native.get("request_timings", [])) != completed or len(native.get("failures", [])) != failed:
        raise ValueError("native timing/failure population mismatch")


def sampling_window(directory, started, finished):
    result = {}
    for name, filename, maximum_gap in (("engine", "engine-metrics.jsonl", 3), ("slow", "slow.jsonl", 6)):
        records = [json.loads(line) for line in (directory / filename).read_text().splitlines()]
        stamps = [record["stamp"]["monotonic_ns"] for record in records if "error" not in record]
        inside = sorted(stamp for stamp in stamps if started <= stamp <= finished)
        gaps = [(b-a) / 1e9 for a, b in zip([started, *inside], [*inside, finished])]
        result[name] = {"in_window_samples": len(inside), "max_gap_seconds": max(gaps),
                        "target_interval_seconds": .5 if name == "engine" else 2}
        if not inside or max(gaps) > maximum_gap:
            raise ValueError("missing or stalled in-flight " + name + " telemetry")
    return result


def sample_loop(callback, path, interval, stop, fault, errors, counts, name):
    """The same loop is used for both actual collectors and the offline test."""
    with path.open("w") as out:
        while not stop.is_set():
            started = time.monotonic()
            try:
                record = callback()
                out.write(json.dumps(record, sort_keys=True) + "\n")
                out.flush()
                counts[name] += 1
            except Exception as exc:
                error = {"stamp": C.stamp(), "collector": name, "error": repr(exc)}
                errors.append(error)
                out.write(json.dumps(error) + "\n")
                out.flush()
                fault.set()
                return
            stop.wait(max(0, interval - (time.monotonic() - started)))


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        stop, fault = threading.Event(), threading.Event()
        counts, errors = {"engine": 0, "slow": 0}, []
        invocations = {"gpu": 0, "host": 0, "cgroup": 0}

        def slow():
            for key in invocations:
                invocations[key] += 1
            return {"stamp": C.stamp(), **invocations}

        threads = [threading.Thread(target=sample_loop, args=(callback, Path(directory) / name,
                   interval, stop, fault, errors, counts, name))
                   for name, callback, interval in (("engine", C.stamp, .01), ("slow", slow, .02))]
        for thread in threads:
            thread.start()
        time.sleep(.12)
        stop.set()
        for thread in threads:
            thread.join()
        assert not fault.is_set() and not errors
        assert counts["engine"] >= 2 and counts["slow"] >= 2
        assert all(value == counts["slow"] for value in invocations.values())
        bad_stop, bad_fault = threading.Event(), threading.Event()
        def bad():
            raise ValueError("fixture collector failure")
        sample_loop(bad, Path(directory) / "bad", .01, bad_stop, bad_fault, errors, counts, "slow")
        assert bad_fault.is_set() and errors[-1]["collector"] == "slow"
        return {"status": "passed", "sample_counts": counts, "slow_invocations": invocations,
                "failure_propagates": True}


def run_cell(row, plan, identity_path):
    frozen = load_plan()
    if plan != frozen or row not in frozen["matrix"]:
        raise ValueError("row not from frozen plan")
    if sha(Path(C.__file__)) != COLLECTOR_SHA:
        raise ValueError("collector source changed")
    arm = row["arm"]
    model = plan["models"][arm]
    artifact = Path(row["argv"][row["argv"].index("--output") + 1])
    directory = ROOT / "telemetry" / arm / row["id"]
    if artifact.resolve() != ROOT / "native" / arm / (row["id"] + ".json") or Path(row["id"]).name != row["id"]:
        raise ValueError("row output path escaped campaign")
    if artifact.exists() or directory.exists():
        raise ValueError("refuse overwrite of prior attempt")
    cutoff = datetime.datetime.fromisoformat(plan["absolute_request_cutoff"]).timestamp()
    # Do not start a cell whose bounded execution would cross the request cutoff.
    if time.time() + row["outer_timeout_seconds"] > cutoff:
        raise ValueError("insufficient time before frozen request cutoff")
    expected = json.loads(identity_path.read_text())
    if "data" in expected:
        expected = json.loads(expected["data"])
    for key in ("container", "model", "revision"):
        required = model["repository"] if key == "model" else model[key]
        if expected[key] != required:
            raise ValueError("identity receipt differs from frozen plan")
    status_argv = row["argv"][:3] + ["models", "recipes", "status", model["repository"],
                    "--container", model["container"], "--registry", model["registry"],
                    "--transport", "local", "--json"]
    directory.mkdir(parents=True)
    env = dict(os.environ, ANVIL_SERVING_HOME=ENV_HOME)
    os.environ["ANVIL_SERVING_HOME"] = ENV_HOME

    def engine():
        with urllib.request.urlopen(f"http://127.0.0.1:{model['port']}/metrics", timeout=10) as response:
            raw = response.read().decode()
        return {"stamp": C.stamp(), "raw": raw, "state": C.engine_state(raw, model["model"]),
                "process_start_time_seconds": C.process_start(raw)}

    def slow():
        return {"stamp": C.stamp(), "gpu": C.gpu_sample(), "host_memory": C.host_memory(),
                "cgroup": C.status_sample(status_argv, expected)}

    before = engine()
    save(directory / "before-metrics.json", before)
    if before["state"]["running"] or before["state"]["waiting"]:
        raise ValueError("engine not idle before cell")
    before_slow = slow()
    save(directory / "before-slow.json", before_slow)
    errors, counts = [], {"engine": 0, "slow": 0}
    stop, fault = threading.Event(), threading.Event()
    summary = {"cell": row["id"], "argv": row["argv"], "frozen_plan_sha256": sha(ROOT / "plans/run-plan.json"),
               "collector_sha256": COLLECTOR_SHA, "runner_sha256": sha(__file__),
               "identity_receipt_sha256": sha(identity_path), "start": C.stamp(),
               "collector_errors": errors, "sample_counts": counts, "coverage_passed": False}
    logdir = ROOT / "logs/cells"
    logdir.mkdir(parents=True, exist_ok=True)
    with (logdir / f"{row['id']}.stdout").open("w") as stdout, (logdir / f"{row['id']}.stderr").open("w") as stderr:
        proc = subprocess.Popen(row["argv"], stdout=stdout, stderr=stderr, env=env)
        summary["process_started"] = C.stamp()
        threads = [threading.Thread(target=sample_loop, args=(callback, directory / filename,
                   interval, stop, fault, errors, counts, name))
                   for name, callback, filename, interval in (
                       ("engine", engine, "engine-metrics.jsonl", .5),
                       ("slow", slow, "slow.jsonl", 2.0))]
        for thread in threads:
            thread.start()
        expiry = time.monotonic() + row["outer_timeout_seconds"]
        while proc.poll() is None:
            if fault.is_set() or time.monotonic() >= expiry or time.time() >= cutoff:
                summary["aborted"] = "collector failure" if fault.is_set() else "deadline"
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()  # Only the harness-owned CLI, never a managed serve.
                    proc.wait(timeout=10)
                break
            time.sleep(.1)
        summary["returncode"] = proc.returncode
        summary["process_finished"] = C.stamp()
        stop.set()
        for thread in threads:
            thread.join(timeout=75)
        if any(thread.is_alive() for thread in threads):
            errors.append({"error": "collector thread did not finish"})
    save(directory / "cell-summary.json", summary)
    # A client exit is not proof that the upstream requests finished.
    idle = False
    with (directory / "settle-metrics.jsonl").open("w") as out:
        settle_end = time.monotonic() + 900
        while time.monotonic() < settle_end:
            after = engine()
            out.write(json.dumps(after) + "\n")
            out.flush()
            if not after["state"]["running"] and not after["state"]["waiting"]:
                idle = True
                break
            time.sleep(1)
    if not idle:
        raise ValueError("upstream did not drain; lifecycle owner must investigate")
    after_slow = slow()
    save(directory / "after-slow.json", after_slow)
    native = json.loads(artifact.read_text()) if artifact.exists() else None
    if native:
        validate_native(row, native)
        timings = list(native["request_timings"])
        timings += [failure["response_timing"] for failure in native["failures"]
                    if isinstance(failure.get("response_timing"), dict)]
        if len(timings) == native["responses_received"] and all(
                type(t.get("prompt_tokens")) is int and type(t.get("output_tokens")) is int for t in timings):
            expected_prompt = sum(t["prompt_tokens"] for t in timings)
            expected_output = sum(t["output_tokens"] for t in timings)
            for attempt in range(20):
                try:
                    delta = C.exact_counter_delta(before["raw"], after["raw"], model["model"],
                            expected_prompt, expected_output, native["responses_received"])
                    summary["counter_reconciliation"] = {"status": "exact", "deltas": delta}
                    break
                except ValueError as exc:
                    summary["counter_reconciliation"] = {"status": "incomplete", "reason": str(exc)}
                    if attempt < 19:
                        time.sleep(1)
                        after = engine()
                        with (directory / "settle-metrics.jsonl").open("a") as out:
                            out.write(json.dumps(after) + "\n")
        else:
            summary["counter_reconciliation"] = {"status": "incomplete", "reason": "response usage ambiguous"}
    save(directory / "after-metrics.json", after)
    summary["end"] = C.stamp()
    oom_before = before_slow["cgroup"]["host_memory"]["events"]
    oom_after = after_slow["cgroup"]["host_memory"]["events"]
    summary["oom_event_deltas"] = {k: oom_after.get(k, 0) - oom_before.get(k, 0) for k in ("oom", "oom_kill", "oom_group_kill")}
    try:
        summary["sampling_window"] = sampling_window(directory, summary["process_started"]["monotonic_ns"], summary["process_finished"]["monotonic_ns"])
    except ValueError as exc:
        errors.append({"error": str(exc)})
    summary["coverage_passed"] = bool(native and not errors and all(counts.values())
           and not summary.get("aborted") and summary.get("counter_reconciliation", {}).get("status") == "exact")
    save(directory / "cell-summary.json", summary)
    print(json.dumps({"cell": row["id"], "completed": native.get("completed") if native else None,
                      "failed": native.get("failed") if native else None,
                      "coverage_passed": summary["coverage_passed"], "samples": counts}), flush=True)
    if not summary["coverage_passed"] or any(summary["oom_event_deltas"].values()):
        return 2
    # Root adjudicates every model failure before a dependent cell is dispatched.
    return 0 if native["failed"] == 0 and summary["returncode"] == 0 else 3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--queue", type=Path)
    parser.add_argument("--identity", type=Path)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return 0
    if not args.queue or not args.identity:
        parser.error("--queue and --identity required")
    plan = load_plan()
    rows = {row["id"]: row for row in plan["matrix"]}
    queue = json.loads(args.queue.read_text())
    if len(queue) != len(set(queue)) or any(cell not in rows for cell in queue):
        raise ValueError("queue must contain unique frozen row IDs")
    for cell in queue:
        result = run_cell(rows[cell], plan, args.identity)
        if result:
            return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
