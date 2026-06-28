#!/usr/bin/env python3
"""score.py - role-suitability scorer (T005 / F002 / R005).

Scores candidate models on a FIXED taxonomy - coding vs research vs writing -
and selects a recommended mixture per tier/role with a target context window.

THE LOAD-BEARING RULE: do NOT fabricate scores. The deep-research verifier
(docs/findings/2026-06-28-model-mixture-research.md, "Verification / refusal to
score" section) explicitly REFUSED to invent 1-5 coding/research/writing scores
because no source backed them - "only standardized SWE-bench positions did". So
this module instead:

  1. Encodes the REAL researched benchmark numbers as a data TABLE (`CANDIDATES`),
     each number citing the doc line it came from. No number is invented.
  2. DERIVES each role score from those facts via a small, documented mapping
     rule (`ROLE_BENCH`) - the scorer's ONLY judgment, encoded as data.
  3. Carries PROVENANCE on every derived score (which benchmark + value + source).
  4. Returns the typed sentinel "unknown" - NEVER a number - for any role a
     candidate has no captured benchmark for. Absence of a benchmark key is the
     only way a role can be unknown; it is never coerced to 0 or a guess.

Structural honesty: a role score exists ONLY as a thin wrapper around a real
benchmark row, so it is impossible to emit a number with no backing entry.

PURE vs IMPURE: `role_score` / `score_candidate` / `score_all` /
`select_mixture` / `bench_value` and the self-check are PURE - no fs, GPU, or
network. `local_candidates()` is the ONLY fs/network path (it wraps
`_sync.discover()` + `_sync.summarize()` for the on-box catalog). `main()` is the
only place that merges the two catalogs and prints.

Stdlib only. Run:  python -m anvil_serving.score --help
                   python -m anvil_serving.score --self-check
"""

# The single, canonical citation target for every research-sourced number. All
# research provenance threads through this ONE string so a citation can't silently
# drift per-row; the self-check asserts it is present + consistent everywhere.
FINDINGS_DOC = "docs/findings/2026-06-28-model-mixture-research.md"

# A real benchmark score is a percentage in [0, 100]. Anything outside this band
# is NOT a score - it's the local-catalog regex (_sync.extract_from_card) having
# grabbed a stray digit (e.g. '500' from 'evaluated on 500 problems'). Such values
# degrade to 'unknown' and can never back a role or win the mixture.
SCORE_MIN, SCORE_MAX = 0.0, 100.0

# ---------------------------------------------------------------------------
# The scorer's ONLY judgment, encoded as DATA: which benchmark backs which role,
# in priority order (first present wins). Documented + transparent.
#   coding   <- SWE-bench Verified (preferred), LiveCodeBench (fallback)
#   research <- MMLU-Pro (knowledge/reasoning), RULER@128K (long-context fallback)
#   writing  <- Arena-Hard / MT-Bench / AlpacaEval  -- NONE captured in the
#               findings doc (grep for these names returns 0), so writing is
#               UNKNOWN for every candidate. We keep the rule explicit rather
#               than dropping the role, so its emptiness is visible & honest.
# ---------------------------------------------------------------------------
ROLE_BENCH = {
    "coding":   ["SWE-bench Verified", "LiveCodeBench"],
    "research": ["MMLU-Pro", "RULER@128K"],
    "writing":  ["Arena-Hard", "MT-Bench", "AlpacaEval"],
}

ROLES = ("coding", "research", "writing")
TIERS = ("heavy", "fast")

