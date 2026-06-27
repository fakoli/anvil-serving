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
import os, re, json, glob, sys, time, urllib.request, urllib.error

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

def dir_size_gb(p):
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
    return round(t/1e9, 1)

def newest_snapshot(model_dir):
    snaps = glob.glob(os.path.join(model_dir, "snapshots", "*"))
    snaps = [s for s in snaps if os.path.isdir(s)]
    # prefer the snapshot that actually has config.json / weights
    snaps.sort(key=lambda s: (os.path.exists(os.path.join(s,"config.json")),
                              len(os.listdir(s)), os.path.getmtime(s)), reverse=True)
    return snaps[0] if snaps else None

def load_json(p):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
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
    except Exception as e:
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
    quant = cfg.get("quantization_config", {})
    qmethod = quant.get("quant_method") or quant.get("format")
    qbits = None
    cg = (quant.get("config_groups") or {})
    for g in cg.values():
        w = (g or {}).get("weights") or {}
        if w.get("num_bits"): qbits = w["num_bits"]
    fmt = detect_format(snap) if snap else "?"
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
        sglang_loadable=(fmt=="safetensors"),
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

def write_index(rows):
    def real(r):
        if r.get("owner")=="unslothai": return False
        if (r.get("size_gb") or 0) < 0.2 and r.get("format")=="?": return False
        return bool(r.get("model_type")) or r.get("format") in ("safetensors","GGUF")
    rows = [r for r in rows if real(r)]
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
            ok=("✅" if r.get("sglang_loadable") else "❌ (llama.cpp)" if r.get("format")=="GGUF" else "?"),
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

if __name__ == "__main__":
    main()
