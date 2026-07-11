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
import os
import sys
import json
import argparse
import shutil

from . import guard


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


# --- EXECUTE LAYER (T008) -------------------------------------------------
# Deletes the plan's candidates and reports ACTUAL bytes reclaimed. NEVER
# re-derives the plan (build_plan/classify_rows above are the single source of
# truth); it consumes the plan OBJECT, so no arbitrary/user path can enter.

def _validate_target(path, roots):
    """SINGLE deletion chokepoint -- fs-stat only, NEVER deletes.

    Returns (ok: bool, realpath: str|None, reason: str). A path is OK to delete
    only if ALL hold: it is a non-empty str; not a symlink (checked BEFORE
    resolving, so we never rmtree through a link); resolves to a real existing
    directory; is not a filesystem/drive root; and lives STRICTLY under a
    recognized cache root in `roots` (commonpath, normcased -- not a prefix
    string match, so /a/bc never counts as under /a/b; equality with a root is
    rejected). Anything else -> (False, ..., reason)."""
    if not path or not isinstance(path, str):
        return (False, None, "refused:empty")
    # Require an ABSOLUTE path: a relative local_path could otherwise resolve
    # against the process CWD (a future-bug foothold), so refuse it outright.
    if not os.path.isabs(path):
        return (False, None, "refused:not-absolute")
    # Reject a symlink BEFORE resolving so an escaping link is never followed.
    if os.path.islink(path):
        return (False, None, "refused:symlink")
    rp = os.path.realpath(path)                  # collapse any symlink escape
    if not os.path.isdir(rp):
        # already-deleted (idempotency) or a file -> never delete.
        return (False, rp, "refused:notdir" if os.path.exists(rp) else "gone")
    if os.path.dirname(rp) == rp:               # '/', 'C:\\' -- a drive/fs root
        return (False, rp, "refused:root")
    ncrp = os.path.normcase(rp)
    for root, _kind in (roots or []):
        ncrr = os.path.normcase(os.path.realpath(root))
        try:
            # commonpath (NOT startswith) kills the /a/bc-vs-/a/b prefix bug;
            # ValueError => different Windows drive => outside; rp==root rejected.
            if os.path.commonpath([ncrr, ncrp]) == ncrr and ncrp != ncrr:
                return (True, rp, "ok")
        except ValueError:
            continue
    return (False, rp, "refused:outside-root")