# ---------------------------------------------------------------------------
# The research catalog, transcribed from
#   docs/findings/2026-06-28-model-mixture-research.md
# Row shape: {id, tier, source, repo, context, servable, benchmarks, note}
#   benchmarks[<BENCH>] = {"value": <float>, "src": "<doc line(s)>"}
# ONLY numbers literally present in the findings doc are encoded. Everything
# else is an ABSENT key -> the role derives to "unknown", never 0/guess.
# Each benchmark `src` is a STABLE anchor (model name / benchmark), not a raw line
# number, so provenance can't silently drift if the doc is re-flowed.
#
# `context` has ONE fixed meaning everywhere in this module: the model's NATIVE
# maximum context window as stated by an authoritative source (the findings doc
# for research rows; config.json `max_position_embeddings` for local rows). It is
# NEVER an inferred-from-prose number and NEVER a deploy --context-length. Where no
# authoritative native max exists, it is None. It is a deploy fact, not a score,
# and never participates in role scoring.
# ---------------------------------------------------------------------------
CANDIDATES = [
    # ----- HEAVY (96GB) -----
    {
        "id": "openai/gpt-oss-120b", "tier": "heavy", "source": "research",
        "repo": "openai/gpt-oss-120b", "context": 131072, "servable": True,
        "benchmarks": {
            "SWE-bench Verified": {"value": 62.4, "src": "gpt-oss-120b / SWE-bench Verified"},
        },
        "note": "117B-A5.1B MoE, native MXFP4 -> TRITON backend; Apache-2.0; ~63GB.",
    },
    {
        "id": "QuantTrio/Seed-OSS-36B-Instruct-AWQ", "tier": "heavy", "source": "research",
        "repo": "QuantTrio/Seed-OSS-36B-Instruct-AWQ", "context": 131072, "servable": True,
        "benchmarks": {
            "SWE-bench Verified": {"value": 56.0,  "src": "Seed-OSS-36B-Instruct / SWE-bench Verified"},
            "LiveCodeBench":      {"value": 67.4,  "src": "Seed-OSS-36B-Instruct / LiveCodeBench"},
            "MMLU-Pro":           {"value": 82.7,  "src": "Seed-OSS-36B-Instruct / MMLU-Pro"},
            "RULER@128K":         {"value": 94.6,  "src": "Seed-OSS-36B-Instruct / RULER@128K"},
        },
        "note": "dense ~20GB; flashinfer; seed_oss parser; strong all-rounder.",
    },
    {
        # Reputational coding pick with NO verified quality benchmark in the doc -
        # only throughput (~8,400 tok/s, lines 23/52/86), which backs no role.
        # Deliberate honesty demo: all roles derive to unknown.
        "id": "cpatonn/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit", "tier": "heavy",
        "source": "research", "repo": "cpatonn/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit",
        "context": 131072, "servable": True, "benchmarks": {},
        "note": "coding replica; only throughput figures exist (lines 23, 52, 86) "
                "- no quality benchmark -> all roles unknown.",
    },
    {
        "id": "casperhansen/llama-3.3-70b-instruct-awq", "tier": "heavy",
        "source": "research", "repo": "casperhansen/llama-3.3-70b-instruct-awq",
        "context": 131072, "servable": True, "benchmarks": {},
        "note": "dense ~40GB; prose only 'weak on agentic coding' (line 54) "
                "-> no numeric benchmark -> all roles unknown.",
    },
    {
        "id": "cyankiwi/Llama-3_3-Nemotron-Super-49B-v1_5-AWQ-4bit", "tier": "heavy",
        "source": "research", "repo": "cyankiwi/Llama-3_3-Nemotron-Super-49B-v1_5-AWQ-4bit",
        "context": None, "servable": True, "benchmarks": {},
        "note": "reasoner ~29GB; provisional, sm_120 untested (line 55) "
                "-> no numeric benchmark -> all roles unknown.",
    },
    {
        "id": "cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit", "tier": "heavy",
        "source": "research", "repo": "cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit",
        "context": 262144, "servable": True, "benchmarks": {},
        "note": "the live qwen35-awq baseline (262K native, line 175); "
                "no numeric benchmark -> all roles unknown.",
    },
    # ----- FAST (32GB) -----
    {
        "id": "cyankiwi/GLM-4.7-Flash-AWQ-4bit", "tier": "fast", "source": "research",
        "repo": "cyankiwi/GLM-4.7-Flash-AWQ-4bit", "context": 131072, "servable": True,
        "benchmarks": {
            "SWE-bench Verified": {"value": 59.2, "src": "GLM-4.7-Flash / SWE-bench Verified"},
            "LiveCodeBench":      {"value": 64.0, "src": "GLM-4.7-Flash / LiveCodeBench"},
        },
        "note": "30B-A3B MoE, MIT; TRITON; AWQ not BF16 (BF16 -> garbage, sglang#18874).",
    },
    {
        "id": "cyankiwi/Devstral-Small-2-24B-Instruct-2512-AWQ-4bit", "tier": "fast",
        "source": "research", "repo": "cyankiwi/Devstral-Small-2-24B-Instruct-2512-AWQ-4bit",
        "context": 262144, "servable": True,
        "benchmarks": {
            "SWE-bench Verified": {"value": 68.0, "src": "Devstral-Small-2-24B / SWE-bench Verified"},
        },
        "note": "dense 24B, highest SWE in class; flashinfer, --language-only; 256K ctx.",
    },
    {
        "id": "openai/gpt-oss-20b", "tier": "fast", "source": "research",
        "repo": "openai/gpt-oss-20b", "context": 131072, "servable": True,
        "benchmarks": {
            "LiveCodeBench": {"value": 70.0, "src": "gpt-oss-20b / LiveCodeBench"},
        },
        "note": "MoE ~16GB, native MXFP4 -> TRITON; only batch tok/s measured.",
    },
    {
        "id": "Qwen/Qwen3-14B-AWQ", "tier": "fast", "source": "research",
        "repo": "Qwen/Qwen3-14B-AWQ", "context": 65536, "servable": True,
        "benchmarks": {},
        "note": "dense ~8GB; dodges every sm_120 MoE/FP8/NVFP4 hang (line 63); "
                "no numeric benchmark -> all roles unknown.",
    },
    {
        "id": "ISTA-DASLab/gemma-3-27b-it-GPTQ-4b-128g", "tier": "fast",
        "source": "research", "repo": "ISTA-DASLab/gemma-3-27b-it-GPTQ-4b-128g",
        "context": None, "servable": True, "benchmarks": {},
        "note": "generalist, prose 'coding-weak', provisional (line 65) "
                "-> no numeric benchmark -> all roles unknown.",
    },
]

