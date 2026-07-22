# ruff: noqa: E701, E702
"""Aggregate the planning-capability eval: join generation stats + structural
grades + de-anonymized blind-judge scores into machine-readable CSV/JSON.
"""

import json
import os
import csv
import glob
import hashlib

OUT = os.path.dirname(os.path.abspath(__file__))
# Committed eval artifacts live in subdirs; grading/ holds the json/csv.
GRADING = os.path.join(OUT, "grading")
OUTPUTS = os.path.join(OUT, "outputs")


def load(p):
    with open(os.path.join(GRADING, p), encoding="utf-8") as f:
        return json.load(f)


PRDS = {"prdA-backlog", "prdB-multiprd"}
MODELS = {"frontier", "heavy", "fast"}
LOCAL_MODELS = {"heavy", "fast"}


def fail(message):
    raise SystemExit(f"evidence binding failed: {message}")


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


output_binding = load("output_sha256.json")
expected_output_files = {f"out_{prd}__{model}.md" for prd in PRDS for model in MODELS}
if set(output_binding) != expected_output_files:
    fail("output_sha256.json does not name exactly the six judged outputs")
for filename, expected_hash in output_binding.items():
    actual_hash = file_sha256(os.path.join(OUTPUTS, filename))
    if actual_hash != expected_hash:
        fail(
            f"{filename} changed ({actual_hash} != {expected_hash}); rerun and bind independent judging"
        )

gen_rows = load("gen_manifest.json")
expected_gen_keys = {(prd, model) for prd in PRDS for model in LOCAL_MODELS}
gen = {(r["prd"], r["model"]): r for r in gen_rows}
if len(gen) != len(gen_rows) or set(gen) != expected_gen_keys:
    fail("gen_manifest.json does not contain exactly one row per PRD and local model")
for (prd, model), row in gen.items():
    if row.get("ok") is not True:
        fail(f"generation is not successful for {prd}/{model}")
    if row.get("file") != f"out_{prd}__{model}.md":
        fail(f"generation filename is inconsistent for {prd}/{model}")

struct_rows = load("grade_struct.json")
struct = {(r["prd"], r["model"]): r for r in struct_rows}
expected_struct_keys = {(prd, model) for prd in PRDS for model in MODELS}
if len(struct) != len(struct_rows) or set(struct) != expected_struct_keys:
    fail("grade_struct.json does not contain exactly one row per PRD and model")

anon = load("anon_map.json")  # {pid: {model: letter}}
if set(anon) != PRDS:
    fail("anon_map.json does not contain exactly the two evaluated PRDs")
for prd, model_map in anon.items():
    if set(model_map) != MODELS or len(set(model_map.values())) != len(MODELS):
        fail(f"anon_map.json is incomplete or ambiguous for {prd}")
inv = {pid: {letter: model for model, letter in m.items()} for pid, m in anon.items()}

DIMS = [
    "decomposition_granularity",
    "requirement_coverage",
    "dependency_correctness",
    "acceptance_verifiability",
    "faithfulness",
]

# de-anonymize judge files -> list of {prd, judge, model, scores..., total}
judge_rows = []
judge_files = sorted(glob.glob(os.path.join(GRADING, "judge_*.json")))
if len(judge_files) != 4:
    fail("expected exactly four blind-judge files")
for jf in judge_files:
    j = json.load(open(jf, encoding="utf-8"))
    pid = j["prd"]
    jn = j["judge"]
    if pid not in PRDS or jn not in {1, 2}:
        fail(f"unexpected judge identity in {os.path.basename(jf)}")
    if set(j["candidates"]) != set(inv[pid]):
        fail(f"judge candidates do not match the anonymization map in {os.path.basename(jf)}")
    for letter, c in j["candidates"].items():
        if set(c.get("scores", {})) != set(DIMS):
            fail(f"judge dimensions are incomplete in {os.path.basename(jf)}/{letter}")
        scores = c["scores"]
        if any(type(scores[d]) is not int or not 1 <= scores[d] <= 5 for d in DIMS):
            fail(f"judge score is outside the integer 1-5 range in {os.path.basename(jf)}/{letter}")
        if type(c.get("total")) is not int or c["total"] != sum(scores[d] for d in DIMS):
            fail(
                f"judge total does not equal its five dimensions in {os.path.basename(jf)}/{letter}"
            )
        model = inv[pid][letter]
        row = {
            "prd": pid,
            "judge": jn,
            "model": model,
            "letter": letter,
            **{d: scores[d] for d in DIMS},
            "total": c["total"],
        }
        judge_rows.append(row)

if len(judge_rows) != 12:
    fail("expected twelve de-anonymized judge rows")
for prd in PRDS:
    for model in MODELS:
        rows = [r for r in judge_rows if r["prd"] == prd and r["model"] == model]
        if {r["judge"] for r in rows} != {1, 2} or len(rows) != 2:
            fail(f"missing or duplicate judge row for {prd}/{model}")


# per (prd, model) judge averages
def avg(xs):
    return round(sum(xs) / len(xs), 3)


pm = {}
for r in judge_rows:
    k = (r["prd"], r["model"])
    pm.setdefault(k, []).append(r)
