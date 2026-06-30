#!/usr/bin/env python3
"""sync_model_cards.py - catalog local models: pull HF cards + local config, extract serving facts.

Scans HuggingFace caches and plain model dirs, downloads each model's README card,
reads local config.json / generation_config.json, extracts the fields that matter for
SERVING (format GGUF-vs-safetensors, quant, context, license, thinking-default,
recommended sampling, benchmarks, serving notes), and writes:
  cards/<owner>__<repo>.md        (raw card)
  cards/<owner>__<repo>.json      (extracted summary)
  INDEX.md                        (master table)

Runs on the host (WSL python): sees /home/<user>/.cache + /mnt/c/... + the internet.
Stdlib only. Public models need no token; set HF_TOKEN env for gated ones.
"""
import os
import re
import json
import glob
import time
import urllib.request
import urllib.error

HERE = os.environ.get("ANVIL_MODELS_OUT") or os.path.join(os.getcwd(), "model-library")
CARDS = os.path.join(HERE, "cards")
STATE = os.path.join(HERE, "_seen.json")
os.makedirs(CARDS, exist_ok=True)

# Scan roots: (path, kind). HF caches use models--owner--repo; "dir" = plain model folders.
def _auto_roots():
    import glob as _g
    roots, seen = [], set()
    def add(p, kind):
        p = os.path.normpath(p)
        if p not in seen and os.path.isdir(p): seen.add(p); roots.append((p, kind))
    env = os.environ.get("ANVIL_HF_ROOTS")
    if env:
        for p in env.split(os.pathsep): add(p, "hf")
    add(os.path.expanduser("~/.cache/huggingface/hub"), "hf")
    up = os.environ.get("USERPROFILE")
    if up: add(os.path.join(up, ".cache", "huggingface", "hub"), "hf")
    for u in _g.glob("/mnt/c/Users/*/.cache/huggingface/hub"): add(u, "hf")
    for d in (os.environ.get("ANVIL_MODEL_DIRS") or "").split(os.pathsep):
        if d: add(d, "dir")
    for u in _g.glob("/mnt/c/Users/*/models"): add(u, "dir")
    return roots
ROOTS = _auto_roots()

# --- sm_120 (Blackwell) SGLang loadability hazards -------------------------
# A safetensors model is normally SGLang-loadable, BUT some quant+arch combos
# load "successfully" then hang or return silent zeros on sm_120. Encode that
# judgment as a small table; add a row when a new case appears (don't abstract).
# Refs: FP8-MoE hang -> sglang#16816 ; NVFP4 GEMM silent zeros ->
#       flashinfer#2577, vllm#24921.
SM120_HAZARDS = [
    # (quant substring, requires_moe, caveat)
    ("fp8",   True,  "FP8-MoE hangs post-load on sm_120 (sglang#16816)"),
    ("nvfp4", False, "NVFP4 GEMM broken on sm_120: silent zeros (flashinfer#2577, vllm#24921)"),
]

# Standard MoE config keys across families: num_experts (generic), num_local_experts
# (Mixtral / gpt-oss), n_routed_experts (DeepSeek-V3). Plus model_type/arch fallback.
_MOE_KEYS = ("num_experts", "num_local_experts", "n_routed_experts")
_MOE_SUBSTRINGS = ("moe", "mixtral", "deepseek", "qwen3_moe", "gpt_oss")

def _is_moe(cfg, txt):
    if any(isinstance(d, dict) and any(k in d for k in _MOE_KEYS) for d in (cfg, txt)):
        return True
    blob = (str(cfg.get("model_type") or "") + " " +
            " ".join(str(x) for x in (cfg.get("architectures") or []))).lower()
    return any(s in blob for s in _MOE_SUBSTRINGS)

def _parse_quant(cfg):
    """Derive (qmethod, qbits, qsig) from cfg's quantization_config.

    qsig folds BOTH the raw quant strings (catches native-fp8 repos whose method
    is literally 'fp8') AND tokens derived from the quantized WEIGHT spec.
    compressed-tensors reports quant_method='compressed-tensors',
    format='float-quantized' and hides the real precision in
    config_groups[*].weights {num_bits, type}; surface it as fp8/nvfp4.
    MXFP4 (gpt-oss) is servable via triton, so it is never folded to nvfp4.
    """
    quant = cfg.get("quantization_config") or {}     # JSON null -> {} (no AttributeError)
    qmethod = quant.get("quant_method") or quant.get("format")
    qbits, qtype = None, ""
    for g in (quant.get("config_groups") or {}).values():
        w = (g or {}).get("weights") or {}
        if w.get("num_bits"):
            qbits = w["num_bits"]
            qtype = (w.get("type") or "").lower()
    qsig = (str(qmethod or "") + " " + str(quant.get("format") or "")).lower()
    if qbits == 8 and qtype == "float":
        qsig += " fp8"
    elif qbits == 4 and "mxfp4" not in qsig and ("float" in qtype or "float" in qsig):
        qsig += " nvfp4"
    return qmethod, qbits, qsig