# Explicitly EXCLUDED from scoring, with the reason - documented so the field
# stays honest about what we left out and why (printed, never scored).
EXCLUDED = [
    {"id": "Qwen/Qwen3-Coder-480B-A35B",
     "reason": "38.7% on SEAL SWE-bench *Pro* (Qwen3-Coder-480B / SWE-bench Pro) "
               "- a DIFFERENT metric (not Verified), ceiling-only; 480B doesn't fit one card."},
    {"id": "QuantTrio/GLM-4.5-Air-AWQ-FP16Mix",
     "reason": "the doc attributes SWE 64.2 to the GLM-4.5 FLAGSHIP, not Air "
               "('Air trails') -> Air's own number is unknown."},
    {"id": "THUDM/GLM-4.7-Flash (BF16) / bullpoint/GLM-4.6-AWQ",
     "reason": "flagged broken / too big on sm_120 (Excluded models section)."},
    {"id": "ByteDance-Seed/Seed-Coder-8B",
     "reason": "SWE claim refuted 0-3 in verification (Seed-Coder-8B / SWE-bench Verified)."},
]


# ---------------------------------------------------------------------------
# PURE helpers
# ---------------------------------------------------------------------------
def _fmt(v):
    """Compact number formatting: 68.0 -> '68', 62.4 -> '62.4'."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f.is_integer() else str(f)


def _bench_lookup(benchmarks, key):
    """Case-INSENSITIVE fetch of a raw benchmark entry.

    `_sync.extract_from_card` stores SWE scores under EITHER 'SWE-bench Verified'
    OR 'SWE-Bench Verified' (capital B). An exact-key lookup would silently miss a
    real local score stored under the other spelling, degrading it to 'unknown'.
    So we match case-insensitively (exact hit preferred), recognizing both.
    """
    if not isinstance(benchmarks, dict):
        return None
    if key in benchmarks:
        return benchmarks[key]
    kl = key.lower()
    for k, v in benchmarks.items():
        if isinstance(k, str) and k.lower() == kl:
            return v
    return None


def bench_value(benchmarks, key):
    """Coerce a captured benchmark to a BOUNDED float in [0, 100], or None.

    Handles BOTH catalog shapes:
      - research rows:  benchmarks[key] = {"value": <num>, "src": "..."}
      - local rows:     benchmarks[key] = "<string>"  (this is exactly how
        `_sync.extract_from_card` stores it: it does
        `out.setdefault("benchmarks", {})[bench] = m.group(1)`, a regex string).

    A missing key, a non-numeric value, OR a value outside [0, 100] -> None.
    The bound is load-bearing: the local regex can capture a stray non-score digit
    (e.g. '500' from 'evaluated on 500 problems'); without the bound that 500 would
    float() into a fake 'SWE-bench Verified' score and WIN select_mixture. Bounding
    it degrades such values to 'unknown' so they can never back a role or be picked.
    Real 0-100 values pass through verbatim.
    """
    raw = _bench_lookup(benchmarks, key)
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v < SCORE_MIN or v > SCORE_MAX:
        return None
    return v


def _bench_src(benchmarks, key):
    """Where the captured number came from (stable doc anchor for research, else card)."""
    raw = _bench_lookup(benchmarks, key)
    if isinstance(raw, dict) and raw.get("src"):
        return raw["src"]
    return "local card"


def role_score(benchmarks, role, *, source, repo):
    """PURE. Derive one role's score from a benchmarks dict via ROLE_BENCH.

    Walks ROLE_BENCH[role] in priority order; returns the FIRST present
    benchmark as a backed value-with-provenance, else the UNKNOWN sentinel.
    Never invents a number; the returned `score` is the published benchmark
    value carried VERBATIM (a percentage, 0-100), not a synthesized scale.
    """
    for key in ROLE_BENCH[role]:
        v = bench_value(benchmarks, key)
        if v is not None:
            src = _bench_src(benchmarks, key)
            # Research numbers ALWAYS cite the one canonical findings doc (+ a
            # stable anchor); local numbers cite the on-box card. Threading every
            # research citation through FINDINGS_DOC is what the provenance
            # self-check verifies for consistency.
            cite = f"{FINDINGS_DOC} -> {src}" if source == "research" else src
            return {
                "score": v,
                "basis": key,
                "raw": v,
                "provenance": f"{source}: {repo} {key}={_fmt(v)} [{cite}]",
                "source": source,
            }
    return {
        "score": "unknown",
        "reason": f"no {role} benchmark ({', '.join(ROLE_BENCH[role])}) captured",
        "source": source,
    }


def score_candidate(cand):
    """PURE. {id,tier,source,repo,context,servable,scores:{coding,research,writing}}."""
    b = cand.get("benchmarks", {})
    return {
        "id": cand["id"],
        "tier": cand["tier"],
        "source": cand["source"],
        "repo": cand["repo"],
        "context": cand.get("context"),
        "servable": bool(cand.get("servable")),
        "note": cand.get("note", ""),
        "scores": {r: role_score(b, r, source=cand["source"], repo=cand["repo"])
                   for r in ROLES},
    }


def score_all(cands):
    """PURE. Score a list of candidate-fact dicts."""
    return [score_candidate(c) for c in cands]


def select_mixture(scored, tiers=TIERS, roles=ROLES):
    """PURE. Pick the recommended model per (tier, role), SAME-BASIS only.

    For each (tier, role):
      1. eligible = candidates of that tier that are `servable` AND whose
         scores[role] is not "unknown".
      2. winning_basis = the highest-priority benchmark in ROLE_BENCH[role] that
         ANY eligible candidate actually used (its scores[role].basis).
      3. Compare ONLY candidates whose basis == winning_basis (same scale), pick
         max. This refuses to rank e.g. a 70 LiveCodeBench above a 68 SWE-bench
         Verified as if they were the same number.
      4. If no eligible candidate -> {pick: None, reason: ...} (honestly empty).

    Returns {tier: {role: cell}}; every cell carries pick=id-or-None.
    """
    out = {}
    for tier in tiers:
        out[tier] = {}
        for role in roles:
            eligible = [c for c in scored
                        if c["tier"] == tier and c["servable"]
                        and c["scores"][role]["score"] != "unknown"]
            if not eligible:
                out[tier][role] = {
                    "role": role, "tier": tier, "pick": None,
                    "reason": f"no backed candidate for {tier}/{role}",
                }
                continue
            winning_basis = None
            for b in ROLE_BENCH[role]:
                if any(c["scores"][role].get("basis") == b for c in eligible):
                    winning_basis = b
                    break
            competitors = [c for c in eligible
                           if c["scores"][role].get("basis") == winning_basis]
            win = max(competitors, key=lambda c: c["scores"][role]["score"])
            rs = win["scores"][role]
            out[tier][role] = {
                "role": role, "tier": tier, "pick": win["id"],
                "id": win["id"], "repo": win["repo"], "source": win["source"],
                "basis": rs["basis"], "score": rs["score"],
                "provenance": rs["provenance"],
                "native_context": win["context"],
            }
    return out


# ---------------------------------------------------------------------------
# IMPURE: the ONLY fs/network path - the local on-box catalog.
# ---------------------------------------------------------------------------
def local_candidates():
    """Discover SGLang-servable local models via `_sync`, as candidate rows.

    Lazy-imports `_sync` (its module body does an os.makedirs side effect, so we
    keep it OUT of import time - --help / --self-check stay fs-free). Emits
    source="local" rows with the SAME shape as CANDIDATES. `servable` is the REAL
    `sglang_loadable` field (safetensors + not sm_120-hazardous). Benchmarks are
    taken straight from `_sync.summarize()`'s card extraction (string values;
    `bench_value` coerces them). Tier is a documented size heuristic only.
    """
    from . import _sync
    rows = []
    for owner, repo, d, kind in _sync.discover():
        try:
            s = _sync.summarize(owner, repo, d, kind)
        except Exception:
            continue
        if not _sync.is_real_model_row(s):
            continue
        size = s.get("size_gb") or 0
        tier = "fast" if 0 < size <= 22 else "heavy"   # deploy-fit heuristic, not a score
        rows.append({
            "id": s["id"], "tier": tier, "source": "local", "repo": s["id"],
            # context == NATIVE max only: config.json max_position_embeddings
            # (s["context"]). Deliberately NOT s["context_hint"], which is inferred
            # from card prose by regex - presenting that as a doc-stated native max
            # would be the same fabrication class as an unbounded benchmark digit.
            "context": s.get("context"),
            "servable": bool(s.get("sglang_loadable")),
            "benchmarks": s.get("benchmarks", {}) or {},
            "note": f"local card; {s.get('format')} {s.get('size_gb')}GB"
                    + (f"; sm_120: {s['sm120_caveat']}" if s.get("sm120_caveat") else ""),
        })
    return rows


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------
def _role_cell(rs):
    if rs["score"] == "unknown":
        return "unknown"
    return f"{_fmt(rs['score'])} ({rs['basis']})"


def _print_markdown(scored, mix):
    print("## Per-candidate role scores (coding / research / writing)\n")
    print("| Candidate | Tier | Source | Servable | Native ctx | Coding | Research | Writing |")
    print("|---|---|---|---|---|---|---|---|")
    for c in scored:
        ctx = c.get("context")
        ctxs = f"{int(ctx)//1024}K" if isinstance(ctx, int) and ctx >= 1024 else (ctx or "-")
        print("| {id} | {t} | {src} | {sv} | {ctx} | {co} | {re} | {wr} |".format(
            id=c["id"], t=c["tier"], src=c["source"],
            sv=("yes" if c["servable"] else "no"), ctx=ctxs,
            co=_role_cell(c["scores"]["coding"]),
            re=_role_cell(c["scores"]["research"]),
            wr=_role_cell(c["scores"]["writing"]),
        ))
    print("\n## Recommended mixture (per tier / role, with native max context)\n")
    for tier in TIERS:
        for role in ROLES:
            cell = mix[tier][role]
            label = f"**{tier.upper()} / {role}**"
            if cell.get("pick") is None:
                print(f"- {label}: - ({cell['reason']})")
            else:
                tc = cell.get("native_context")
                tcs = (f"{int(tc)//1024}K" if isinstance(tc, int) and tc >= 1024
                       else (tc if tc is not None else "unknown"))
                print(f"- {label}: `{cell['id']}` "
                      f"(score {_fmt(cell['score'])} via {cell['basis']}, "
                      f"native ctx {tcs}, source={cell['source']}) "
                      f"<- {cell['provenance']}")
    if EXCLUDED:
        print("\n## Explicitly excluded (kept honest)\n")
        for e in EXCLUDED:
            print(f"- `{e['id']}`: {e['reason']}")


# ---------------------------------------------------------------------------
# Self-check (PURE: no fs / GPU / network) - drives the pure functions on an
# in-module fixture. Aggregates every invariant into a single assert.
# ---------------------------------------------------------------------------
def _selfcheck():
    fixtures = [
        # heavy/coding: SWE-bench Verified only (value 68)
        {"id": "f-swe", "tier": "heavy", "source": "research", "repo": "x/swe",
         "context": 131072, "servable": True,
         "benchmarks": {"SWE-bench Verified": {"value": 68.0, "src": "fixture"}}},
        # heavy/coding: same basis, LOWER value (60) - must lose to f-swe
        {"id": "f-swe-low", "tier": "heavy", "source": "research", "repo": "x/swelow",
         "context": 131072, "servable": True,
         "benchmarks": {"SWE-bench Verified": {"value": 60.0, "src": "fixture"}}},
        # heavy/coding: LiveCodeBench only (70) - HIGHER number, DIFFERENT basis,
        # must NOT win coding (no cross-basis comparison)
        {"id": "f-lcb", "tier": "heavy", "source": "research", "repo": "x/lcb",
         "context": 131072, "servable": True,
         "benchmarks": {"LiveCodeBench": {"value": 70.0, "src": "fixture"}}},
        # heavy/research: MMLU-Pro only
        {"id": "f-mmlu", "tier": "heavy", "source": "research", "repo": "x/mmlu",
         "context": 131072, "servable": True,
         "benchmarks": {"MMLU-Pro": {"value": 82.7, "src": "fixture"}}},
        # fast: empty benchmarks -> all unknown
        {"id": "f-empty", "tier": "fast", "source": "research", "repo": "x/empty",
         "context": 65536, "servable": True, "benchmarks": {}},
        # fast: non-numeric local-style string -> unknown (bench_value -> None)
        {"id": "f-nonnum", "tier": "fast", "source": "local", "repo": "x/nonnum",
         "context": 65536, "servable": True,
         "benchmarks": {"SWE-bench Verified": "N/A"}},
        # heavy/coding: OUT-OF-RANGE local digit ('500' = _sync regex grabbing a
        # non-score) -> bounded out -> unknown -> MUST NOT win over f-swe (68).
        {"id": "f-oor", "tier": "heavy", "source": "local", "repo": "x/oor",
         "context": 131072, "servable": True,
         "benchmarks": {"SWE-bench Verified": "500"}},
        # fast/coding: capital-B 'SWE-Bench Verified' (the alternate spelling
        # _sync may store) with a real in-range value -> MUST be recognized.
        {"id": "f-capb", "tier": "fast", "source": "local", "repo": "x/capb",
         "context": 65536, "servable": True,
         "benchmarks": {"SWE-Bench Verified": "61.0"}},
    ]
    scored = score_all(fixtures)
    by = {c["id"]: c for c in scored}
    mix = select_mixture(scored)

    checks = []

    def chk(name, cond):
        checks.append((name, bool(cond)))

    # (1) SCORES DERIVED WITH PROVENANCE - value equals the fixture's input, and
    #     provenance names the originating benchmark + value + source.
    swe = by["f-swe"]["scores"]["coding"]
    chk("swe coding value", swe["score"] == 68.0)
    chk("swe coding basis", swe["basis"] == "SWE-bench Verified")
    chk("swe coding provenance names bench", "SWE-bench Verified" in swe["provenance"])
    chk("swe coding provenance names value", "68" in swe["provenance"])
    chk("swe coding provenance names source", "research" in swe["provenance"])
    lcb = by["f-lcb"]["scores"]["coding"]
    chk("lcb coding value", lcb["score"] == 70.0)
    chk("lcb coding basis", lcb["basis"] == "LiveCodeBench")
    mmlu = by["f-mmlu"]["scores"]["research"]
    chk("mmlu research value", mmlu["score"] == 82.7)
    chk("mmlu research basis", mmlu["basis"] == "MMLU-Pro")

    # (2) UNKNOWN STAYS UNKNOWN (never invented / never coerced to a number).
    chk("mmlu coding unknown", by["f-mmlu"]["scores"]["coding"]["score"] == "unknown")
    for r in ROLES:
        chk(f"empty {r} unknown", by["f-empty"]["scores"][r]["score"] == "unknown")
    chk("nonnum coding unknown", by["f-nonnum"]["scores"]["coding"]["score"] == "unknown")
    # writing unknown for ALL candidates (no Arena/MT-Bench/AlpacaEval anywhere)
    chk("writing unknown for all",
        all(c["scores"]["writing"]["score"] == "unknown" for c in scored))
    # no "unknown" is ever an int/float
    for c in scored:
        for r in ROLES:
            sc = c["scores"][r]["score"]
            if sc == "unknown":
                chk(f"{c['id']}/{r} unknown not numeric", not isinstance(sc, (int, float)))

    # bench_value coercion: both shapes + non-numeric + missing.
    chk("bench_value string", bench_value({"SWE-bench Verified": "62.4"}, "SWE-bench Verified") == 62.4)
    chk("bench_value dict", bench_value({"k": {"value": 56}}, "k") == 56.0)
    chk("bench_value non-numeric None", bench_value({"k": "N/A"}, "k") is None)
    chk("bench_value missing None", bench_value({}, "k") is None)
    # bench_value BOUND [0,100]: out-of-range degrades, in-range edges pass.
    chk("bench_value out-of-range None", bench_value({"k": "500"}, "k") is None)
    chk("bench_value negative None", bench_value({"k": "-5"}, "k") is None)
    chk("bench_value 0 ok", bench_value({"k": "0"}, "k") == 0.0)
    chk("bench_value 100 ok", bench_value({"k": "100"}, "k") == 100.0)
    # bench_value CASE-INSENSITIVE key (SWE-Bench vs SWE-bench).
    chk("bench_value capital-B key",
        bench_value({"SWE-Bench Verified": "61"}, "SWE-bench Verified") == 61.0)
    # f-oor: out-of-range local value derives to unknown (never a backed score).
    chk("oor coding unknown", by["f-oor"]["scores"]["coding"]["score"] == "unknown")
    # f-capb: capital-B local key IS recognized as a real backed coding score.
    capb = by["f-capb"]["scores"]["coding"]
    chk("capb coding recognized", capb["score"] == 61.0)
    chk("capb coding basis normalized", capb["basis"] == "SWE-bench Verified")

    # (3) SAME-BASIS MIXTURE per tier/role.
    hc = mix["heavy"]["coding"]
    chk("heavy/coding picks SWE winner (not LCB 70, not SWE 60, not oor 500)",
        hc.get("pick") == "f-swe")
    chk("heavy/coding basis is SWE", hc.get("basis") == "SWE-bench Verified")
    chk("heavy/coding native context", hc.get("native_context") == 131072)
    # the out-of-range value can NEVER win the mixture.
    chk("heavy/coding winner is not f-oor", hc.get("pick") != "f-oor")
    chk("heavy/research picks MMLU", mix["heavy"]["research"].get("pick") == "f-mmlu")
    hw = mix["heavy"]["writing"]
    chk("heavy/writing empty", hw.get("pick") is None and "reason" in hw)
    # fast/coding: f-capb's capital-B SWE score IS recognized and wins.
    fc = mix["fast"]["coding"]
    chk("fast/coding picks capital-B SWE candidate", fc.get("pick") == "f-capb")
    chk("fast/coding basis normalized", fc.get("basis") == "SWE-bench Verified")

    # (4) PROVENANCE: every backed score from a RESEARCH candidate cites the ONE
    #     canonical findings doc path, consistently - so provenance can't drift.
    research_provs = [c["scores"][r]["provenance"]
                      for c in score_all(CANDIDATES)
                      for r in ROLES
                      if c["source"] == "research"
                      and c["scores"][r]["score"] != "unknown"]
    chk("research catalog has backed scores", len(research_provs) > 0)
    chk("every research score cites the findings doc",
        all(FINDINGS_DOC in p for p in research_provs))
    # local-sourced backed scores must NOT borrow the research doc citation.
    chk("local score does not cite findings doc",
        FINDINGS_DOC not in by["f-capb"]["scores"]["coding"]["provenance"])

    failed = [n for n, ok in checks if not ok]
    assert not failed, "score self-check FAILED: " + ", ".join(failed)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="anvil-serving score",
        description="Role-suitability scorer: derive coding/research/writing "
                    "scores from real benchmarks (with provenance) and recommend "
                    "a mixture per tier/role. Never fabricates a score.")
    p.add_argument("--self-check", action="store_true",
                   help="run the pure-function self-check (no fs/network) and exit")
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of markdown")
    p.add_argument("--no-local", action="store_true",
                   help="skip local-catalog discovery (offline/fast; research list only)")
    args = p.parse_args(argv)

    if args.self_check:
        _selfcheck()
        print("score self-check: OK")
        return 0

    cands = list(CANDIDATES)
    locals_ = []
    if not args.no_local:
        try:
            locals_ = local_candidates()
        except Exception as e:  # noqa: BLE001 - never let discovery sink the report
            print(f"# note: local catalog skipped ({type(e).__name__}: {e})")
    scored = score_all(locals_ + cands)
    mix = select_mixture(scored)

    if args.json:
        print(_json.dumps({"candidates": scored, "mixture": mix, "excluded": EXCLUDED},
                          indent=2))
    else:
        _print_markdown(scored, mix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
