#!/usr/bin/env python3
"""Private deterministic serving-cell projection; reads retained evidence only."""
from __future__ import annotations

import argparse, hashlib, importlib.util, json, math, sys
from pathlib import Path
from typing import Any

from anvil_serving.benchmarking.artifacts import atomic_write_json, evidence_file_reference

PROFILE = "serving-comparison/v1"
PLAN_SHA = "0f02f75f15f527f00c58bf46f16dcc1b523df98af50e38b0045e728fa87c3ea0"
COLLECTOR_SHA = "addcd9a4145e09c918282d939a6bd44860f690ec5ee24419a65dd0eb8ea0806e"
TIMINGS = {"effective_prefill_tok_s": "effective_prefill_tok_s", "time_to_first_output_ms": "time_to_first_output_ms", "ttft_ms": "ttft_ms", "decode_tok_s": "decode_tok_s", "tpot_ms": "tpot_ms", "e2e_ms": "e2e_ms", "prompt_tokens": "prompt_tokens", "completion_tokens": "completion_tokens"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError(f"expected JSON object: {path}")
    return value


def ref(path: Path, root: Path) -> dict[str, Any]: return evidence_file_reference(str(path.resolve()), root=str(root))

def distribution(metrics: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    suffix = "_ms" if prefix.endswith("_ms") else ""
    stem = prefix[:-3] if suffix else prefix
    items = (metrics.get(f"{stem}_samples"), metrics.get(f"{stem}_p50{suffix}"), metrics.get(f"{stem}_p95{suffix}"))
    if not isinstance(items[0], int) or isinstance(items[0], bool) or items[0] < 1: return None
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 for x in items[1:]): return None
    return {"samples": items[0], "p50": items[1], "p95": items[2]}

def import_collector(path: Path):
    spec = importlib.util.spec_from_file_location("private_collector", path)
    if spec is None or spec.loader is None: raise ValueError("cannot load approved collector")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module

def admission_counts(native):
    failures = native.get("failures")
    if not isinstance(failures, list) or len(failures) != native.get("failed") or any(not isinstance(item, dict) for item in failures):
        raise ValueError("native_failures_not_explicit_dicts")
    classes = {}
    for item in failures:
        classification = item.get("failure_class")
        if not isinstance(classification, str) or not classification or len(classification) > 64 or not classification.replace("_", "").isalnum():
            raise ValueError("failure_class_missing_or_ambiguous")
        classes[classification] = classes.get(classification, 0) + 1
    refused = classes.pop("admission_refusal", 0)
    return refused, classes

def request_identity(native: dict[str, Any]) -> dict[str, Any]:
    """Retain input comparability facts; reconstructed hashes are not wire-byte proof."""
    namespace, seed = native.get("unique_prefix_namespace"), native.get("context_seed")
    timings = native.get("request_timings")
    if not isinstance(namespace, str) or not namespace or not isinstance(seed, int) or not isinstance(timings, list):
        raise ValueError("native_request_identity_missing")
    prompts, indices, canaries = [], [], []
    for item in timings:
        if not isinstance(item, dict) or not isinstance(item.get("prompt_tokens"), int) or not isinstance(item.get("request_index"), int):
            raise ValueError("native_request_identity_invalid")
        canary = item.get("request_canary")
        if not isinstance(canary, dict) or canary.get("passed") is not True:
            raise ValueError("native_request_canary_invalid")
        prompts.append(item["prompt_tokens"]); indices.append(item["request_index"]); canaries.append(canary)
    if indices != list(range(len(timings))): raise ValueError("native_request_indices_noncanonical")
    return {"namespace": namespace, "context_seed": seed, "request_indices": indices, "prompt_tokens_ordered": prompts, "request_canaries": canaries, "wire_prompt_proof": "not retained; deterministic reconstruction is not transmitted-byte proof"}

def raw_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    values = [json.loads(line) for line in text.splitlines()] if path.suffix == ".jsonl" else [json.loads(text)]
    if not all(isinstance(v, dict) for v in values): raise ValueError(f"invalid telemetry object: {path.name}")
    return values

def strict_cache_labels(raw: str, model: str, collector) -> dict[str, str] | None:
    matches = []
    for line in raw.splitlines():
        match = collector._SAMPLE.match(line)
        if not match or match["name"] != "vllm:cache_config_info": continue
        labels = {item["key"]: item["value"] for item in collector._LABEL.finditer(match["labels"])}
        # This runtime emits one unlabeled CacheConfig gauge. Accept it only when
        # there is exactly one such gauge in an otherwise model-bound scrape.
        if "model_name" not in labels and labels.get("engine") in {None, "0"}: matches.append(labels)
        elif labels.get("model_name") == model and labels.get("engine") == "0": matches.append(labels)
    if len(matches) != 1: raise ValueError("missing_or_ambiguous_cache_config_info")
    return matches[0]

def engine_projection(records: list[dict[str, Any]], model: str, collector) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    states, caches, errors = [], [], []
    for record in records:
        raw = record.get("raw")
        if not isinstance(raw, str): continue
        try:
            states.append(collector.engine_state(raw, model))
        except ValueError as exc:
            errors.append(str(exc)); continue
        try:
            caches.append(strict_cache_labels(raw, model, collector))
        except ValueError as exc:
            errors.append(str(exc))
    if not states: raise ValueError("no_valid_engine_samples:" + ";".join(sorted(set(errors))))
    cache_values = {(item.get("kv_cache_size_tokens"), item.get("kv_cache_memory_bytes"), item.get("kv_cache_max_concurrency")) for item in caches}
    if len(cache_values) > 1: raise ValueError("ambiguous_cache_configuration")
    tokens, _configured_bytes, derived = cache_values.pop() if cache_values else (None, None, None)
    # cache_config_info.kv_cache_memory_bytes is a configured budget, not observed allocation.
    kv = {"allocated_tokens": int(tokens)} if tokens not in {None, "", "None"} and str(tokens).isdigit() else None
    estimate = float(derived) if derived not in {None, "", "None"} else None
    if estimate is not None and (not math.isfinite(estimate) or estimate < 0): raise ValueError("invalid_kv_cache_max_concurrency")
    active_max = max(int(state["running"]) for state in states)
    queued_max = max(int(state["waiting"]) for state in states)
    occupancy_max = max(int(state["running"] + state["waiting"]) for state in states)
    queue_reasons: dict[str, float] = {}
    for record in records:
        raw = record.get("raw")
        if not isinstance(raw, str): continue
        for line in raw.splitlines():
            match = collector._SAMPLE.match(line)
            if not match or match["name"] != "vllm:num_requests_waiting_by_reason": continue
            labels = {item["key"]: item["value"] for item in collector._LABEL.finditer(match["labels"])}
            if labels.get("model_name") != model or labels.get("engine") != "0": continue
            reason = labels.get("reason")
            value = float(match["value"])
            if not reason or not math.isfinite(value) or value < 0: raise ValueError("invalid_waiting_by_reason")
            queue_reasons[reason] = max(queue_reasons.get(reason, 0.0), value)
    result = {"active_max": active_max, "queued_max": queued_max, "occupancy_max": occupancy_max, "occupancy_definition": "sampled running plus waiting; active_max counts running only", "kv_usage_fraction_max": max(s["kv_usage_fraction"] for s in states), "samples": len(states), "sampled_lower_bound": True}
    if queue_reasons: result["waiting_by_reason_max"] = queue_reasons
    else: result["waiting_by_reason_unavailable"] = "no exact model/engine-0 num_requests_waiting_by_reason samples"
    return result, kv, ([f"engine_reported_full_context_concurrency_estimate={estimate}"] if estimate is not None else [])

def startup_kv_bytes(path: Path) -> tuple[int, dict[str, Any]] | None:
    if not path.is_file(): return None
    raw = path.read_text(encoding="utf-8")
    matches = __import__("re").findall(r"^\((Worker_[^)]+)\).*?Current kv cache memory in use is\s*([0-9.]+)\s*GiB", raw, flags=__import__("re").M)
    wording = "engine-reported current KV cache allocation"
    if not matches:
        matches = __import__("re").findall(r"^\((Worker_[^)]+)\).*?reserved\s+([0-9.]+)\s+GiB memory for KV Cache as specified", raw, flags=__import__("re").I | __import__("re").M)
        wording = "engine-reported KV reservation from fixed configuration"
    matches = matches[-2:]
    workers, values = [worker for worker, _value in matches], [float(value) for _worker, value in matches]
    if len(values) != 2 or len(set(workers)) != 2 or len(set(values)) != 1 or values[0] <= 0: return None
    per_gpu_bytes = int(values[0] * 1024 ** 3)
    return per_gpu_bytes * 2, {"accounting": wording, "per_gpu_gib_rounded": values[0], "per_gpu_bytes_rounded": per_gpu_bytes, "workers": 2, "physical_total_bytes": per_gpu_bytes * 2, "precision": "startup log rounds to GiB"}

def ordered(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def stamp(record: dict[str, Any]) -> int:
        value = record.get("stamp", {}).get("unix_ns") if isinstance(record.get("stamp"), dict) else None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0: raise ValueError("missing_timestamped_telemetry")
        return value
    return sorted(records, key=stamp)

def preemption_delta(before: Path, after: Path, model: str, collector) -> int:
    before_raw, after_raw = load(before).get("raw"), load(after).get("raw")
    if not isinstance(before_raw, str) or not isinstance(after_raw, str): raise ValueError("missing_preemption_snapshots")
    if collector.process_start(before_raw) != collector.process_start(after_raw): raise ValueError("engine_restart_or_counter_reset")
    delta = collector.engine_counter(after_raw, model, "vllm:num_preemptions_total") - collector.engine_counter(before_raw, model, "vllm:num_preemptions_total")
    if not delta.is_integer() or delta < 0: raise ValueError("invalid_preemption_delta")
    return int(delta)

def _model_engine_samples(raw: str, model: str, name: str, approved_collector) -> list[tuple[dict[str, str], float]]:
    """Return only the exact model/engine-0 Prometheus samples; ambiguity fails."""
    values = []
    for line in raw.splitlines():
        match = approved_collector._SAMPLE.match(line)
        if not match or match["name"] != name:
            continue
        labels = {item["key"]: item["value"] for item in approved_collector._LABEL.finditer(match["labels"])}
        if labels.get("model_name") == model and labels.get("engine") == "0":
            value = float(match["value"])
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid_histogram_sample_{name}")
            values.append((labels, value))
    return values

def histogram_delta(before: Path, after: Path, model: str, approved_collector) -> dict[str, Any]:
    """Preserve exact phase histogram counter deltas without manufacturing quantiles."""
    before_raw, after_raw = load(before).get("raw"), load(after).get("raw")
    if not isinstance(before_raw, str) or not isinstance(after_raw, str):
        raise ValueError("missing_histogram_snapshots")
    if approved_collector.process_start(before_raw) != approved_collector.process_start(after_raw):
        raise ValueError("engine_restart_or_histogram_reset")
    phases = ("request_prefill", "request_queue", "request_decode")
    result: dict[str, Any] = {}
    for phase in phases:
        buckets: dict[str, float] = {}
        for suffix in ("bucket", "count", "sum"):
            name = f"vllm:{phase}_time_seconds_{suffix}"
            earlier = _model_engine_samples(before_raw, model, name, approved_collector)
            later = _model_engine_samples(after_raw, model, name, approved_collector)
            def keyed(rows):
                output = {}
                for labels, value in rows:
                    key = labels.get("le", "") if suffix == "bucket" else "scalar"
                    if key in output: raise ValueError(f"ambiguous_histogram_{name}")
                    output[key] = value
                return output
            old, new = keyed(earlier), keyed(later)
            if set(old) != set(new) or not new:
                raise ValueError(f"histogram_shape_changed_{name}")
            deltas = {key: new[key] - old[key] for key in old}
            if any(value < 0 for value in deltas.values()):
                raise ValueError(f"negative_histogram_delta_{name}")
            if suffix == "bucket":
                buckets = deltas
            else:
                if suffix == "count" and not deltas["scalar"].is_integer():
                    raise ValueError(f"nonintegral_histogram_count_{name}")
                result.setdefault(phase, {})[suffix] = deltas["scalar"]
        # Prometheus cumulative bucket counts must remain monotone in the emitted order.
        parsed = [(float("inf") if key == "+Inf" else float(key), value) for key, value in buckets.items()]
        ordered_buckets = sorted(parsed)
        if any(right < left for (_, left), (_, right) in zip(ordered_buckets, ordered_buckets[1:])):
            raise ValueError(f"nonmonotone_histogram_buckets_{phase}")
        if not ordered_buckets or not math.isinf(ordered_buckets[-1][0]) or ordered_buckets[-1][1] != result[phase]["count"]:
            raise ValueError(f"histogram_count_mismatch_{phase}")
        result[phase]["buckets"] = [{"le": key, "count": buckets[key]} for key in sorted(buckets, key=lambda key: float("inf") if key == "+Inf" else float(key))]
    return result

def startup_runtime_memory(path: Path) -> dict[str, Any] | None:
    """Read two worker lines; values are runtime reports, never pure checkpoint weights."""
    if not path.is_file(): return None
    raw = path.read_text(encoding="utf-8")
    model_matches = __import__("re").findall(r"^\((Worker_[^)]+)\).*?Model loading took\s+([0-9.]+)\s+GiB(?:\s+memory)?\s+and", raw, flags=__import__("re").M)
    model_matches = model_matches[-2:]
    workers = [worker for worker, _value in model_matches]
    model_load = [float(value) for _worker, value in model_matches]
    consumed_matches = __import__("re").findall(r"^\((Worker_[^)]+)\).*?Actual usage is\s+([0-9.]+)\s+GiB for consumed memory \(weights \+ non-torch\)", raw, flags=__import__("re").M)[-2:]
    consumed_workers = [worker for worker, _value in consumed_matches]
    consumed = [float(value) for _worker, value in consumed_matches]
    if len(model_load) != 2 or len(set(workers)) != 2 or len(set(model_load)) != 1 or model_load[0] <= 0: return None
    result = {"accounting": "engine-reported rounded runtime model-load memory; not pure checkpoint weights", "per_worker_gib_rounded": model_load[0], "workers": 2, "physical_total_bytes": int(model_load[0] * 1024**3) * 2}
    if len(consumed) == 2 and len(set(consumed_workers)) == 2 and len(set(consumed)) == 1 and consumed[0] > 0:
        result["weights_plus_non_torch_per_worker_gib_rounded"] = consumed[0]
        result["weights_plus_non_torch_physical_total_bytes"] = int(consumed[0] * 1024**3) * 2
    return result

def slow_projection(records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    gpu_rows, host, cgroups = [], [], []
    for record in ordered(records):
        gpu = record.get("gpu", {}); rows = gpu.get("rows") if isinstance(gpu, dict) else None
        if not isinstance(rows, list) or len(rows) != 2: raise ValueError("invalid_gpu_sample")
        gpu_rows.extend(rows)
        hm = record.get("host_memory", {}); available = hm.get("MemAvailable", {}) if isinstance(hm, dict) else {}
        if not isinstance(available.get("value"), int) or available["value"] < 0: raise ValueError("missing_host_MemAvailable")
        host.append(available["value"] * 1024)
        cg = record.get("cgroup", {}).get("host_memory") if isinstance(record.get("cgroup"), dict) else None
        if not isinstance(cg, dict): raise ValueError("missing_cgroup_memory")
        cgroups.append(cg)
    used = [row.get("memory_used_mib") for row in gpu_rows]; free = [row.get("memory_free_mib") for row in gpu_rows]; util = [row.get("utilization_gpu_percent") for row in gpu_rows]
    if any(not isinstance(v, int) or v < 0 for v in [*used, *free, *util]): raise ValueError("invalid_gpu_numeric_sample")
    if not all(isinstance(c.get("current_bytes"), int) and isinstance(c.get("limit_bytes"), int) and isinstance(c.get("peak_bytes"), int) for c in cgroups): raise ValueError("invalid_cgroup_numeric_sample")
    gpu = {"accounting": "sampled nvidia-smi per-device; reported free preserved", "peak_used_bytes": max(used) * 1024**2, "min_free_bytes": min(free) * 1024**2, "max_utilization_percent": max(util), "samples_per_device": len(gpu_rows) // 2}
    last = cgroups[-1]
    host_out = {"accounting": "host MemAvailable plus cgroup status", "cgroup_current_bytes": last["current_bytes"], "cgroup_sampled_peak_bytes": max(c["current_bytes"] for c in cgroups), "cgroup_lifetime_peak_bytes": max(c["peak_bytes"] for c in cgroups), "cgroup_limit_bytes": last["limit_bytes"], "host_available_bytes": min(host)}
    events = {key: max(int(c.get("events", {}).get(key, 0)) for c in cgroups) - int(cgroups[0].get("events", {}).get(key, 0)) for key in {k for c in cgroups for k in c.get("events", {})}}
    failure = {"ooms": max(events.get("oom", 0), events.get("oom_kill", 0), events.get("oom_group_kill", 0)), "cgroup_events_delta": events}
    return gpu, host_out, failure

def self_test() -> int:
    assert admission_counts({"failed": 1, "failures": [{"failure_class": "quality_failure", "private_detail": "must not propagate"}]}) == (0, {"quality_failure": 1})
    assert admission_counts({"failed": 2, "failures": [{"failure_class": "admission_refusal"}, {"failure_class": "quality_failure"}]}) == (1, {"quality_failure": 1})
    for invalid in (["malformed"], [{}]):
        try: admission_counts({"failed": 1, "failures": invalid})
        except ValueError: pass
        else: raise AssertionError("ambiguous admission evidence accepted")
    """Positive and negative extractor fixtures, with no live or artifact inputs."""
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        path = root / "collector.py"
        path.write_text('''import re
_SAMPLE=re.compile(r'^(?P<name>vllm:[A-Za-z0-9_:]+)\\{(?P<labels>[^}]*)\\}\\s+(?P<value>\\S+)$')
_LABEL=re.compile(r'(?:^|,)(?P<key>[A-Za-z_][A-Za-z0-9_]*)="(?P<value>(?:[^"\\\\]|\\\\.)*)"')
def engine_state(raw, model): return {"running": float(raw.split(',')[0]), "waiting": float(raw.split(',')[1]), "kv_usage_fraction": 0.0}
def process_start(raw): return 1.0
def engine_counter(raw, model, name): return float(raw)
''')
        collector = import_collector(path)
        raw = 'vllm:cache_config_info{engine="0",model_name="other"} 1\nvllm:cache_config_info{engine="0",model_name="wanted"} 1\nvllm:cache_config_info{engine="0",model_name="wanted"} 1'
        try: strict_cache_labels(raw, "wanted", collector)
        except ValueError as exc: assert str(exc) == "missing_or_ambiguous_cache_config_info"
        else: raise AssertionError("ambiguous cache labels accepted")
        engine, _, _ = engine_projection([{"raw": "3,1"}], "wanted", collector)
        assert (engine["active_max"], engine["queued_max"], engine["occupancy_max"]) == (3, 1, 4)
        before, after = root / "before.json", root / "after.json"
        before.write_text(json.dumps({"raw": "2"})); after.write_text(json.dumps({"raw": "5"}))
        assert preemption_delta(before, after, "wanted", collector) == 3
        def histogram(value):
            rows = []
            for phase in ("prefill", "queue", "decode"):
                labels = 'engine="0",model_name="wanted"'
                rows.append(f'vllm:request_{phase}_time_seconds_bucket{{{labels},le="+Inf"}} {value}')
                rows.append(f'vllm:request_{phase}_time_seconds_count{{{labels}}} {value}')
                rows.append(f'vllm:request_{phase}_time_seconds_sum{{{labels}}} {value}')
            return "\n".join(rows)
        before.write_text(json.dumps({"raw": histogram(1)})); after.write_text(json.dumps({"raw": histogram(3)}))
        assert all(item["count"] == 2 for item in histogram_delta(before, after, "wanted", collector).values())
        startup = root / "startup.log"; startup.write_text("(Worker_A pid=1) Current kv cache memory in use is 7.75 GiB.\n(Worker_B pid=2) Current kv cache memory in use is 7.75 GiB.\n")
        total, caveat = startup_kv_bytes(startup) or (None, {})
        assert total == 2 * int(7.75 * 1024 ** 3) and caveat["workers"] == 2
        slow = [{"stamp":{"unix_ns":2}, "gpu":{"rows":[{"memory_used_mib":3,"memory_free_mib":7,"utilization_gpu_percent":1},{"memory_used_mib":3,"memory_free_mib":7,"utilization_gpu_percent":1}]}, "host_memory":{"MemAvailable":{"value":2}}, "cgroup":{"host_memory":{"current_bytes":2,"limit_bytes":9,"peak_bytes":11,"events":{"oom":0}}}}, {"stamp":{"unix_ns":1}, "gpu":{"rows":[{"memory_used_mib":2,"memory_free_mib":8,"utilization_gpu_percent":1},{"memory_used_mib":2,"memory_free_mib":8,"utilization_gpu_percent":1}]}, "host_memory":{"MemAvailable":{"value":3}}, "cgroup":{"host_memory":{"current_bytes":1,"limit_bytes":9,"peak_bytes":10,"events":{"oom":0}}}}]
        gpu, host, failure = slow_projection(slow)
        assert gpu["peak_used_bytes"] == 3 * 1024 ** 2 and host["cgroup_current_bytes"] == 2 and failure["ooms"] == 0
    print(json.dumps({"self_test": "passed", "fixtures": ["queued-occupancy", "preemption-delta", "kv-total-two-workers", "quality-independent-memory"]}, sort_keys=True)); return 0

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=Path); parser.add_argument("--arm"); parser.add_argument("--cell"); parser.add_argument("--native", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test: return self_test()
    if not all((args.root, args.arm, args.cell, args.native)): parser.error("--root --arm --cell --native are required")
    root = args.root.resolve(); plan_path = root / "plans/comparison-plan.json"
    if hashlib.sha256(plan_path.read_bytes()).hexdigest() != PLAN_SHA: raise ValueError("frozen comparison plan changed")
    if hashlib.sha256((root / "telemetry/collector.py").read_bytes()).hexdigest() != COLLECTOR_SHA: raise ValueError("approved collector changed")
    plan = load(plan_path)
    if plan.get("profile") != PROFILE or plan.get("frozen_before_requests") is not True: raise ValueError("comparison plan is not frozen")
    arm = plan["arms"][args.arm]; cell = plan["required_cells"][args.cell]
    if cell.get("arm") != args.arm: raise ValueError("cell arm mismatch")
    native = load(args.native); expected = {"model": arm["model"], "context_tokens": cell["context_tokens"], "concurrency": cell["concurrency"], "requests": cell["requests"]}
    bad = {key: [value, native.get(key)] for key, value in expected.items() if native.get(key) != value}
    if native.get("schema") != "anvil-serving.benchmark/v1" or bad: raise ValueError(f"native/frozen binding mismatch: {bad}")
    metrics = native.get("metrics") if isinstance(native.get("metrics"), dict) else {}; native_ref, plan_ref = ref(args.native, root), ref(plan_path, root)
    directory = root / "telemetry" / args.arm / args.cell; files = sorted(path for path in directory.iterdir() if path.is_file()) if directory.is_dir() else []
    collection = load(directory / "cell-summary.json")
    if collection.get("coverage_passed") is not True or collection.get("collector_sha256") != COLLECTOR_SHA:
        raise ValueError("cell collection was not accepted")
    refs = {path.relative_to(root).as_posix(): ref(path, root) for path in files}; engine_files = [p for p in files if p.name in {"before-metrics.json", "after-metrics.json", "engine-metrics.jsonl", "settle-metrics.jsonl"}]
    slow_files = [p for p in files if p.name in {"before-slow.json", "after-slow.json", "slow.jsonl"}]
    engine_records = [item for p in engine_files for item in raw_records(p)]; slow_records = [item for p in slow_files for item in raw_records(p)]
    projection: dict[str, Any] = {"schema":"serving-cell-projection/v2", "profile":PROFILE, "arm":args.arm, "cell_id":args.cell, "frozen_plan":plan_ref, "native":native_ref, "telemetry":refs, "metrics":{}, "supplementals":{}, "unavailable":[], "notes":[]}
    def measured(key, *, value=None, observation=None, evidence=None):
        sources = [native_ref, *(evidence or [])]
        unique = {item["path"]: item for item in sources}
        record={"status":"measured","evidence":[unique[path] for path in sorted(unique)]}
        if value is not None: record["value"] = value
        if observation is not None: record["observation"] = observation
        projection["metrics"][key]=record
    def missing(key, reason): projection["metrics"][key]={"status":"not_collected","reason":reason}; projection["unavailable"].append({"metric":key,"reason":reason})
    def supplemental(key, value=None, reason=None, evidence=None):
        if value is None:
            projection["supplementals"][key] = {"status":"not_collected", "reason": reason or "unavailable"}
        else:
            projection["supplementals"][key] = {"status":"measured", "value":value, "evidence":evidence or []}
    try:
        supplemental("request_identity", request_identity(native), evidence=[native_ref])
    except ValueError as exc:
        supplemental("request_identity", reason=str(exc))
    for key,prefix in TIMINGS.items():
        obs=distribution(metrics,prefix)
        measured(key,observation=obs) if obs else missing(key,f"native {prefix} distribution unavailable")
    throughput=metrics.get("throughput_tok_s")
    if isinstance(throughput,(int,float)) and not isinstance(throughput,bool) and throughput>=0 and metrics.get("throughput_performance_eligible") is True: measured("throughput_tok_s",value=throughput)
    else: missing("throughput_tok_s","native aggregate throughput absent or ineligible")
    actual_context = metrics.get("prompt_tokens_max")
    projection["configured_target_context_tokens"] = native["context_tokens"]
    if isinstance(actual_context, (int, float)) and not isinstance(actual_context, bool) and actual_context >= 0:
        measured("context_tokens",value=actual_context)
    else: missing("context_tokens", "native actual prompt_tokens_max unavailable; configured target retained separately")
    collector = import_collector(root / "telemetry/collector.py")
    try:
        engine, kv, notes = engine_projection(engine_records, native["model"], collector); projection["engine_sampling"]=engine; projection["notes"].extend(notes)
        supplemental("queue_waiting_by_reason", engine.get("waiting_by_reason_max"), engine.get("waiting_by_reason_unavailable", "waiting-by-reason unavailable"), list(refs.values()))
        client=native.get("client_concurrency_observed_max")
        if isinstance(client,int) and client>=1: measured("concurrency",observation={"requested":cell["concurrency"],"client_max":client,"engine_max":engine["active_max"]},evidence=[native_ref,*refs.values()])
        else: missing("concurrency","native client overlap unavailable")
        if kv and "allocated_tokens" in kv: measured("kv_cache_tokens",value=kv["allocated_tokens"],evidence=list(refs.values()))
        else: missing("kv_cache_tokens","fresh runtime allocated token pool unavailable")
        startup_log = next((candidate for candidate in (root / "logs" / f"{args.arm}-phase2-startup.log", root / "logs" / f"{args.arm}-startup.log") if candidate.is_file()), None)
        startup_ref = ref(startup_log, root) if startup_log else None
        load_memory = startup_runtime_memory(startup_log) if startup_log else None
        supplemental("runtime_model_load_memory", load_memory, "matching two-worker startup model-load observation unavailable", [startup_ref] if startup_ref else [])
        startup = startup_kv_bytes(startup_log) if startup_log else None
        if startup:
            bytes_value, caveat = startup
            measured("kv_cache_bytes",value=bytes_value,evidence=[startup_ref])
            projection.setdefault("kv_startup_observation", {}).update(caveat)
        else: missing("kv_cache_bytes","runtime cache configuration lacks actual allocation bytes and no matching fresh startup observation exists")
    except ValueError as exc:
        missing("concurrency",f"engine telemetry unavailable: {exc}"); missing("kv_cache_tokens",f"engine telemetry unavailable: {exc}"); missing("kv_cache_bytes",f"engine telemetry unavailable: {exc}")
        supplemental("queue_waiting_by_reason", reason=f"engine telemetry unavailable: {exc}")
        supplemental("runtime_model_load_memory", reason=f"engine telemetry unavailable before startup association: {exc}")
    try:
        gpu, host, failure=slow_projection(slow_records); projection["memory_sampling"]={"gpu":gpu,"host":host,"failure":failure}
        measured("gpu_memory",observation={"accounting":gpu["accounting"],"peak_used_bytes":gpu["peak_used_bytes"],"min_free_bytes":gpu["min_free_bytes"]},evidence=list(refs.values()))
        measured("host_memory",observation={"accounting":host["accounting"],"cgroup_current_bytes":host["cgroup_current_bytes"],"cgroup_peak_bytes":host["cgroup_sampled_peak_bytes"],"cgroup_limit_bytes":host["cgroup_limit_bytes"],"host_available_bytes":host["host_available_bytes"]},evidence=list(refs.values()))
        measured("oom_status",value=failure["ooms"],evidence=list(refs.values()))
    except ValueError as exc:
        failure = None
        for key in ("gpu_memory","host_memory","oom_status"): missing(key,f"slow telemetry unavailable: {exc}")
    before = directory / "before-metrics.json"; after = directory / "after-metrics.json"
    histogram_refs = [refs[path.relative_to(root).as_posix()] for path in (before, after) if path.relative_to(root).as_posix() in refs]
    try:
        histograms = histogram_delta(before, after, native["model"], collector)
        projection["engine_phase_histogram_deltas"] = histograms
        projection["engine_phase_histogram_evidence"] = histogram_refs
        supplemental("engine_phase_histograms", histograms, evidence=histogram_refs)
    except ValueError as exc:
        supplemental("engine_phase_histograms", reason=f"histogram evidence unavailable: {exc}")
    try:
        preemptions = preemption_delta(before, after, native["model"], collector)
        refused, non_admission = admission_counts(native)
        projection["non_admission_failure_counts"] = non_admission
        if failure is None: raise ValueError("measured OOM evidence missing")
        ooms = failure["ooms"]
        measured("admission_status",observation={"refused_requests":refused,"preemptions":preemptions,"ooms":ooms},evidence=[*refs.values()])
    except ValueError as exc:
        missing("admission_status",f"admission evidence unavailable: {exc}")
    receipt={"profile":PROFILE,"arm":args.arm,"identity":arm,"cell":cell,"raw":native_ref}
    receipt_path=root/"analysis/receipts"/f"{args.cell}.json"; projection_path=root/"analysis/projections"/f"{args.cell}.json"; atomic_write_json(str(receipt_path),receipt); projection["measurement"]=ref(receipt_path,root); atomic_write_json(str(projection_path),projection)
    print(json.dumps({"projection":str(projection_path),"receipt":str(receipt_path),"measured":sorted(k for k,v in projection["metrics"].items() if v["status"]=="measured"),"unavailable":projection["unavailable"]},sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
