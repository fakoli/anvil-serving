#!/usr/bin/env python3
"""cache_prune.py - PLAN (not execute) the pruning of the local HF model caches.

Scans the local caches (via _sync.discover, the real merged scanner), classifies
each model, and emits a PRUNE PLAN: removal candidates, each with the REASON it is
dropped and the reclaimable bytes, plus the disk total. This is PLAN-ONLY; deleting
is the separate T008 (each candidate carries local_path + dead_everywhere for it).

Two layers, split on testability:
  (1) classify_rows(rows, mixture) -> plan : the pure classification/protection/totals
      brain. Touches NO fs/GPU/network; reads only keys a model-row already carries.
      This is the seam the self-check drives with fixture dicts.
  (2) _default_scan() -> rows : thin glue over the REAL _sync local-only path
      (discover/newest_snapshot/load_json/detect_format/dir_size_gb + the merged
      sm120_hazard/_parse_quant servability verdict). NO fetch_card (network), NO
      card-file writes. Injectable so the self-check bypasses the filesystem.

NOTE: _sync.discover() returns (owner, repo, dir, kind) TUPLES, not id/format/size
rows -- _default_scan is the adapter that turns tuples into rows. The sm_120 verdict
is REUSED from _sync (sm120_hazard + _parse_quant are merged), not re-implemented.

Classification lesson (real prune this session): SGLang-unservable != deletable.
GGUF is llama.cpp-usable (a real fallback), so it gets its own tier and is NOT marked
dead_everywhere. Only FP8/FP4-MoE safetensors that hang post-load on this sm_120 box
(CLAUDE.md gotcha #3) are dead by every engine -> truly safe to delete.

Stdlib only; dicts/tables in-out.
"""
import os, sys, json, argparse


def classify_rows(rows, mixture):
    """Pure core: classify discover()-shaped rows into a prune plan dict.

    mixture: iterable of model ids to PROTECT (never list for removal, acceptance #2).
    No fs/GPU/network. Each row: {id, format, size_gb, sm120_caveat, ...}; sglang_loadable
    is derived from format+sm120_caveat if absent so fixtures stay tiny.
    """
    mixture = set(mixture or [])
    candidates, protected, by_reason = [], [], {}
    for r in rows:
        rid = r.get("id")
        # Mixture protection is checked FIRST and is ABSOLUTE (acceptance #2): a
        # selected model is never a candidate, regardless of incompatibility.
        if rid in mixture:
            protected.append(rid)
            continue
        fmt = r.get("format", "?")
        caveat = r.get("sm120_caveat")
        # Exactly one reason, by precedence. GGUF is tested FIRST: it has a real
        # llama.cpp fallback and is NEVER dead_everywhere -- a stray sm120 caveat
        # (which only ever applies to safetensors) must not promote a GGUF row to
        # the deletable incompatible-sm120 tier.
        if fmt == "GGUF":
            reason, dead = "incompatible-gguf", False    # llama.cpp fallback exists
        elif caveat and fmt == "safetensors":
            reason, dead = "incompatible-sm120", True     # dead by every engine here
        else:
            reason, dead = "purposeless", False           # servable, simply unused
        size_gb = float(r.get("size_gb") or 0.0)
        # True byte total when the scanner carried it; else fall back to the
        # 0.1GB-rounded display size (fixtures stay tiny).
        sb = r.get("size_bytes")
        rbytes = int(sb) if sb is not None else round(size_gb * 1e9)
        candidates.append({
            "id": rid, "reason": reason, "format": fmt, "size_gb": size_gb,
            "reclaimable_bytes": rbytes, "dead_everywhere": dead,
            "local_path": r.get("local_path"),
        })
        b = by_reason.setdefault(reason, {"count": 0, "bytes": 0})
        b["count"] += 1
        b["bytes"] += rbytes
    candidates.sort(key=lambda c: (-c["size_gb"], c["id"]))   # deterministic
    total_bytes = sum(c["reclaimable_bytes"] for c in candidates)
    return {
        "candidates": candidates,
        "protected": protected,
        "by_reason": by_reason,
        "total_reclaimable_gb": round(total_bytes / 1e9, 1),
        "total_reclaimable_bytes": total_bytes,
    }


