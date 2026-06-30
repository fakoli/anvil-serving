"""anvil-serving eval — one entry point for the project's evaluations.

There are four evals in this repo, with three different invocation styles. This
verb makes them uniform and fills in the fakoli-dark topology so the common case
is one line:

  eval preflight [--tier heavy|fast]   correctness gate vs a live endpoint
  eval benchmark [--tier heavy|fast]   throughput / request-replay vs a live endpoint
  eval planning  [--offline]           planning-capability bake-off; --offline
                                       re-grades the committed eval-data
                                       deterministically (no serves needed)
  eval bootstrap                       replay the committed eval fixtures into a
                                       quality profile (no serves needed)

`preflight`/`benchmark` resolve `--base-url`/`--model` from the serves manifest
(examples/fakoli-dark/serves.toml), so `eval preflight --tier fast` just works
when that serve is up — and prints a `serves up` hint when it isn't. stdlib-only.
"""
import argparse
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EVAL_DATA_ROOT = os.path.join(REPO, "docs", "findings", "eval-data")
PLANNING_DIR = os.path.join(EVAL_DATA_ROOT, "2026-06-28-planning-capability")


def _tiers():
    """tier name -> {base_url, model, port, health, container} from the manifest."""
    from . import serves
    out = {}
    try:
        for s in serves.load_manifest(serves.DEFAULT_MANIFEST):
            if s.get("model"):
                out[s["name"]] = {
                    "base_url": "http://127.0.0.1:%s/v1" % s["port"],
                    "model": s["model"], "port": s["port"],
                    "health": s.get("health", "/health"), "container": s["container"]}
    except Exception:
        pass
    return out


def _reachable(port, path, _open=urllib.request.urlopen):
    try:
        with _open("http://127.0.0.1:%s%s" % (port, path), timeout=2):
            return True
    except Exception:
        return False


def _run_endpoint_eval(script, a, _call=subprocess.call, _open=urllib.request.urlopen):
    """Shell preflight.py / benchmark.py, defaulting base-url/model from a tier."""
    base_url, model = a.base_url, a.model
    if a.tier:
        tiers = _tiers()
        if a.tier not in tiers:
            print("unknown tier %r; manifest tiers: %s"
                  % (a.tier, ", ".join(tiers) or "(none)"), file=sys.stderr)
            return 2
        t = tiers[a.tier]
        base_url = base_url or t["base_url"]
        model = model or t["model"]
        if not _reachable(t["port"], t["health"], _open=_open):
            print("tier %r (%s) is not reachable at %s\n  start it:  anvil-serving serves up %s"
                  % (a.tier, t["container"], base_url, a.tier), file=sys.stderr)
            return 3
    if not base_url or not model:
        print("need --tier, or both --base-url and --model", file=sys.stderr)
        return 2
    argv = ["--base-url", base_url, "--model", model] + a.extra
    return _call([sys.executable, os.path.join(HERE, script)] + argv)


def _run_planning(a, _call=subprocess.call):
    d = a.dir
    rc = 0
    if not a.offline:
        print("[planning] eval_gen.py (LIVE — needs the heavy+fast serves up) ...")
        rc = _call([sys.executable, os.path.join(d, "eval_gen.py")], cwd=d)
        if rc:
            print("[planning] eval_gen failed (are the serves up? `anvil-serving serves up`)",
                  file=sys.stderr)
            return rc
        print("[planning] note: the frontier baseline + blind judges are human-agent "
              "steps (see the eval README) — run them before aggregate for a fresh panel.")
    print("[planning] grade_struct.py (deterministic) ...")
    rc = _call([sys.executable, os.path.join(d, "grade_struct.py")], cwd=d) or rc
    print("[planning] aggregate.py ...")
    rc = _call([sys.executable, os.path.join(d, "aggregate.py")], cwd=d) or rc
    if rc == 0:
        print("[planning] done -> %s" % os.path.join(d, "grading"))
    return rc


def _run_bootstrap(a, _call=subprocess.call):
    print("[bootstrap] profile_bootstrap --replay %s -> %s" % (a.eval_data, a.out))
    return _call([sys.executable, "-m", "anvil_serving.router.profile_bootstrap",
                  "--replay", a.eval_data, "--out", a.out])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        prog="anvil-serving eval",
        description="Run the project's evaluations (preflight / benchmark / planning / bootstrap).")
    sub = p.add_subparsers(dest="kind")

    for name, helptext in (("preflight", "correctness gate vs a live endpoint"),
                           ("benchmark", "throughput / request-replay vs a live endpoint")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--tier", help="serve tier from the manifest (e.g. heavy, fast); "
                                       "fills --base-url/--model.")
        sp.add_argument("--base-url", help="override the endpoint base URL.")
        sp.add_argument("--model", help="override the served model id.")
        sp.add_argument("extra", nargs=argparse.REMAINDER,
                        help="extra args passed through to %s.py (after --)." % name)

    spp = sub.add_parser("planning", help="planning-capability bake-off (offline re-grade by default)")
    spp.add_argument("--offline", action="store_true", default=True,
                     help="re-grade committed eval-data only (default; no serves needed).")
    spp.add_argument("--live", dest="offline", action="store_false",
                     help="also run eval_gen.py against live serves first.")
    spp.add_argument("--dir", default=PLANNING_DIR, help="eval-data dir (default: %(default)s).")

    spb = sub.add_parser("bootstrap", help="replay committed eval fixtures into a quality profile")
    spb.add_argument("--eval-data", default=EVAL_DATA_ROOT, help="eval-data root (default: %(default)s).")
    spb.add_argument("--out", default=os.path.join(REPO, "profile.json"),
                     help="output profile path (default: %(default)s).")

    if not argv:
        p.print_help()
        return 0
    a = p.parse_args(argv)
    if a.kind in ("preflight", "benchmark"):
        # argparse.REMAINDER keeps a leading "--"; drop it for a clean passthrough.
        if a.extra and a.extra[0] == "--":
            a.extra = a.extra[1:]
        return _run_endpoint_eval(a.kind + ".py", a)
    if a.kind == "planning":
        return _run_planning(a)
    if a.kind == "bootstrap":
        return _run_bootstrap(a)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
