"""Offline reconciliation of one completed Flare arm; no inference operations."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def parse(raw):
    result = {}
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        key, value = line.rsplit(" ", 1)
        if key in result:
            raise ValueError("duplicate metric")
        result[key] = float(value)
    return result


def analyze(root, arm):
    rows = []
    for concurrency in (1, 4):
        for repetition in ("prime", "r1", "r2", "r3"):
            name = f"{arm}-c{concurrency}-{repetition}"
            sources = [root / (name + suffix) for suffix in
                       (".json", "-before-native.prom", "-after-native.prom")]
            run = json.loads(sources[0].read_text())
            before, after = (parse(p.read_text()) for p in sources[1:])
            delta = {k: after[k] - v for k, v in before.items()
                     if k.startswith("flare_") and k.split("{")[0].endswith("_total") and k in after}
            if not all(math.isfinite(v) and v >= 0 for v in delta.values()):
                raise ValueError("counter reset or nonfinite delta")
            if run["completed"] + run["failed"] != run["requests"]:
                raise ValueError("request denominator mismatch")
            if delta["flare_requests_completed_total"] != run["requests"]:
                raise ValueError("native completed-request mismatch")
            # Completion counters can include a contract-invalid HTTP success.
            if not run["failed"]:
                if delta["flare_generated_tokens_total"] != run["metrics"]["output_tokens"]:
                    raise ValueError("generated-token mismatch")
                if run["metrics"]["prompt_tokens"] != delta["flare_prefill_tokens_total"] + delta["flare_cache_reused_tokens_total"]:
                    raise ValueError("prompt-token mismatch")
            shapes = []
            for key, rounds in delta.items():
                if not key.startswith("flare_speculative_rounds_total{") or not rounds:
                    continue
                labels = key[key.index("{"):]
                ns = delta["flare_speculative_gpu_nanoseconds_total" + labels]
                offered = delta["flare_speculative_offered_drafts_total" + labels]
                accepted = delta["flare_speculative_accepted_drafts_total" + labels]
                emitted = delta["flare_speculative_emitted_tokens_total" + labels]
                shapes.append({"labels": labels, "rounds": rounds,
                               "max_rank_event_ms_per_round": ns / rounds / 1e6,
                               "max_rank_event_seconds": ns / 1e9,
                               "raw_emitted_per_round": emitted / rounds,
                               "raw_accepted_fraction": accepted / offered if offered else None})
            resident = after["flare_host_cache_memory_bytes"]
            rows.append({"name": name, "concurrency": concurrency, "repetition": repetition,
                         "requests": run["requests"], "failed": run["failed"],
                         "deltas": delta, "shapes": shapes,
                         "resident_records": after["flare_host_cache_memory_records"],
                         "resident_bytes": resident, "resident_GiB": resident / 2**30,
                         "configured_budget_GiB": 32,
                         "source_files": [{"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sources]})
    return {"arm": arm, "rows": rows,
            "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "limits": ["Native counters reconcile requests; no semantic correctness claim",
                       "GPU snapshot evictions are not host eviction counts",
                       "Resident bytes are not free page/row counts",
                       "Raw speculative emission/acceptance may include terminal or cancelled lanes",
                       "Per-graph maximum-rank CUDA event intervals are not whole-device utilization or summed-rank critical path",
                       "Runtime/source identity must be joined from recipe, build and incarnation receipts"]}


if __name__ == "__main__":
    assert parse('# HELP x doc\nx{a="1"} 12\n') == {'x{a="1"}': 12.0}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arm", choices=["flare-control", "flare-instrumented"])
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root, args.arm)
    with args.output.open("x") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"arm": args.arm, "blocks": len(result["rows"])}))
