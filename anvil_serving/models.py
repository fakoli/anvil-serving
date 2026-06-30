"""`anvil-serving models sync` — scan HF caches + model dirs, pull cards, build the catalog."""
import os
import argparse
import subprocess
import sys
from . import config
HERE = os.path.dirname(__file__)

def main(argv):
    ap = argparse.ArgumentParser(prog="anvil-serving models")
    ap.add_argument("action", choices=["sync"], help="sync = refresh the catalog")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "model-library"),
                    help="output dir for cards/ + INDEX.md")
    ap.add_argument("--hf-roots", default="", help="extra HF cache roots (os.pathsep-separated)")
    ap.add_argument("--model-dirs", default="", help="extra plain model dirs (os.pathsep-separated)")
    a = ap.parse_args(argv)
    os.makedirs(os.path.join(a.out, "cards"), exist_ok=True)
    roots = os.pathsep.join(config.hf_cache_roots(a.hf_roots.split(os.pathsep) if a.hf_roots else None))
    env = dict(os.environ, ANVIL_MODELS_OUT=a.out)
    if roots: env["ANVIL_HF_ROOTS"] = roots
    if a.model_dirs: env["ANVIL_MODEL_DIRS"] = a.model_dirs
    return subprocess.call([sys.executable, os.path.join(HERE, "_sync.py")], env=env)