def sm120_hazard(fmt, qsig, cfg, txt):
    """safetensors quant+arch hazardous on sm_120 -> caveat string, else None."""
    if fmt != "safetensors":
        return None
    q = (qsig or "").lower()
    moe = _is_moe(cfg, txt)
    for sub, requires_moe, caveat in SM120_HAZARDS:
        if sub in q and (moe or not requires_moe):
            return caveat
    return None

def dir_size_bytes(p):
    """Raw on-disk byte total for a model dir (deduping HF blob hardlinks)."""
    blobs = os.path.join(p, "blobs")
    seen, t = set(), 0
    targets = [blobs] if os.path.isdir(blobs) else [p]
    for base in targets:
        for dp, _, fns in os.walk(base):
            for f in fns:
                try:
                    rp = os.path.realpath(os.path.join(dp, f))
                    if rp in seen: continue
                    seen.add(rp); t += os.path.getsize(rp)
                except OSError: pass
    return t

def dir_size_gb(p):
    """Display size in GB (rounded to 0.1). Use dir_size_bytes for exact totals."""
    return round(dir_size_bytes(p)/1e9, 1)

def newest_snapshot(model_dir):
    snaps = glob.glob(os.path.join(model_dir, "snapshots", "*"))
    snaps = [s for s in snaps if os.path.isdir(s)]
    # prefer the snapshot that actually has config.json / weights
    snaps.sort(key=lambda s: (os.path.exists(os.path.join(s,"config.json")),
                              len(os.listdir(s)), os.path.getmtime(s)), reverse=True)
    return snaps[0] if snaps else None

def load_json(p):
    try:
        with open(p, encoding="utf-8") as f: v = json.load(f)
        return v if isinstance(v, dict) else {}   # non-dict JSON -> {} (no .get crash)
    except Exception: return {}

def detect_format(d):
    g = glob.glob(os.path.join(d, "**", "*.gguf"), recursive=True)
    s = glob.glob(os.path.join(d, "**", "*.safetensors"), recursive=True)
    if g: return "GGUF"
    if s: return "safetensors"
    return "?"

def fetch_card(owner, repo):
    url = f"https://huggingface.co/{owner}/{repo}/raw/main/README.md"
    req = urllib.request.Request(url, headers={"User-Agent":"model-card-sync"})
    tok = os.environ.get("HF_TOKEN")
    if tok: req.add_header("Authorization", "Bearer "+tok)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None

def parse_frontmatter(card):
    fm = {}
    if card and card.startswith("---"):
        end = card.find("\n---", 3)
        if end > 0:
            for line in card[3:end].splitlines():
                m = re.match(r"\s*([A-Za-z_]+):\s*(.+?)\s*$", line)
                if m: fm[m.group(1).lower()] = m.group(2).strip()
    return fm

def extract_from_card(card):
    out = {}
    if not card: return out
    low = card.lower()
    out["thinking_default"] = ("thinking mode" in low and "default" in low) or "enable_thinking" in low
    # first recommended sampling line
    m = re.search(r"temperature\s*=\s*([0-9.]+).{0,80}?top_p\s*=\s*([0-9.]+)", card, re.S|re.I)
    if m: out["sampling_hint"] = f"temp={m.group(1)}, top_p={m.group(2)}"
    # context length mentions
    ctxs = re.findall(r"([0-9][0-9,]{3,})\s*(?:tokens|context)", card, re.I)
    if ctxs: out["context_hint"] = max(int(c.replace(",","")) for c in ctxs)
    # benchmark hints
    for bench in ["SWE-bench Verified","SWE-Bench Verified","Terminal-Bench","BFCL","TAU2","LiveCodeBench"]:
        m = re.search(re.escape(bench)+r"[^0-9]{0,30}([0-9]{1,3}\.?[0-9]?)", card)
        if m: out.setdefault("benchmarks", {})[bench] = m.group(1)
    out["mentions_sglang"] = "sglang" in low
    out["mentions_vllm"] = "vllm" in low
    return out