def _default_scan():
    """Adapt the REAL _sync local-only path into classifier rows (no network)."""
    from . import _sync
    rows = []
    for owner, repo, d, kind in _sync.discover():
        snap = _sync.newest_snapshot(d) if kind == "hf" else d
        cfg = _sync.load_json(os.path.join(snap, "config.json")) if snap else {}
        fmt = _sync.detect_format(snap) if snap else "?"
        size_bytes = _sync.dir_size_bytes(d)     # exact bytes for reclaim totals
        size_gb = round(size_bytes / 1e9, 1)     # rounded for display only
        rid = f"{owner}/{repo}" if owner else repo
        # Servability verdict REUSED from _sync (merged sm120 logic), not re-derived.
        _qm, _qb, qsig = _sync._parse_quant(cfg)
        txt = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else {}
        caveat = _sync.sm120_hazard(fmt, qsig, cfg, txt or {})
        row = {
            "id": rid, "owner": owner, "model_type": cfg.get("model_type"),
            "format": fmt, "size_gb": size_gb, "size_bytes": size_bytes,
            "sm120_caveat": caveat,
            "sglang_loadable": (fmt == "safetensors" and not caveat),
            "local_path": d,
        }
        # SHARED 3-part real-model gate (identical to _sync.write_index): a non-model
        # dir (no weights, no model_type) must NEVER reach the prune planner, where
        # T008 would delete it by local_path. model_type is carried above so the
        # decisive positive gate can apply.
        if not _sync.is_real_model_row(row):
            continue
        rows.append(row)
    return rows


def build_plan(mixture, rows=None, scan=_default_scan):
    """Plan over `rows` if given, else `scan()`; then the pure classifier."""
    return classify_rows(rows if rows is not None else scan(), mixture)


def _render(plan, mixture):
    lines = ["PRUNE PLAN (plan only; execute is T008)", ""]
    lines.append("| id | format | size_gb | reason | dead? |")
    lines.append("|---|---|---|---|---|")
    for c in plan["candidates"]:
        lines.append("| {id} | {fmt} | {sz} | {rs} | {d} |".format(
            id=c["id"], fmt=c["format"], sz=c["size_gb"], rs=c["reason"],
            d=("yes" if c["dead_everywhere"] else "no")))
    lines.append("")
    for reason, b in sorted(plan["by_reason"].items()):
        lines.append(f"  {reason}: {b['count']} model(s), {round(b['bytes']/1e9, 1)} GB")
    lines.append(f"TOTAL reclaimable: {plan['total_reclaimable_gb']} GB "
                 f"({plan['total_reclaimable_bytes']} bytes)")
    # Report what was ACTUALLY matched-and-protected, not the requested set: a
    # requested id absent from the scan is NOT protected, and printing it as such
    # would be false safety. Surface any such mismatch loudly.
    lines.append(f"protected (matched in scan, never removed): {sorted(plan['protected'])}")
    unmatched = sorted(set(mixture) - set(plan["protected"]))
    if unmatched:
        lines.append(f"WARNING: requested protect id(s) NOT found in scan -- "
                     f"NOT protected: {unmatched}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="anvil_serving.cache_prune",
                                 description="Plan (not execute) pruning of local HF model caches.")
    ap.add_argument("--mixture", default="",
                    help="comma-separated model ids to PROTECT (never removed). "
                         "Empty => nothing protected (an explicit, echoed choice).")
    ap.add_argument("--json", action="store_true", help="emit the plan dict as JSON")
    ap.add_argument("--self-check", action="store_true",
                    help="run the internal self-check (no fs/GPU/network), print "
                         "'self-check OK', and exit 0")
    args = ap.parse_args(argv)
    if args.self_check:
        _selfcheck()
        print("self-check OK")
        return 0
    mixture = {m.strip() for m in args.mixture.split(",") if m.strip()}
    plan = build_plan(mixture)
    if args.json:
        print(json.dumps({"plan": plan, "mixture": sorted(mixture)}, indent=2))
    else:
        print(_render(plan, mixture))
    return 0