def execute_plan(plan, roots=None, dry_run=True, include_servable=False):
    """Execute (or, by default, dry-run) the prune `plan` build_plan() produced.

    Bias to SAFETY (the module's 'SGLang-unservable != deletable' doctrine):
    by DEFAULT only candidates with dead_everywhere==True (the FP8/NVFP4-MoE
    that are dead by EVERY engine on this sm_120 box) are deleted. The
    servable-elsewhere tiers -- incompatible-gguf (real llama.cpp fallback) and
    purposeless (servable safetensors, simply unused) -- are KEPT and reported
    in `kept`, NOT deleted, unless include_servable broadens deletion to them.

    Iterates plan['candidates'] ONLY (classify_rows already dropped every
    mixture/protected id) and routes every local_path through _validate_target
    immediately before deletion -- this minimizes the validate->delete window
    (it is NOT a true fd-based TOCTOU lock; Windows rmtree isn't fd-based).
    Bytes are credited ONLY after a post-rmtree existence re-check confirms the
    dir is truly gone. One bad row is isolated to `skipped` and never aborts the
    batch.

    roots: injectable (root, kind) list; defaults to _sync.ROOTS (the seam the
    self-check uses to gate deletion to a temp tree). dry_run=True (the safe
    default) makes ZERO rmtree calls and leaves reclaimed_bytes == 0.

    Returns {dry_run, include_servable, deleted:[id], would_delete:[id],
    kept:[{id,reason}], skipped:[{id,reason}], reclaimed_bytes:int (0 when
    dry_run), planned_bytes:int, reclaimed_gb:float}.
    """
    from . import _sync
    if roots is None:
        roots = _sync.ROOTS
    protected = set(plan.get("protected") or [])
    deleted, would_delete, kept, skipped = [], [], [], []
    reclaimed_bytes, planned_bytes = 0, 0
    for c in plan.get("candidates", []):
        cid = c.get("id")
        # Defense-in-depth tripwire that SURVIVES python -O (an assert would be
        # stripped): a protected id must never be a candidate -- if one ever is,
        # skip it explicitly and record why, never delete it.
        if cid in protected:
            skipped.append({"id": cid, "reason": "refused:protected"})
            continue
        # SAFETY default: keep the servable-elsewhere tiers (dead_everywhere
        # False) unless the caller explicitly opts in. Only dead_everywhere
        # candidates are deletable by default.
        if not c.get("dead_everywhere") and not include_servable:
            kept.append({"id": cid, "reason": c.get("reason")})
            continue
        ok, rp, reason = _validate_target(c.get("local_path"), roots)
        if not ok:
            skipped.append({"id": cid, "reason": reason})
            continue
        n = _sync.dir_size_bytes(rp)            # measure BEFORE removal
        planned_bytes += n
        if dry_run:
            would_delete.append(cid)
            continue
        try:
            shutil.rmtree(rp)
        except OSError as e:
            skipped.append({"id": cid, "reason": "refused:rmtree-error:%s" % e.__class__.__name__})
            continue
        if not os.path.exists(rp):              # credit only if truly gone
            reclaimed_bytes += n
            deleted.append(cid)
        else:
            skipped.append({"id": cid, "reason": "refused:still-present"})
    return {
        "dry_run": dry_run,
        "include_servable": include_servable,
        "deleted": deleted,
        "would_delete": would_delete,
        "kept": kept,
        "skipped": skipped,
        "reclaimed_bytes": reclaimed_bytes,
        "planned_bytes": planned_bytes,
        "reclaimed_gb": round(reclaimed_bytes / 1e9, 1),
    }


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