def summarize(owner, repo, model_dir, kind):
    snap = newest_snapshot(model_dir) if kind == "hf" else model_dir
    cfg = load_json(os.path.join(snap, "config.json")) if snap else {}
    gen = load_json(os.path.join(snap, "generation_config.json")) if snap else {}
    txt = cfg.get("text_config", {}) if isinstance(cfg.get("text_config"), dict) else {}
    qmethod, qbits, qsig = _parse_quant(cfg)
    fmt = detect_format(snap) if snap else "?"
    sm120 = sm120_hazard(fmt, qsig, cfg, txt)
    s = dict(
        id=f"{owner}/{repo}" if owner else repo,
        owner=owner, repo=repo, local_path=model_dir, source=kind,
        size_gb=dir_size_gb(model_dir),
        format=fmt,
        architectures=cfg.get("architectures"),
        model_type=cfg.get("model_type"),
        context=cfg.get("max_position_embeddings") or txt.get("max_position_embeddings"),
        quant=qmethod, quant_bits=qbits,
        gen_sampling={k:gen[k] for k in ("temperature","top_p","top_k") if k in gen},
        sglang_loadable=(fmt == "safetensors" and not sm120),
        sm120_caveat=sm120,
        synced=time.strftime("%Y-%m-%d %H:%M"),
    )
    card = fetch_card(owner, repo) if owner else None
    if card:
        open(os.path.join(CARDS, f"{owner}__{repo}.md"), "w", encoding="utf-8").write(card)
        fm = parse_frontmatter(card)
        s["license"] = fm.get("license")
        s["pipeline_tag"] = fm.get("pipeline_tag")
        s["base_model"] = fm.get("base_model")
        s.update(extract_from_card(card))
        s["card_saved"] = f"cards/{owner}__{repo}.md"
    else:
        s["card_saved"] = None
    open(os.path.join(CARDS, f"{owner}__{repo}.json" if owner else f"{repo}.json"), "w",
         encoding="utf-8").write(json.dumps(s, indent=1))
    return s

def discover():
    found = []
    for root, kind in ROOTS:
        if not os.path.isdir(root): continue
        if kind == "hf":
            for d in glob.glob(os.path.join(root, "models--*")):
                base = os.path.basename(d)
                parts = base.split("--")
                if len(parts) < 3: continue
                owner, repo = parts[1], "--".join(parts[2:])
                found.append((owner, repo, d, "hf"))
        else:
            for d in glob.glob(os.path.join(root, "*")):
                if os.path.isdir(d):
                    cfg = load_json(os.path.join(d, "config.json"))
                    nm = cfg.get("_name_or_path") or os.path.basename(d)
                    owner, repo = (nm.split("/",1)+[None])[:2] if "/" in str(nm) else (None, os.path.basename(d))
                    found.append((owner, repo, d, "dir"))
    return found

def is_real_model_row(r):
    """SHARED 3-part real-model gate (used by write_index AND cache_prune).

    A row is a real servable model only if all three hold:
      1. not an unslothai mirror,
      2. not a tiny (<0.2GB) format-unknown dir,
      3. POSITIVE evidence it is a model: a model_type OR a weights format.
    The third gate is decisive — without it a dataset / partial download /
    tokenizer-only dir (no weights, no model_type, >=0.2GB) would slip through
    and, in cache_prune, become a DELETION candidate. Keep the three parts here
    so the prune planner and the index can never drift apart.
    """
    if r.get("owner") == "unslothai": return False
    if (r.get("size_gb") or 0) < 0.2 and r.get("format") == "?": return False
    return bool(r.get("model_type")) or r.get("format") in ("safetensors", "GGUF")

