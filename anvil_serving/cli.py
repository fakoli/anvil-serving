"""anvil-serving CLI — profile / models / deploy / preflight / benchmark / multiplexer / serve."""
import sys, os, runpy, subprocess

HERE = os.path.dirname(__file__)

def _run_script(name, argv, env=None):
    e = dict(os.environ); e.update(env or {})
    return subprocess.call([sys.executable, os.path.join(HERE, name)] + argv, env=e)

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__ + "\n  commands: profile | models | deploy | preflight | benchmark | multiplexer | cache-prune | score | serve"); return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "serve":       from .router import serve as _serve; return _serve.main(rest)
    if cmd == "score":       from . import score; return score.main(rest)
    if cmd == "multiplexer": from . import multiplexer; return multiplexer.main(rest)
    if cmd == "cache-prune": from . import cache_prune; return cache_prune.main(rest)
    if cmd == "preflight":   return _run_script("preflight.py", rest)
    if cmd == "benchmark":   return _run_script("benchmark.py", rest)
    if cmd == "deploy":      from . import deploy; return deploy.main(rest)
    if cmd == "models":      from . import models; return models.main(rest)
    if cmd == "profile":     from . import profile; return profile.main(rest)
    print("unknown command:", cmd); return 2

if __name__ == "__main__":
    raise SystemExit(main())
