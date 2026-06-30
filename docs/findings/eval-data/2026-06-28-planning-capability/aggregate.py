"""Aggregate the planning-capability eval: join generation stats + structural
grades + de-anonymized blind-judge scores into machine-readable CSV/JSON.
"""
import json, os, csv, glob, statistics as st

OUT = os.path.dirname(os.path.abspath(__file__))
# Committed eval artifacts live in subdirs; grading/ holds the json/csv.
GRADING = os.path.join(OUT, "grading")
def load(p):
    with open(os.path.join(GRADING, p), encoding="utf-8") as f: return json.load(f)

gen = {(r["prd"], r["model"]): r for r in load("gen_manifest.json")}
struct = {(r["prd"], r["model"]): r for r in load("grade_struct.json")}
anon = load("anon_map.json")                      # {pid: {model: letter}}
inv = {pid: {letter: model for model, letter in m.items()} for pid, m in anon.items()}

DIMS = ["decomposition_granularity","requirement_coverage","dependency_correctness",
        "acceptance_verifiability","faithfulness"]

# de-anonymize judge files -> list of {prd, judge, model, scores..., total}
judge_rows = []
for jf in sorted(glob.glob(os.path.join(GRADING, "judge_*.json"))):
    j = json.load(open(jf, encoding="utf-8"))
    pid = j["prd"]; jn = j["judge"]
    for letter, c in j["candidates"].items():
        model = inv[pid][letter]
        row = {"prd": pid, "judge": jn, "model": model, "letter": letter,
               **{d: c["scores"][d] for d in DIMS}, "total": c["total"]}
        judge_rows.append(row)

# per (prd, model) judge averages
def avg(xs): return round(sum(xs)/len(xs), 3)
pm = {}
for r in judge_rows:
    k = (r["prd"], r["model"]); pm.setdefault(k, []).append(r)
judge_agg = {}
for k, rows in pm.items():
    judge_agg[k] = {d: avg([r[d] for r in rows]) for d in DIMS}
    judge_agg[k]["total_avg"] = avg([r["total"] for r in rows])
    judge_agg[k]["total_min"] = min(r["total"] for r in rows)
    judge_agg[k]["total_max"] = max(r["total"] for r in rows)

# per model, across both PRDs
model_agg = {}
for model in ["frontier","heavy","fast"]:
    rs = [r for r in judge_rows if r["model"]==model]
    model_agg[model] = {d: avg([r[d] for r in rs]) for d in DIMS}
    model_agg[model]["total_avg_of_25"] = avg([r["total"] for r in rs])
    model_agg[model]["pct_of_frontier"] = None  # fill below

fr = model_agg["frontier"]["total_avg_of_25"]
for model in model_agg:
    model_agg[model]["pct_of_frontier"] = round(100*model_agg[model]["total_avg_of_25"]/fr, 1)

# ---- write metrics_long.csv (one row per prd x model) ----
fields = ["prd","model","model_id","gen_ok","elapsed_s","completion_tokens","tok_per_s","out_chars",
          "n_tasks","structural_score","features_covered","n_dep_edges","has_dep_cycle","dangling_deps",
          "uncovered_features"] + ["judge_"+d for d in DIMS] + ["judge_total_avg_of_25","judge_total_min","judge_total_max"]
with open(os.path.join(GRADING,"metrics_long.csv"),"w",newline="",encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
    for (pid, model), g in gen.items():
        s = struct[(pid,model)]; ja = judge_agg[(pid,model)]
        w.writerow({
            "prd":pid,"model":model,"model_id":g.get("model_id"),"gen_ok":g.get("ok"),
            "elapsed_s":g.get("elapsed_s"),"completion_tokens":g.get("completion_tokens"),
            "tok_per_s":g.get("tok_per_s"),"out_chars":g.get("chars"),
            "n_tasks":s["n_tasks"],"structural_score":s["structural_score"],
            "features_covered":s["features_covered"],"n_dep_edges":s["n_dep_edges"],
            "has_dep_cycle":s["has_dep_cycle"],"dangling_deps":len(s["dangling_deps"]),
            "uncovered_features":";".join(s["uncovered_features"]),
            **{"judge_"+d:ja[d] for d in DIMS},
            "judge_total_avg_of_25":ja["total_avg"],"judge_total_min":ja["total_min"],"judge_total_max":ja["total_max"],
        })

# ---- write judge_dimensions_long.csv (prd x model x judge x dimension) ----
with open(os.path.join(GRADING,"judge_dimensions_long.csv"),"w",newline="",encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["prd","model","judge","dimension","score"])
    for r in judge_rows:
        for d in DIMS: w.writerow([r["prd"],r["model"],r["judge"],d,r[d]])

# ---- inter-judge agreement: identical rankings? per-PRD ----
agreement = {}
for pid in anon:
    per_judge_rank = {}
    for r in judge_rows:
        if r["prd"]!=pid: continue
        per_judge_rank.setdefault(r["judge"], []).append((r["total"], r["model"]))
    ranks = {jn: [m for _,m in sorted(v, reverse=True)] for jn,v in per_judge_rank.items()}
    agreement[pid] = {"judge_rankings": ranks, "identical": len(set(map(tuple, ranks.values())))==1}

bundle = {"per_prd_model_judge_avg": {f"{k[0]}|{k[1]}": v for k,v in judge_agg.items()},
          "per_model_overall": model_agg, "inter_judge_agreement": agreement,
          "anon_map": anon, "raw_judge_rows": judge_rows}
with open(os.path.join(GRADING,"aggregate.json"),"w",encoding="utf-8") as f:
    json.dump(bundle, f, indent=2)

# ---- console summary ----
print("=== PER-MODEL OVERALL (avg of 4 judge scores: 2 PRDs x 2 judges) ===")
print(f"{'model':9} {'DG':>4} {'RC':>4} {'DC':>4} {'AV':>4} {'FA':>4} {'TOTAL/25':>9} {'%frontier':>9}")
for m in ["frontier","fast","heavy"]:
    a=model_agg[m]
    print(f"{m:9} {a['decomposition_granularity']:>4} {a['requirement_coverage']:>4} {a['dependency_correctness']:>4} {a['acceptance_verifiability']:>4} {a['faithfulness']:>4} {a['total_avg_of_25']:>9} {a['pct_of_frontier']:>8}%")
print("\n=== PER PRD x MODEL (judge total, avg of 2 judges, min-max) ===")
for pid in anon:
    for m in ["frontier","fast","heavy"]:
        a=judge_agg[(pid,m)]
        print(f"  {pid:14} {m:9} total={a['total_avg']:>5}/25  (range {a['total_min']}-{a['total_max']})  DC={a['dependency_correctness']}")
print("\n=== INTER-JUDGE AGREEMENT ===")
for pid,a in agreement.items():
    print(f"  {pid}: identical_ranking={a['identical']}  rankings={a['judge_rankings']}")
print("\nWrote: metrics_long.csv, judge_dimensions_long.csv, aggregate.json")
