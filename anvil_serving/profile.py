"""`anvil-serving profile` — turn your Claude Code logs into a usage baseline + sizing inputs."""
import os
import argparse
import subprocess
import sys
from . import config
HERE = os.path.dirname(__file__)

def main(argv):
    ap = argparse.ArgumentParser(prog="anvil-serving profile")
    ap.add_argument("--logs-dir", default=config.claude_logs_dir(),
                    help="Claude Code session logs (default ~/.claude/projects)")
    ap.add_argument("--out-dir", default=os.getcwd())
    a = ap.parse_args(argv)
    env = dict(os.environ, ANVIL_CLAUDE_LOGS=a.logs_dir)
    agg = os.path.join(a.out_dir, "usage_aggregate.json")
    rs  = os.path.join(a.out_dir, "role_split.json")
    subprocess.call([sys.executable, os.path.join(HERE, "_aggregate_usage.py"), "--out", agg], env=env)
    subprocess.call([sys.executable, os.path.join(HERE, "_role_split.py"), "--out", rs], env=env)
    print(f"\nwrote {agg} and {rs}")
    print("Size your local serve from these percentiles — see docs/USAGE-BASELINE-METHOD.md and docs/BLUEPRINT.md.")
    return 0
