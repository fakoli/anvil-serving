"""Check the published arithmetic against retained per-run records."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def read(name):
    return json.loads((ROOT / name).read_text())

results, validity, summary = map(read, ("panel-results.json", "validity.json", "summary.json"))
assert len(results) == len(validity) == 24
assert sum(v["status"] == "valid" for v in validity) == 16
for arm in ("A", "B"):
    rows = [r for r, v in zip(results, validity) if r["arm"] == arm and v["status"] == "valid"]
    observed = summary["arms"][arm]
    assert len(rows) == observed["valid_tasks"] == 8
    assert sum(r["passed"] for r in rows) == observed["successful_tasks"]
    assert sum(r["truncations"] for r in rows) == observed["truncations"]
    assert sum(r["empty_final"] for r in rows) == observed["empty_finals"]
    assert sum(r["wall_seconds"] for r in rows) == observed["total_task_seconds"]
    assert sum(r["tool_error_results"] - r["expected_transient_errors"] for r in rows) == 0
upstream = {(r["case"], r["repetition"], r["arm"]): r["requests"] for r in read("upstream.json")}
for r, v in zip(results, validity):
    if v["status"] != "valid":
        continue
    requests = upstream[r["case"], r["repetition"], r["arm"]]
    assert all(q["max_tokens"] == r["effective_completion_tokens"] and q["reasoning_effort"] == "max" for q in requests)
    assert requests[0]["messages_sha256"] == upstream[r["case"], r["repetition"], "B" if r["arm"] == "A" else "A"][0]["messages_sha256"]
print("PASS: valid-run arithmetic and matched request controls")
