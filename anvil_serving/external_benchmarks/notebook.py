"""Bakeoff notebook — the repeatable form of the hand-written fast-tier report.

Turns persisted ``bakeoff_runs`` rows (see store.record_bakeoff_run) into a
comparable candidate matrix + rubric scores + a win/lose/hold verdict with a
recorded REASON. Pure and total, in the style of ``score.py``: no I/O, no
network, no clock — deterministic from the row dicts, so the ``_selfcheck``
below fully exercises it.

The rubric encodes the 100-point scale from
``docs/findings/2026-07-08-fast-tier-llm-bakeoff.md`` as DATA (like score.py's
ROLE_BENCH), so the weights live in one auditable place:

    Voice latency        30   (<=  BUDGET fast; degrades linearly to 2x budget)
    Intelligence + tool  30   (intelligence_pass_rate * 20  +  tool 10)
    Context              15   (usable_context_tokens vs a target)
    Agent reliability    15   (session recall 15)
    Ops / no-failures    10   (10 minus 5 per recorded failure, floored at 0)

Hard gates (a candidate that trips one cannot WIN regardless of score):
tool_call_passed, session_recall_passed, and no failures.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

# Rubric weights and the reference budgets they score against. Override per
# task by passing `targets=` to score_run (kept explicit, never hidden).
RUBRIC = {
    "voice": 30,
    "intelligence_tool": 30,
    "context": 15,
    "agent": 15,
    "ops": 10,
}
DEFAULT_TARGETS = {
    "voice_budget_ms": 1200.0,   # <= budget scores full voice points
    "context_target_tokens": 65536,
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def score_run(run: Mapping[str, Any], *, targets: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A single bakeoff row -> {per-category points, total, gates}. Total.

    A missing metric scores 0 for its category (an unmeasured candidate does
    not get free points) — never raises."""
    t = {**DEFAULT_TARGETS, **(dict(targets) if targets else {})}

    # Voice: full points at/under budget, linear to 0 at 2x budget.
    voice_ms = run.get("voice_latency_ms")
    if voice_ms is None:
        voice = 0.0
    else:
        budget = float(t["voice_budget_ms"])
        frac = _clamp((2 * budget - float(voice_ms)) / budget, 0.0, 1.0)
        voice = RUBRIC["voice"] * frac

    # Intelligence (0-1 rate -> 20 pts) + tool pass (10 pts).
    ipr = run.get("intelligence_pass_rate")
    intel = (float(ipr) * 20.0) if ipr is not None else 0.0
    tool = 10.0 if run.get("tool_call_passed") else 0.0
    intelligence_tool = intel + tool

    # Context: usable tokens vs target, capped at full points.
    uct = run.get("usable_context_tokens")
    if uct is None:
        context = 0.0
    else:
        context = RUBRIC["context"] * _clamp(
            float(uct) / float(t["context_target_tokens"]), 0.0, 1.0
        )

    agent = 15.0 if run.get("session_recall_passed") else 0.0

    failures = run.get("failures_json")
    n_fail = _count_failures(failures)
    ops = max(0.0, RUBRIC["ops"] - 5.0 * n_fail)

    total = round(voice + intelligence_tool + context + agent + ops, 2)
    gates = {
        "tool_call_passed": bool(run.get("tool_call_passed")),
        "session_recall_passed": bool(run.get("session_recall_passed")),
        "no_failures": n_fail == 0,
    }
    return {
        "voice": round(voice, 2),
        "intelligence_tool": round(intelligence_tool, 2),
        "context": round(context, 2),
        "agent": round(agent, 2),
        "ops": round(ops, 2),
        "total": total,
        "gates": gates,
        "gates_passed": all(gates.values()),
    }


