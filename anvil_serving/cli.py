"""anvil-serving CLI — profile / models / deploy / init / serves / serve / router / harness / preflight / benchmark / external-bench / eval / calibrate / multiplexer / doctor."""
import sys
import os
import subprocess

HERE = os.path.dirname(__file__)

MIN_PYTHON = (3, 11)

def _check_python_version(version_info=None):
    """Return an error message if running under an unsupported interpreter, else None."""
    vi = version_info if version_info is not None else sys.version_info
    if (vi[0], vi[1]) < MIN_PYTHON:
        return "anvil-serving needs Python >=%d.%d; you have %d.%d" % (
            MIN_PYTHON[0], MIN_PYTHON[1], vi[0], vi[1],
        )
    return None

def _run_script(name, argv, env=None):
    e = dict(os.environ); e.update(env or {})
    return subprocess.call([sys.executable, os.path.join(HERE, name)] + argv, env=e)

def main(argv=None):
    _version_error = _check_python_version()
    if _version_error:
        print(_version_error, file=sys.stderr)
        return 1
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__ + "\n  commands: profile | models | deploy | init (alias: onboard) | serves | serve | router | "
                        "preflight | benchmark | external-bench | eval | calibrate | multiplexer | cache-prune | score | doctor"); return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "serve":       from .router.serve import main as _serve_main; return _serve_main(rest)
    if cmd == "serves":      from . import serves; return serves.main(rest)
    if cmd == "router":      from . import router_manage; return router_manage.main(rest)
    if cmd == "harness":     from . import harness; return harness.main(rest)
    if cmd == "eval":        from . import eval as _eval; return _eval.main(rest)
    if cmd == "calibrate":   from . import calibrate as _calibrate; return _calibrate.main(rest)
    if cmd == "score":       from . import score; return score.main(rest)
    if cmd == "multiplexer": from . import multiplexer; return multiplexer.main(rest)
    if cmd == "cache-prune": from . import cache_prune; return cache_prune.main(rest)
    if cmd == "preflight":   return _run_script("preflight.py", rest)
    if cmd == "benchmark":   return _run_script("benchmark.py", rest)
    if cmd == "external-bench": from .external_benchmarks import cli as _external_bench; return _external_bench.main(rest)
    if cmd == "deploy":      from . import deploy; return deploy.main(rest)
    if cmd in ("init", "onboard"): from . import init as _init; return _init.main(rest)
    if cmd == "doctor":      from . import doctor; return doctor.main(rest)
    if cmd == "models":      from . import models; return models.main(rest)
    if cmd == "profile":     from . import profile; return profile.main(rest)
    print("unknown command:", cmd); return 2

if __name__ == "__main__":
    raise SystemExit(main())