def write_index(rows):
    rows = [r for r in rows if is_real_model_row(r)]
    rows.sort(key=lambda r: (r["format"] != "safetensors", -(r.get("size_gb") or 0)))
    L = ["# Model Library — Index", "",
         f"_Auto-generated by `sync_model_cards.py` — {time.strftime('%Y-%m-%d %H:%M')}. "
         f"{len(rows)} models. Cards in `cards/`._", "",
         "| Model | Format | SGLang? | Params/size | Context | Quant | License | Thinking | Coding bench | Local |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        bench = ""
        b = r.get("benchmarks") or {}
        for k in ("SWE-bench Verified","SWE-Bench Verified","Terminal-Bench"):
            if k in b: bench = f"{k.split('-')[0]} {b[k]}"; break
        ctx = r.get("context") or r.get("context_hint") or ""
        ctx = f"{int(ctx)//1024}K" if str(ctx).isdigit() and int(ctx)>=1024 else (ctx or "")
        L.append("| {id} | {fmt} | {ok} | {sz} GB | {ctx} | {q} | {lic} | {th} | {bn} | {src} |".format(
            id=r["id"], fmt=r.get("format","?"),
            ok=("✅" if r.get("sglang_loadable")
                else "⚠️ sm_120" if r.get("sm120_caveat")
                else "❌ (llama.cpp)" if r.get("format")=="GGUF"
                else "?"),
            sz=r.get("size_gb","?"), ctx=ctx,
            q=(f"{r.get('quant') or ''} {r.get('quant_bits') or ''}".strip() or "—"),
            lic=r.get("license") or "—",
            th=("yes" if r.get("thinking_default") else "—"),
            bn=bench or "—", src=("win" if "/mnt/c" in r.get("local_path","") else "wsl")))
    open(os.path.join(HERE, "INDEX.md"), "w", encoding="utf-8").write("\n".join(L)+"\n")

def main():
    models = discover()
    print(f"discovered {len(models)} model folders")
    rows = []
    for owner, repo, d, kind in models:
        try:
            s = summarize(owner, repo, d, kind)
            rows.append(s)
            print(f"  [{s.get('format'):>11}] {s['id']}  {s.get('size_gb')}GB  card={'y' if s.get('card_saved') else 'n'}")
        except Exception as e:
            print(f"  ERROR {owner}/{repo}: {e}")
    write_index(rows)
    # new-model detection (vs last run) for the Cowork analysis task
    real_ids = [r["id"] for r in rows if r.get("format") in ("safetensors","GGUF")]
    prior = set((load_json(STATE) or {}).get("ids", []))
    new_ids = [i for i in real_ids if i not in prior]
    json.dump({"ids": real_ids, "updated": time.strftime("%Y-%m-%d %H:%M")},
              open(STATE, "w", encoding="utf-8"), indent=1)
    print(f"wrote INDEX.md + {len(rows)} summaries to {HERE}")
    print("NEW_MODELS: " + (", ".join(new_ids) if new_ids else "none"))

def _loadable(cfg, fmt="safetensors"):
    """Mirror summarize()'s decision via the same derivation, for self-check."""
    _, _, qsig = _parse_quant(cfg)
    sm120 = sm120_hazard(fmt, qsig, cfg, cfg.get("text_config") or {})
    return (fmt == "safetensors" and not sm120), sm120

def _selfcheck():
    # --- REAL derivation path: compressed-tensors hides FP8-ness in the weight
    #     spec (num_bits=8/type=float), NOT in the method string. ---
    ct_fp8 = {"quant_method": "compressed-tensors", "format": "float-quantized",
              "config_groups": {"group_0": {"weights": {"num_bits": 8, "type": "float"}}}}
    # FP8 + MoE (num_local_experts, Mixtral/gpt-oss style) must NOT be clean-loadable.
    cfg_fp8_moe = {"quantization_config": ct_fp8, "num_local_experts": 128,
                   "architectures": ["Qwen3MoeForCausalLM"]}
    ok, caveat = _loadable(cfg_fp8_moe)
    assert not ok and caveat, "compressed-tensors FP8 on MoE must caveat (not clean)"
    # FP8 + MoE via DeepSeek-V3 key n_routed_experts must also caveat.
    cfg_fp8_ds = {"quantization_config": ct_fp8, "n_routed_experts": 256}
    ok, caveat = _loadable(cfg_fp8_ds)
    assert not ok and caveat, "compressed-tensors FP8 on DeepSeek MoE must caveat"
    # NVFP4 via weight spec (num_bits=4/type=float) caveats on any arch.
    ct_nvfp4 = {"quantization_config": {"quant_method": "compressed-tensors",
                "config_groups": {"g": {"weights": {"num_bits": 4, "type": "float"}}}}}
    ok, caveat = _loadable(ct_nvfp4)
    assert not ok and caveat, "compressed-tensors NVFP4 must caveat on any arch"
    # gpt-oss MXFP4 is SERVABLE via triton -> must NOT be flagged.
    gptoss = {"quantization_config": {"quant_method": "mxfp4"},
              "model_type": "gpt_oss", "num_local_experts": 128}
    ok, caveat = _loadable(gptoss)
    assert ok and caveat is None, "gpt-oss MXFP4 is servable, must not be flagged"
    # Dense AWQ (int4) stays clean.
    awq = {"quantization_config": {"quant_method": "awq",
            "config_groups": {"g": {"weights": {"num_bits": 4, "type": "int"}}}}}
    ok, caveat = _loadable(awq)
    assert ok and caveat is None, "dense AWQ (int4) stays clean-loadable"
    # JSON null quantization_config must not crash.
    ok, _ = _loadable({"quantization_config": None})
    assert ok, "null quantization_config -> clean, no AttributeError"

    # --- Legacy string-folding path still holds (native-fp8 repos etc.). ---
    assert sm120_hazard("safetensors", "fp8", {"num_experts": 128}, {}), "FP8-MoE should caveat"
    assert sm120_hazard("safetensors", "nvfp4", {}, {}), "NVFP4 caveats on any arch"
    assert sm120_hazard("safetensors", "fp8", {}, {}) is None, "dense FP8 is fine"
    assert sm120_hazard("safetensors", "awq", {"num_experts": 128}, {}) is None, "AWQ-MoE clean"
    assert sm120_hazard("safetensors", "awq", {}, {}) is None, "dense AWQ clean"
    assert sm120_hazard("GGUF", "fp8", {"num_experts": 128}, {}) is None, "GGUF handled elsewhere"

if __name__ == "__main__":
    _selfcheck()
    main()
