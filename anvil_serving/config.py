"""Config + cross-platform auto-detection (no hardcoded user paths)."""
import os
import glob

def claude_logs_dir():
    return os.environ.get("ANVIL_CLAUDE_LOGS") or os.path.expanduser("~/.claude/projects")

def _candidate_hf_caches():
    c = []
    # HF_HOME is the cache ROOT (hub/ lives under it); HF_HUB_CACHE and
    # HUGGINGFACE_HUB_CACHE already point AT the hub dir — appending "hub" to
    # those made e.g. HF_HUB_CACHE=/data/hf-cache silently miss as
    # /data/hf-cache/hub.
    v = os.environ.get("HF_HOME")
    if v:
        c.append(v if v.endswith("hub") else os.path.join(v, "hub"))
    for env in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        v = os.environ.get(env)
        if v:
            c.append(v)
    c.append(os.path.expanduser("~/.cache/huggingface/hub"))
    # Windows user profile (when run from WSL, /mnt/c/Users/<user>/.cache/...)
    up = os.environ.get("USERPROFILE")
    if up: c.append(os.path.join(up, ".cache", "huggingface", "hub"))
    for u in glob.glob("/mnt/c/Users/*/.cache/huggingface/hub"):
        c.append(u)
    return c

def hf_cache_roots(extra=None):
    seen, out = set(), []
    for p in (_candidate_hf_caches() + list(extra or [])):
        p = os.path.normpath(p)
        if p not in seen and os.path.isdir(p):
            seen.add(p); out.append(p)
    return out

def load(path=None):
    """Load optional TOML config; returns a dict with sane defaults."""
    cfg = dict(claude_logs=claude_logs_dir(), hf_extra_roots=[], model_dirs=[],
               gpu_index=0, served_model_name="local-specialist")
    if path and os.path.isfile(path):
        try:
            import tomllib
            with open(path, "rb") as f: cfg.update(tomllib.load(f))
        except Exception as e:
            import sys
            print("warn: could not read config:", e, file=sys.stderr)
    return cfg