def _selfcheck():
    """Assert the pure classifier on fixture rows -- ZERO fs/GPU/network."""
    mixture = {"keep/me", "fp8/protected"}
    cav = "FP8/FP4 MoE hangs post-load on sm_120, see CLAUDE.md gotcha #3"
    rows = [
        {"id": "keep/me", "format": "safetensors", "size_gb": 10.0, "sm120_caveat": None},
        {"id": "g/coder", "format": "GGUF", "size_gb": 20.0, "sm120_caveat": None},
        {"id": "fp8/moe", "format": "safetensors", "size_gb": 80.0, "sm120_caveat": cav},
        {"id": "unused/dense", "format": "safetensors", "size_gb": 30.0, "sm120_caveat": None},
        {"id": "fp8/protected", "format": "safetensors", "size_gb": 40.0, "sm120_caveat": cav},
    ]
    plan = build_plan(mixture, rows=rows)
    cands = plan["candidates"]
    cid = {c["id"] for c in cands}
    # acceptance #2: no mixture id is ever a candidate; both protected echoed.
    assert cid.isdisjoint(mixture), "mixture model must never be a candidate"
    assert set(plan["protected"]) == mixture, "protected ids must be echoed"
    assert cid == {"g/coder", "fp8/moe", "unused/dense"}, cid
    by_id = {c["id"]: c for c in cands}
    # acceptance #1: each candidate carries a reason; tiers distinct.
    assert by_id["g/coder"]["reason"] == "incompatible-gguf"
    assert by_id["g/coder"]["dead_everywhere"] is False, "GGUF has llama.cpp fallback"
    assert by_id["fp8/moe"]["reason"] == "incompatible-sm120"
    assert by_id["fp8/moe"]["dead_everywhere"] is True, "FP8-MoE dead on this box"
    assert by_id["unused/dense"]["reason"] == "purposeless"
    assert by_id["unused/dense"]["dead_everywhere"] is False
    # acceptance #3: total = sum over candidates only (protected excluded).
    expect = round(20e9) + round(80e9) + round(30e9)
    assert plan["total_reclaimable_bytes"] == expect, plan["total_reclaimable_bytes"]
    assert plan["total_reclaimable_bytes"] == sum(c["reclaimable_bytes"] for c in cands)
    assert plan["total_reclaimable_gb"] == round(expect / 1e9, 1)
    # by_reason subtotals reconcile to the total and the candidate count.
    assert sum(b["bytes"] for b in plan["by_reason"].values()) == expect
    assert sum(b["count"] for b in plan["by_reason"].values()) == 3
    # deterministic sort by (-size_gb, id): [fp8/moe(80), unused/dense(30), g/coder(20)].
    assert [c["id"] for c in cands] == ["fp8/moe", "unused/dense", "g/coder"], cands

    # --- SAFETY: shared real-model predicate. A non-model dir (no weights, no
    #     model_type, >=0.2GB: a dataset / partial download / tokenizer-only dir)
    #     must FAIL the gate and so never become a deletion candidate. ---
    from . import _sync
    junk = {"id": "some/dataset", "owner": "x", "model_type": None,
            "format": "?", "size_gb": 5.0, "size_bytes": 5_000_000_000,
            "sm120_caveat": None, "local_path": "/junk"}
    realm = {"id": "real/model", "owner": "x", "model_type": "llama",
             "format": "safetensors", "size_gb": 3.0, "size_bytes": 3_000_000_000,
             "sm120_caveat": None, "local_path": "/real"}
    assert not _sync.is_real_model_row(junk), "non-model dir must FAIL the real gate"
    assert _sync.is_real_model_row(realm), "real model must PASS the real gate"
    plan_safe = build_plan(set(), rows=[r for r in (junk, realm)
                                        if _sync.is_real_model_row(r)])
    assert {c["id"] for c in plan_safe["candidates"]} == {"real/model"}, \
        "a non-model dir must never be a prune candidate"

    # --- true-byte reclaim: size_bytes carries through un-quantized (not the
    #     0.1GB-rounded size_gb). ---
    plan_b = build_plan(set(), rows=[{"id": "a/b", "format": "safetensors",
                                      "size_gb": 3.1, "size_bytes": 3_141_592_653}])
    assert plan_b["total_reclaimable_bytes"] == 3_141_592_653, \
        plan_b["total_reclaimable_bytes"]

    # --- GGUF guard: a GGUF row carrying a (spurious) sm120 caveat stays the
    #     llama.cpp tier and is NEVER dead_everywhere. ---
    plan_g = build_plan(set(), rows=[{"id": "g/x", "format": "GGUF",
                                      "size_gb": 4.0, "sm120_caveat": cav}])
    gg = plan_g["candidates"][0]
    assert gg["reason"] == "incompatible-gguf" and gg["dead_everywhere"] is False, gg

    # --- render: protected line shows the MATCHED set; a requested id absent from
    #     the scan is NOT shown as protected and triggers a visible WARNING. ---
    req = {"keep/me", "ghost/missing"}
    rtxt = _render(build_plan(req, rows=rows), req)
    head, _, warn = rtxt.partition("WARNING")
    assert "keep/me" in head, "matched id must show as protected"
    assert warn and "ghost/missing" in warn, "unmatched id must WARN"
    assert "ghost/missing" not in head, "unmatched id must NOT show as protected"


if __name__ == "__main__":
    sys.exit(main())