def _count_failures(failures_json: Any) -> int:
    import json as _json

    if failures_json in (None, "", "[]"):
        return 0
    if isinstance(failures_json, (list, tuple)):
        return len(failures_json)
    try:
        parsed = _json.loads(failures_json)
        return len(parsed) if isinstance(parsed, (list, tuple)) else 0
    except (ValueError, TypeError):
        return 0


def verdict(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any] | None = None,
    *,
    targets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """win | lose | hold for a candidate, optionally vs a baseline run.

    - A candidate that fails any HARD GATE is ``lose`` (with the gate named).
    - With no baseline: ``win`` if all gates pass and total >= 70 (the report's
      pass line), else ``hold``.
    - With a baseline: ``win`` if gates pass AND total beats the baseline's;
      ``lose`` if it fails a gate or scores below baseline; ``hold`` on a tie.
    The REASON string is the recorded "why it won/lost".
    """
    c = score_run(candidate, targets=targets)
    if not c["gates_passed"]:
        failed = [k for k, v in c["gates"].items() if not v]
        return {
            "result": "lose",
            "total": c["total"],
            "reason": "failed hard gate(s): " + ", ".join(failed),
            "rubric": c,
        }

    if baseline is None:
        if c["total"] >= 70:
            return {"result": "win", "total": c["total"],
                    "reason": f"all gates pass; score {c['total']} >= 70 pass line",
                    "rubric": c}
        return {"result": "hold", "total": c["total"],
                "reason": f"gates pass but score {c['total']} < 70 pass line",
                "rubric": c}

    b = score_run(baseline, targets=targets)
    if c["total"] > b["total"]:
        return {"result": "win", "total": c["total"],
                "reason": (f"gates pass; score {c['total']} beats baseline "
                           f"{baseline.get('candidate_id')} ({b['total']})"),
                "rubric": c}
    if c["total"] < b["total"]:
        return {"result": "lose", "total": c["total"],
                "reason": (f"score {c['total']} below baseline "
                           f"{baseline.get('candidate_id')} ({b['total']})"),
                "rubric": c}
    return {"result": "hold", "total": c["total"],
            "reason": f"tie with baseline at {c['total']}", "rubric": c}