judge_agg = {}
for k, rows in pm.items():
    judge_agg[k] = {d: avg([r[d] for r in rows]) for d in DIMS}
    judge_agg[k]["total_avg"] = avg([r["total"] for r in rows])
    judge_agg[k]["total_min"] = min(r["total"] for r in rows)
    judge_agg[k]["total_max"] = max(r["total"] for r in rows)

# per model, across both PRDs
model_agg = {}
for model in ["frontier", "heavy", "fast"]:
    rs = [r for r in judge_rows if r["model"] == model]
    model_agg[model] = {d: avg([r[d] for r in rs]) for d in DIMS}
    model_agg[model]["total_avg_of_25"] = avg([r["total"] for r in rs])
    model_agg[model]["pct_of_frontier"] = None  # fill below

fr = model_agg["frontier"]["total_avg_of_25"]
for model in model_agg:
    model_agg[model]["pct_of_frontier"] = round(100 * model_agg[model]["total_avg_of_25"] / fr, 1)

# ---- write metrics_long.csv (one row per prd x model) ----
fields = (
    [
        "prd",
        "model",
        "model_id",
        "gen_ok",
        "elapsed_s",
        "completion_tokens",
        "tok_per_s",
        "out_chars",
        "n_tasks",
        "structural_score",
        "features_covered",
        "n_dep_edges",
        "has_dep_cycle",
        "dangling_deps",
        "uncovered_features",
    ]
    + ["judge_" + d for d in DIMS]
    + ["judge_total_avg_of_25", "judge_total_min", "judge_total_max"]
)
with open(os.path.join(GRADING, "metrics_long.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    for (pid, model), g in gen.items():
        s = struct[(pid, model)]
        ja = judge_agg[(pid, model)]
        w.writerow(
            {
                "prd": pid,
                "model": model,
                "model_id": g.get("model_id"),
                "gen_ok": g.get("ok"),
                "elapsed_s": g.get("elapsed_s"),
                "completion_tokens": g.get("completion_tokens"),
                "tok_per_s": g.get("tok_per_s"),
                "out_chars": g.get("chars"),
                "n_tasks": s["n_tasks"],
                "structural_score": s["structural_score"],
                "features_covered": s["features_covered"],
                "n_dep_edges": s["n_dep_edges"],
                "has_dep_cycle": s["has_dep_cycle"],
                "dangling_deps": len(s["dangling_deps"]),
                "uncovered_features": ";".join(s["uncovered_features"]),
                **{"judge_" + d: ja[d] for d in DIMS},
                "judge_total_avg_of_25": ja["total_avg"],
                "judge_total_min": ja["total_min"],
                "judge_total_max": ja["total_max"],
            }
        )

# ---- write judge_dimensions_long.csv (prd x model x judge x dimension) ----
with open(
    os.path.join(GRADING, "judge_dimensions_long.csv"), "w", newline="", encoding="utf-8"
) as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["prd", "model", "judge", "dimension", "score"])
    for r in judge_rows:
        for d in DIMS:
            w.writerow([r["prd"], r["model"], r["judge"], d, r[d]])

# ---- inter-judge agreement: identical rankings? per-PRD ----
agreement = {}
for pid in anon:
    per_judge_rank = {}
    for r in judge_rows:
        if r["prd"] != pid:
            continue
        per_judge_rank.setdefault(r["judge"], []).append((r["total"], r["model"]))
    ranks = {jn: [m for _, m in sorted(v, reverse=True)] for jn, v in per_judge_rank.items()}
    agreement[pid] = {
        "judge_rankings": ranks,
        "identical": len(set(map(tuple, ranks.values()))) == 1,
    }

bundle = {
    "per_prd_model_judge_avg": {f"{k[0]}|{k[1]}": v for k, v in judge_agg.items()},
    "per_model_overall": model_agg,
    "inter_judge_agreement": agreement,
    "anon_map": anon,
    "raw_judge_rows": judge_rows,
}
with open(os.path.join(GRADING, "aggregate.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(bundle, f, indent=2)

# ---- console summary ----
print("=== PER-MODEL OVERALL (avg of 4 judge scores: 2 PRDs x 2 judges) ===")
print(
    f"{'model':9} {'DG':>4} {'RC':>4} {'DC':>4} {'AV':>4} {'FA':>4} {'TOTAL/25':>9} {'%frontier':>9}"
)
for m in ["frontier", "fast", "heavy"]:
    a = model_agg[m]
    print(
        f"{m:9} {a['decomposition_granularity']:>4} {a['requirement_coverage']:>4} {a['dependency_correctness']:>4} {a['acceptance_verifiability']:>4} {a['faithfulness']:>4} {a['total_avg_of_25']:>9} {a['pct_of_frontier']:>8}%"
    )
print("\n=== PER PRD x MODEL (judge total, avg of 2 judges, min-max) ===")
for pid in anon:
    for m in ["frontier", "fast", "heavy"]:
        a = judge_agg[(pid, m)]
        print(
            f"  {pid:14} {m:9} total={a['total_avg']:>5}/25  (range {a['total_min']}-{a['total_max']})  DC={a['dependency_correctness']}"
        )
print("\n=== INTER-JUDGE AGREEMENT ===")
for pid, a in agreement.items():
    print(f"  {pid}: identical_ranking={a['identical']}  rankings={a['judge_rankings']}")
print("\nWrote: metrics_long.csv, judge_dimensions_long.csv, aggregate.json")