def main(argv=None, scan=_default_scan, *, prog="anvil-serving models cache prune"):
    ap = argparse.ArgumentParser(prog=prog,
                                 description="Plan (and optionally execute) pruning of local HF model caches.")
    ap.add_argument("--mixture", default="",
                    help="comma-separated model ids to PROTECT (never removed). "
                         "Empty => nothing protected (an explicit, echoed choice).")
    ap.add_argument("--json", action="store_true", help="emit the plan dict as JSON")
    ap.add_argument("--execute", action="store_true",
                    help="ACTUALLY DELETE candidate directories. Default ABSENT "
                         "=> safe dry-run. Requires --yes. By default deletes "
                         "ONLY dead_everywhere candidates (servable tiers KEPT).")
    # default=None (NOT True): with store_true an unset flag is None, a set flag
    # True. That makes the literal gate `args.execute and not args.dry_run`
    # actually work -- True default would force dry_run forever (never deletes).
    ap.add_argument("--dry-run", action="store_true", default=None,
                    help="plan/report only; delete nothing. This is the default, "
                         "and when passed ALONGSIDE --execute it OVERRIDES it "
                         "(forces a dry-run -- bias to safety).")
    ap.add_argument("--include-servable", action="store_true",
                    help="broaden deletion to the servable-elsewhere tiers "
                         "(incompatible-gguf / purposeless). WITHOUT it, execute "
                         "deletes ONLY dead_everywhere candidates and KEEPS those.")
    ap.add_argument("--allow-empty-mixture", action="store_true",
                    help="permit an --execute --include-servable broad wipe when "
                         "NO --mixture is protected (otherwise such a wipe is "
                         "refused, since it would take servable models too).")
    ap.add_argument("--yes", action="store_true",
                    help="required confirmation gate alongside --execute "
                         "(without it, --execute refuses and deletes nothing).")
    ap.add_argument("--self-check", action="store_true",
                    help="run the internal self-check (no real cache/GPU/network), "
                         "print 'self-check OK', and exit 0")
    args = ap.parse_args(argv)
    if args.self_check:
        _selfcheck()
        print("self-check OK")
        return 0
    mixture = {m.strip() for m in args.mixture.split(",") if m.strip()}
    # Gate 1: real deletion needs BOTH --execute AND --yes.
    if args.execute and not (args.yes or guard.confirmation_authorized()):
        print("refusing to delete without --yes (this DELETES directories)")
        return 2
    # Gate 2 (checked BEFORE any scan): a broad wipe -- --execute with
    # --include-servable and an EMPTY mixture -- protects NOTHING and would also
    # take servable GGUF/safetensors. Refuse unless explicitly allowed.
    if args.execute and args.include_servable and not mixture and not args.allow_empty_mixture:
        print("REFUSING broad wipe: --execute --include-servable with an EMPTY "
              "--mixture protects nothing and would delete servable GGUF/"
              "safetensors too. Re-run with --allow-empty-mixture to override, "
              "or pass --mixture to protect your models.")
        return 2
    # Build the plan ONCE here; execute_plan consumes this OBJECT and never
    # re-scans/re-classifies (so no path outside the freshly-built plan can be
    # deleted).
    plan = build_plan(mixture, scan=scan)
    # Gate 3: a requested protect id that did NOT match the scan means we cannot
    # honor that protection -- ABORT before deleting anything (warning printed
    # FIRST / surfaced in --json). Never delete after a failed-to-match protect id.
    unmatched = sorted(mixture - set(plan["protected"]))
    if args.execute and unmatched:
        if args.json:
            print(json.dumps({"error": "unmatched-protect-ids",
                              "unmatched": unmatched, "plan": plan,
                              "mixture": sorted(mixture)}, indent=2))
        else:
            print("ABORT: requested protect id(s) NOT found in scan -- refusing "
                  "to delete (cannot honor protection): %s" % unmatched)
        return 2
    # Honor --dry-run: it OVERRIDES --execute. Real deletion only when --execute
    # is set AND --dry-run was not.
    real_delete = bool(args.execute and not args.dry_run)
    report = execute_plan(plan, dry_run=not real_delete,
                          include_servable=args.include_servable)
    if args.json:
        print(json.dumps({"plan": plan, "report": report,
                          "mixture": sorted(mixture)}, indent=2))
        return 0
    print(_render(plan, mixture))
    print()
    if real_delete:
        print("reclaimed {b} bytes ({g} GB)".format(
            b=report["reclaimed_bytes"], g=report["reclaimed_gb"]))
        print("deleted: {d}".format(d=report["deleted"]))
    else:
        planned_gb = round(report["planned_bytes"] / 1e9, 1)
        tag = "DRY-RUN (--dry-run overrides --execute)" if args.execute else "DRY-RUN"
        print("{t} -- would reclaim {b} bytes ({g} GB)".format(
            t=tag, b=report["planned_bytes"], g=planned_gb))
        print("would delete: {d}".format(d=report["would_delete"]))
    if report.get("kept"):
        print("kept (servable elsewhere): {k}".format(
            k=[e["id"] for e in report["kept"]]))
    if report["skipped"]:
        print("skipped: {s}".format(s=report["skipped"]))
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

    # --- EXECUTE layer on a TEMP filesystem (real dirs, real rmtree); NO real
    #     HF cache, NO GPU, NO network. Roots are INJECTED so every deletion is
    #     gated to the temp tree only. ---
    import tempfile
    import shutil as _sh
    root = tempfile.mkdtemp(prefix="anvil_prune_")
    outside = tempfile.mkdtemp(prefix="anvil_outside_")
    try:
        def _mk(parent, name):
            d = os.path.join(parent, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "data.bin"), "wb") as fh:
                fh.write(b"x" * 4096)            # real bytes => dir_size_bytes > 0
            return d
        d_coder = _mk(root, "models--g--coder")      # GGUF candidate
        d_moe = _mk(root, "models--fp8--moe")        # sm120 candidate
        d_keep = _mk(root, "models--keep--me")       # PROTECTED
        d_out = _mk(outside, "models--evil--escape") # off-root

        def tmp_scan():
            """Injectable scan seam: emit rows from the temp tree, no real cache."""
            out = []
            for name in sorted(os.listdir(root)):
                p = os.path.join(root, name)
                if not (os.path.isdir(p) and name.startswith("models--")):
                    continue
                ident = name[len("models--"):].replace("--", "/", 1)
                if ident == "g/coder":
                    fmt, c = "GGUF", None
                elif ident == "fp8/moe":
                    fmt, c = "safetensors", cav
                else:
                    fmt, c = "safetensors", None
                nb = _sync.dir_size_bytes(p)
                out.append({"id": ident, "owner": ident.split("/")[0],
                            "model_type": "llama", "format": fmt,
                            "size_bytes": nb, "size_gb": round(nb / 1e9, 1),
                            "sm120_caveat": c, "local_path": p})
            return out

        inj = [(root, "hf")]
        plan1 = build_plan({"keep/me"}, scan=tmp_scan)   # reuse the T007 planner
        assert {c["id"] for c in plan1["candidates"]} == {"g/coder", "fp8/moe"}, \
            plan1["candidates"]
        assert plan1["protected"] == ["keep/me"], plan1["protected"]
        n_coder = _sync.dir_size_bytes(d_coder)
        n_moe = _sync.dir_size_bytes(d_moe)

        # DEFAULT DRY-RUN (include_servable False): only the dead_everywhere
        # candidate (fp8/moe) would be deleted; the servable GGUF (g/coder) is
        # KEPT. planned counts ONLY the deletable one; reclaimed == 0; all survive.
        rd = execute_plan(plan1, roots=inj, dry_run=True)
        assert rd["dry_run"] is True and rd["reclaimed_bytes"] == 0, rd
        assert rd["would_delete"] == ["fp8/moe"], rd
        assert [e["id"] for e in rd["kept"]] == ["g/coder"], rd
        assert rd["planned_bytes"] == n_moe, rd
        assert os.path.isdir(d_coder) and os.path.isdir(d_moe) and os.path.isdir(d_keep)

        # DEFAULT EXECUTE: deletes ONLY dead_everywhere (fp8/moe); KEEPS the
        # servable GGUF (g/coder) and the protected dir; credits ACTUAL bytes.
        rx = execute_plan(plan1, roots=inj, dry_run=False)
        assert rx["deleted"] == ["fp8/moe"], rx
        assert [e["id"] for e in rx["kept"]] == ["g/coder"], rx
        assert rx["reclaimed_bytes"] == n_moe, rx
        assert not os.path.exists(d_moe), "dead_everywhere dir must be deleted"
        assert os.path.isdir(d_coder), "servable GGUF must be KEPT by default"
        assert os.path.isdir(d_keep), "protected dir must remain after execute"

        # --include-servable broadens deletion to the servable tier: the GGUF
        # the default KEPT is now deleted. (fp8/moe already gone above.)
        plan_inc = build_plan({"keep/me"}, scan=tmp_scan)
        assert {c["id"] for c in plan_inc["candidates"]} == {"g/coder"}, plan_inc
        ri = execute_plan(plan_inc, roots=inj, dry_run=False, include_servable=True)
        assert ri["deleted"] == ["g/coder"] and ri["kept"] == [], ri
        assert ri["reclaimed_bytes"] == n_coder, ri
        assert not os.path.exists(d_coder), "include_servable must delete the GGUF"
        assert os.path.isdir(d_keep), "protected dir must still remain"

        # IDEMPOTENT: a fresh scan no longer finds the deleted dirs => no candidates.
        plan2 = build_plan({"keep/me"}, scan=tmp_scan)
        assert plan2["candidates"] == [], plan2["candidates"]
        r2 = execute_plan(plan2, roots=inj, dry_run=False, include_servable=True)
        assert r2["deleted"] == [] and r2["reclaimed_bytes"] == 0, r2

        # GUARD: an off-root candidate is refused even with include_servable; survives.
        fake = {"candidates": [{"id": "evil/escape", "local_path": d_out,
                                "dead_everywhere": True}], "protected": []}
        rg = execute_plan(fake, roots=inj, dry_run=False, include_servable=True)
        assert rg["deleted"] == [] and os.path.isdir(d_out), rg
        assert rg["skipped"] == [{"id": "evil/escape", "reason": "refused:outside-root"}], rg

        # GUARD: protected id sneaking into candidates is SKIPPED (not deleted),
        #   and survives python -O (explicit branch, not an assert).
        d_sneak = _mk(root, "models--keep--me2")
        sneak = {"candidates": [{"id": "keep/me2", "local_path": d_sneak,
                                 "dead_everywhere": True}],
                 "protected": ["keep/me2"]}
        rp_ = execute_plan(sneak, roots=inj, dry_run=False)
        assert rp_["deleted"] == [] and os.path.isdir(d_sneak), rp_
        assert rp_["skipped"] == [{"id": "keep/me2", "reason": "refused:protected"}], rp_

        # GUARD: _validate_target rejects empty/None, RELATIVE, the fs root, equals-a-root.
        assert _validate_target("", inj)[0] is False
        assert _validate_target(None, inj)[0] is False
        assert _validate_target("relative/dir", inj)[2] == "refused:not-absolute", \
            _validate_target("relative/dir", inj)
        assert _validate_target("/", inj)[0] is False
        ok_r, _rp, why_r = _validate_target(root, inj)   # equals a root
        assert ok_r is False and why_r == "refused:outside-root", why_r

        # GUARD: a symlink inside root pointing OUTSIDE is refused (never followed)
        #   and its target survives. Skipped where the OS denies symlink creation.
        link = os.path.join(root, "models--link--escape")
        made_link = True
        try:
            os.symlink(d_out, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            made_link = False
        if made_link:
            ok_l, _lp, why_l = _validate_target(link, inj)
            assert ok_l is False and why_l == "refused:symlink", why_l
            assert os.path.isdir(d_out), "symlink target must survive"

        # --- POLICY GATES via main() (scan INJECTED -> never touches the REAL
        #     cache). Re-make candidate dirs first so an accidental deletion would
        #     show up; these gates must touch NOTHING. ---
        import io
        from contextlib import redirect_stdout
        d_coder = _mk(root, "models--g--coder")
        d_moe = _mk(root, "models--fp8--moe")
        # Empty mixture + --include-servable broad wipe is REFUSED (non-zero),
        # BEFORE any scan; nothing is deleted.
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc_wipe = main(["--execute", "--yes", "--include-servable"], scan=tmp_scan)
        assert rc_wipe != 0 and "REFUSING" in buf.getvalue(), (rc_wipe, buf.getvalue())
        assert os.path.isdir(d_coder) and os.path.isdir(d_moe), "wipe must delete nothing"
        # An unmatched protect id ABORTS (non-zero) with the warning printed FIRST.
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc_un = main(["--mixture", "ghost/missing", "--execute", "--yes"], scan=tmp_scan)
        assert rc_un != 0 and buf.getvalue().lstrip().startswith("ABORT"), \
            (rc_un, buf.getvalue())
        # ...and is surfaced in --json too.
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc_unj = main(["--mixture", "ghost/missing", "--execute", "--yes",
                           "--json"], scan=tmp_scan)
        assert rc_unj != 0 and '"unmatched-protect-ids"' in buf.getvalue(), \
            (rc_unj, buf.getvalue())
        assert os.path.isdir(d_coder) and os.path.isdir(d_moe), "abort must delete nothing"
        # --dry-run OVERRIDES --execute: even with --execute --yes, nothing deletes.
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc_dry = main(["--mixture", "keep/me", "--execute", "--yes",
                           "--dry-run"], scan=tmp_scan)
        assert rc_dry == 0 and "DRY-RUN" in buf.getvalue(), (rc_dry, buf.getvalue())
        assert os.path.isdir(d_moe), "--dry-run override must not delete"
    finally:
        _sh.rmtree(root, ignore_errors=True)
        _sh.rmtree(outside, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