def render_markdown(
    runs: Sequence[Mapping[str, Any]],
    *,
    task: str | None = None,
    hardware: str | None = None,
    baseline_candidate: str | None = None,
    targets: Mapping[str, Any] | None = None,
) -> str:
    """The t006-style matrix + rubric table + determination block, repeatable."""
    lines: list[str] = []
    title = "# Fast-tier bakeoff notebook"
    scope = ", ".join(x for x in (task and f"task={task}", hardware and f"hardware={hardware}") if x)
    lines.append(title + (f" ({scope})" if scope else ""))
    lines.append("")
    if not runs:
        lines.append("_No recorded bakeoff runs for this scope._")
        return "\n".join(lines) + "\n"

    baseline = next((r for r in runs if r.get("candidate_id") == baseline_candidate), None) \
        if baseline_candidate else None

    lines.append("## Candidate matrix")
    lines.append("")
    lines.append("| Candidate | Config | TTFT p50 | E2E p50 | Voice ms | Ctx tok | Tool | Session | Intel | Fails |")
    lines.append("|---|---|--:|--:|--:|--:|:-:|:-:|--:|--:|")
    for r in runs:
        lines.append(
            "| {cand} | {cfg} | {ttft} | {e2e} | {voice} | {ctx} | {tool} | {sess} | {ipr} | {fails} |".format(
                cand=r.get("candidate_id", "?"),
                cfg=r.get("config_id", "?"),
                ttft=_fmt(r.get("ttft_p50_ms")),
                e2e=_fmt(r.get("e2e_p50_ms")),
                voice=_fmt(r.get("voice_latency_ms")),
                ctx=_fmt(r.get("usable_context_tokens")),
                tool="PASS" if r.get("tool_call_passed") else "fail",
                sess="PASS" if r.get("session_recall_passed") else "fail",
                ipr=_fmt(r.get("intelligence_pass_rate")),
                fails=_count_failures(r.get("failures_json")),
            )
        )
    lines.append("")

    lines.append("## Rubric (out of 100)")
    lines.append("")
    lines.append("| Candidate | Voice/30 | Intel+Tool/30 | Context/15 | Agent/15 | Ops/10 | Total | Gates |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|:-:|")
    scored = []
    for r in runs:
        s = score_run(r, targets=targets)
        scored.append((r, s))
        lines.append(
            "| {cand} | {v} | {it} | {c} | {a} | {o} | **{tot}** | {g} |".format(
                cand=r.get("candidate_id", "?"),
                v=s["voice"], it=s["intelligence_tool"], c=s["context"],
                a=s["agent"], o=s["ops"], tot=s["total"],
                g="OK" if s["gates_passed"] else "GATE",
            )
        )
    lines.append("")

    lines.append("## Determination")
    lines.append("")
    for r, s in sorted(scored, key=lambda x: x[1]["total"], reverse=True):
        v = verdict(r, baseline if baseline is not r else None, targets=targets)
        lines.append(f"- **{r.get('candidate_id')}** ({r.get('config_id')}): "
                     f"{v['result'].upper()} — {v['reason']}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.0f}" if value >= 100 else f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


# --------------------------------------------------------------------------- #
# self-check (invoked by tests; mirrors score.py's --self-check discipline)
# --------------------------------------------------------------------------- #
def _selfcheck() -> int:
    strong = {
        "candidate_id": "cand-a", "config_id": "cfg1", "task": "t", "hardware": "h",
        "voice_latency_ms": 800.0, "intelligence_pass_rate": 1.0,
        "tool_call_passed": 1, "session_recall_passed": 1,
        "usable_context_tokens": 65536, "failures_json": "[]",
        "ttft_p50_ms": 120.0, "e2e_p50_ms": 900.0,
    }
    weak = {
        **strong, "candidate_id": "cand-b",
        "voice_latency_ms": 2200.0, "intelligence_pass_rate": 0.5,
        "usable_context_tokens": 16384,
    }
    gated = {**strong, "candidate_id": "cand-c", "tool_call_passed": 0}
    failing = {**strong, "candidate_id": "cand-d", "failures_json": '["a","b"]'}

    s = score_run(strong)
    assert s["total"] == 100.0, s["total"]                 # perfect candidate
    assert s["gates_passed"] is True
    assert score_run(weak)["total"] < s["total"]
    assert verdict(strong)["result"] == "win"
    assert verdict(weak)["result"] == "hold"               # gates pass, low score
    assert verdict(gated)["result"] == "lose"              # hard-gate fail
    assert "tool_call_passed" in verdict(gated)["reason"]
    # failures dock ops points: 2 failures -> ops 0, and it's a gate fail too
    assert score_run(failing)["ops"] == 0.0
    assert verdict(failing)["result"] == "lose"
    # baseline comparison
    assert verdict(strong, weak)["result"] == "win"
    assert verdict(weak, strong)["result"] == "lose"
    # missing metrics never raise; every measured category scores 0. Only the
    # "no failures observed" ops floor (10) remains — and the candidate still
    # fails every hard gate, so its verdict is `lose` (the safe outcome).
    empty = {"candidate_id": "e", "config_id": "c", "task": "t", "hardware": "h"}
    es = score_run(empty)
    assert es["voice"] == es["intelligence_tool"] == es["context"] == es["agent"] == 0.0
    assert es["gates_passed"] is False
    assert verdict(empty)["result"] == "lose"
    # render is total and contains the sections
    md = render_markdown([strong, weak], task="t", hardware="h", baseline_candidate="cand-b")
    assert "Candidate matrix" in md and "Determination" in md and "cand-a" in md
    assert render_markdown([]).strip().endswith("_No recorded bakeoff runs for this scope._")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selfcheck())
