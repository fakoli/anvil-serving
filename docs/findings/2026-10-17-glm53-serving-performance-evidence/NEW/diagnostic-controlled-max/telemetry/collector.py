#!/usr/bin/env python3
"""Private campaign telemetry collector; records evidence, never sends model requests."""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import subprocess
import time
import urllib.request


_SAMPLE = re.compile(r'^(?P<name>vllm:[A-Za-z0-9_:]+)\{(?P<labels>[^}]*)\}\s+(?P<value>\S+)$')
_LABEL = re.compile(r'(?:^|,)(?P<key>[A-Za-z_][A-Za-z0-9_]*)="(?P<value>(?:[^"\\]|\\.)*)"')
_EXPECTED_GPU_UUIDS = (
    "GPU-REDACTED",
    "GPU-REDACTED",
)


def stamp():
    return {"unix_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}


def engine_value(raw, model, name):
    values = []
    for line in raw.splitlines():
        match = _SAMPLE.match(line)
        labels = dict((item["key"], item["value"]) for item in _LABEL.finditer(match["labels"])) if match else {}
        if match and match["name"] == name and labels.get("model_name") == model and labels.get("engine") == "0":
            values.append(float(match["value"]))
    if len(values) != 1 or not math.isfinite(values[0]) or values[0] < 0:
        raise ValueError(f"missing_or_ambiguous_{name}")
    return values[0]


def engine_state(raw, model):
    state = {
        "running": engine_value(raw, model, "vllm:num_requests_running"),
        "waiting": engine_value(raw, model, "vllm:num_requests_waiting"),
        "kv_usage_fraction": engine_value(raw, model, "vllm:kv_cache_usage_perc"),
    }
    if state["running"] != int(state["running"]) or state["waiting"] != int(state["waiting"]):
        raise ValueError("non_integral_engine_request_count")
    if not 0 <= state["kv_usage_fraction"] <= 1:
        raise ValueError("invalid_kv_usage_fraction")
    return state


def engine_counter(raw, model, name):
    values = []
    for line in raw.splitlines():
        match = _SAMPLE.match(line)
        labels = dict((item["key"], item["value"]) for item in _LABEL.finditer(match["labels"])) if match else {}
        if match and match["name"] == name and labels.get("model_name") == model and labels.get("engine") == "0":
            values.append(float(match["value"]))
    if not values or any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError(f"missing_or_negative_{name}")
    return sum(values)


def process_start(raw):
    values = []
    for line in raw.splitlines():
        if line.startswith("process_start_time_seconds "):
            values.append(float(line.rsplit(" ", 1)[1]))
    if len(values) != 1 or values[0] <= 0:
        raise ValueError("missing_or_ambiguous_process_start")
    return values[0]


def exact_counter_delta(before, after, model, expected_prompt, expected_output, expected_responses_received):
    if process_start(before) != process_start(after):
        raise ValueError("engine_restart_or_counter_reset")
    checks = (
        ("vllm:prompt_tokens_total", expected_prompt),
        ("vllm:generation_tokens_total", expected_output),
        ("vllm:request_success_total", expected_responses_received),
    )
    observed = {}
    for name, expected in checks:
        delta = engine_counter(after, model, name) - engine_counter(before, model, name)
        if delta < 0 or delta != expected:
            raise ValueError(f"unexpected_counter_delta_{name}")
        observed[name] = delta
    return observed


def gpu_sample():
    argv = ["nvidia-smi", "--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu,power.draw,temperature.gpu", "--format=csv,noheader,nounits"]
    result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=10)
    rows = []
    for line in result.stdout.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 9:
            raise ValueError("gpu_field_count")
        rows.append({"index": int(fields[0]), "uuid": fields[1], "name": fields[2], "memory_total_mib": int(fields[3]), "memory_used_mib": int(fields[4]), "memory_free_mib": int(fields[5]), "utilization_gpu_percent": int(fields[6]), "power_draw_w": float(fields[7]), "temperature_gpu_c": int(fields[8])})
    if result.returncode or tuple(row["uuid"] for row in rows) != _EXPECTED_GPU_UUIDS or tuple(row["index"] for row in rows) != (0, 1):
        raise ValueError("gpu_sample_invalid")
    for row in rows:
        if row["memory_total_mib"] <= 0 or row["memory_used_mib"] < 0 or row["memory_free_mib"] < 0 or not 0 <= row["utilization_gpu_percent"] <= 100 or not math.isfinite(row["power_draw_w"]) or row["temperature_gpu_c"] < 0:
            raise ValueError("gpu_sample_out_of_range")
    return {"argv": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "rows": rows}


def host_memory():
    selected = {}
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            amount, unit = value.split()
            if unit != "kB":
                raise ValueError("host_memory_unit")
            selected[key] = {"value": int(amount), "unit": unit}
    if set(selected) != {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
        raise ValueError("host_memory_fields_missing")
    if any(item["value"] < 0 for item in selected.values()) or selected["MemAvailable"]["value"] > selected["MemTotal"]["value"] or selected["SwapFree"]["value"] > selected["SwapTotal"]["value"]:
        raise ValueError("host_memory_range")
    return selected


def status_sample(argv, expected):
    result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=60)
    envelope = json.loads(result.stdout)
    if result.returncode or envelope.get("ok") is not True:
        raise ValueError("recipe_status_failed")
    detail = json.loads(envelope["data"])
    actual = {key: detail.get(key) for key in ("container", "container_id", "model", "revision", "image", "running", "state")}
    for key in ("container", "container_id", "model", "revision", "image"):
        if actual[key] != expected[key]:
            raise ValueError(f"identity_{key}_mismatch")
    if actual["running"] is not True or actual["state"] != "running":
        raise ValueError("recipe_not_running")
    memory = detail.get("host_memory")
    numeric = ("current_bytes", "peak_bytes", "limit_bytes", "effective_limit_bytes", "effective_swap_limit_bytes", "ram_plus_swap_limit_bytes")
    if not isinstance(memory, dict) or any(type(memory.get(key)) is not int or memory[key] < 0 for key in numeric):
        raise ValueError("cgroup_memory_invalid")
    events = memory.get("events")
    if not isinstance(events, dict) or not events or any(type(value) is not int or value < 0 for value in events.values()):
        raise ValueError("cgroup_events_invalid")
    if type(memory.get("oom_killed")) is not bool:
        raise ValueError("cgroup_oom_invalid")
    return {"argv": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "identity": actual, "host_memory": memory}


def self_test():
    model = "fixture"
    idle = '\n'.join((
        'vllm:num_requests_running{engine="0",model_name="fixture"} 0.0',
        'vllm:num_requests_waiting{engine="0",model_name="fixture"} 0.0',
        'vllm:kv_cache_usage_perc{engine="0",model_name="fixture"} 0.0',
    ))
    loaded = idle.replace('running{engine="0",model_name="fixture"} 0.0', 'running{engine="0",model_name="fixture"} 3.0')
    assert engine_state(idle, model)["running"] == 0.0
    assert engine_state(loaded, model)["running"] == 3.0
    try:
        engine_state(idle.replace('engine="0"', 'engine="1"'), model)
    except ValueError:
        return
    raise AssertionError("missing engine zero must fail")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"collector_self_test": "passed"}))
