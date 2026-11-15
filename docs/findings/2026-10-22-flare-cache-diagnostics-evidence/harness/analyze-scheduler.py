"""Offline scheduler-trace arithmetic; no serving or network operations."""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None}
    return {"n": len(values), "min": values[0], "p50": median(values),
            "p95": values[math.ceil(.95 * len(values)) - 1], "max": values[-1]}


def measured_cost(sample):
    gpu = max(rank["gpu_ns"] for rank in sample["rank_gpu_ns"])
    return gpu if sample["queued"] else max(gpu, sample["wall_ns"])


def analyze(path):
    raw = path.read_bytes()
    envelope = json.loads(raw)
    trace = envelope["trace"]
    if not envelope["ok"] or not envelope["complete"]:
        raise ValueError("incomplete capture")
    if trace["dropped_updates"] != 0 or not trace["samples"]:
        raise ValueError("dropped or absent samples")
    graphs = {g["index"]: g for g in trace["graphs"]}
    groups = defaultdict(list)
    for sample in trace["samples"]:
        if [r["rank"] for r in sample["rank_gpu_ns"]] != [0, 1]:
            raise ValueError("invalid TP2 ranks")
        if sample["graph"] not in graphs:
            raise ValueError("unknown graph")
        if not trace["started_ns"] <= sample["observed_ns"] <= trace["ended_ns"]:
            raise ValueError("sample outside capture")
        groups[(sample["graph"], sample["queued"], len(sample["requests"]))].append(sample)
    results = []
    for (index, queued, lanes), samples in sorted(groups.items()):
        measures = defaultdict(list)
        for s in samples:
            selection = s["selection"]
            gpu = [r["gpu_ns"] for r in s["rank_gpu_ns"]]
            for rank in s["rank_gpu_ns"]:
                measures[f"rank_{rank['rank']}_gpu_ms"].append(rank["gpu_ns"] / 1e6)
            measures["max_rank_gpu_ms"].append(max(gpu) / 1e6)
            measures["wall_ms"].append(s["wall_ns"] / 1e6)
            measures["rank_interval_spread_ms"].append((max(gpu) - min(gpu)) / 1e6)
            measures["selection_to_commit_us"].append((selection["commit_ns"] - selection["selected_at_ns"]) / 1e3)
            measures["commit_to_observation_ms"].append((s["observed_ns"] - selection["commit_ns"]) / 1e6)
            if selection["observed_at_ns"] is not None:
                measures["prior_observation_age_ms"].append((selection["selected_at_ns"] - selection["observed_at_ns"]) / 1e6)
            measures["retained_shape_cost_ms"].append(selection["retained_ns"] / 1e6)
            if selection["retained_ns"] > 0:
                measures["measured_cost_over_retained"].append(measured_cost(s) / selection["retained_ns"])
        for optional in ("prior_observation_age_ms", "measured_cost_over_retained"):
            measures.setdefault(optional, [])
        results.append({"graph": graphs[index], "queued": queued, "physical_lanes": lanes,
                        "samples": len(samples), "metrics": {k: distribution(v) for k, v in measures.items()},
                        "missing_prior_observations": sum(s["selection"]["observed_at_ns"] is None for s in samples),
                        "nonpositive_retained_cost_samples": sum(s["selection"]["retained_ns"] <= 0 for s in samples),
                        "freshness": sorted({s["selection"]["freshness"] for s in samples}),
                        "origins": sorted({s["selection"]["origin"] for s in samples})})
    anchors = trace["clock_anchors"]
    return {"source": path.name, "sha256": hashlib.sha256(raw).hexdigest(),
            "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "complete": True, "samples": len(trace["samples"]),
            "duration_ns": trace["ended_ns"] - trace["started_ns"],
            "excluded_boundary_samples": trace["excluded_boundary_samples"],
            "anchor_bracket_ns": [a["after_ns"] - a["before_ns"] for a in anchors],
            "utc_offset_envelope_ns": [min(a["unix_ns"] - a["after_ns"] for a in anchors),
                                       max(a["unix_ns"] - a["before_ns"] for a in anchors)],
            "groups": results,
            "limits": ["One bounded observational window; no causal bottleneck or overhead claim",
                       "Rank intervals overlap and are not summed; interval spread is not synchronization wait",
                       "Wall minus GPU time is not pure CPU overhead",
                       "retained_ns is a previous shape-cost observation, not the submitted-plan estimate",
                       "Request handles are physical slots/generations, not HTTP request IDs",
                       "Graph dimensions are capacities, not executed token counts",
                       "Runtime/source identity must be joined from recipe, build and incarnation receipts"]}


if __name__ == "__main__":
    assert distribution([1, 2, 3, 4])["p95"] == 4
    fixture = {"rank_gpu_ns": [{"gpu_ns": 10}, {"gpu_ns": 12}], "wall_ns": 20, "queued": True}
    assert measured_cost(fixture) == 12
    assert measured_cost({**fixture, "queued": False}) == 20
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.input)
    with args.output.open("x") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"samples": result["samples"], "groups": len(result["groups"])}))
