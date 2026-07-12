"""anvil-serving eval — one entry point for the project's evaluations.

There are four evals in this repo, with three different invocation styles. This
verb makes them uniform and fills in the fakoli-dark topology so the common case
is one line:

  eval preflight [--tier heavy|fast] [extra flags...]   correctness gate vs a live endpoint
  eval benchmark [--tier heavy|fast] [extra flags...]   throughput / request-replay
  eval planning  [--live]                               planning-capability bake-off
                                                        (offline re-grade by default)
  eval bootstrap                                        replay eval fixtures -> quality profile

`preflight`/`benchmark` resolve `--base-url`/`--model` from the serves manifest
(examples/fakoli-dark/serves.toml), so `eval preflight --tier fast` just works
when that serve is up — and prints a `serves up` hint when it isn't. Any extra
flags are passed straight through to the underlying script
(`eval preflight --tier fast --requests 5`). stdlib-only.
"""
import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# The committed eval fixtures live under tests/fixtures/ (the dated findings tree was
# relocated to the private notes repo); fall back to the legacy docs/findings path so a
# checkout that still carries it keeps working.
_EVAL_DATA_CANDIDATES = (
    os.path.join(REPO, "tests", "fixtures", "eval-data"),
    os.path.join(REPO, "docs", "findings", "eval-data"),
)
EVAL_DATA_ROOT = next(
    (p for p in _EVAL_DATA_CANDIDATES if os.path.isdir(p)), _EVAL_DATA_CANDIDATES[0]
)
PLANNING_DIR = os.path.join(EVAL_DATA_ROOT, "2026-06-28-planning-capability")


def _tiers(manifest=None):
    """tier name -> {base_url, model, port, health, container} from the manifest.

    Lets manifest errors propagate (the caller surfaces them) so a broken manifest
    is reported as a parse error, not as "no tiers".
    """
    from . import serves
    # Preserve the shipped reference topology as the compatibility default. An
    # explicit manifest is the production path for another deployment.
    manifest_path = manifest or serves.EXAMPLE_MANIFEST
    return {s["name"]: {
                "base_url": "http://127.0.0.1:%s/v1" % s["port"], "model": s["model"],
                "port": s["port"], "health": s.get("health", "/health"),
                "container": s["container"]}
            for s in serves.load_manifest(manifest_path) if s.get("model")}


def resolve_endpoint_target(*, tier=None, manifest=None, base_url=None, model=None):
    """Resolve one eval target from either endpoint or manifest inputs."""
    if manifest and not tier:
        raise ValueError("--manifest requires --tier")
    selected = None
    if tier:
        tiers = _tiers(manifest)
        if tier not in tiers:
            source = manifest or "the bundled reference manifest"
            raise ValueError(
                "unknown tier %r in %s; available tiers: %s"
                % (tier, source, ", ".join(tiers) or "(none)")
            )
        selected = tiers[tier]
        base_url = base_url or selected["base_url"]
        model = model or selected["model"]
    if not base_url or not model:
        raise ValueError(
            "choose a manifest target with --tier [--manifest PATH], or provide "
            "both --base-url and --model"
        )
    return base_url, model, selected


def _reachable(port, path, _open=urllib.request.urlopen):
    """True if the endpoint answers at all (even a non-2xx) within 3s.

    A serve that is up but still loading (503) or under load counts as reachable —
    only a refused/timed-out connection means "not up".
    """
    try:
        with _open("http://127.0.0.1:%s%s" % (port, path), timeout=3):
            return True
    except urllib.error.HTTPError:
        return True  # the server responded -> it is up
    except Exception:
        return False


def _run_endpoint_eval(script, a, extra, _call=subprocess.call, _open=urllib.request.urlopen):
    """Shell preflight.py / benchmark.py, defaulting base-url/model from a tier."""
    base_url, model = a.base_url, a.model
    if a.tier:
        try:
            tiers = _tiers(getattr(a, "manifest", None))
        except Exception as e:
            print("cannot read serves manifest: %s" % e, file=sys.stderr)
            return 2
        if a.tier not in tiers:
            print("unknown tier %r; manifest tiers: %s"
                  % (a.tier, ", ".join(tiers) or "(none)"), file=sys.stderr)
            return 2
        t = tiers[a.tier]
        base_url = base_url or t["base_url"]
        model = model or t["model"]
        # Gate on reachability ONLY when we're actually targeting the tier's local
        # endpoint — an explicit --base-url override points elsewhere.
        if not a.base_url and not _reachable(t["port"], t["health"], _open=_open):
            print("tier %r (%s) is not reachable at %s\n  start it:  anvil-serving serves up %s"
                  % (a.tier, t["container"], base_url, a.tier), file=sys.stderr)
            return 3
    if not base_url or not model:
        print("need --tier [--manifest PATH], or both --base-url and --model", file=sys.stderr)
        return 2
    argv = ["--base-url", base_url, "--model", model] + list(extra)
    return _call([sys.executable, os.path.join(HERE, script)] + argv)


def _run_planning(a, _call=subprocess.call):
    d = os.path.abspath(a.dir)  # absolute so cwd=d doesn't double-join the script path
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
    if rc:
        # Don't aggregate over stale/partial grading output.
        print("[planning] grade_struct failed; skipping aggregate", file=sys.stderr)
        return rc
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
        sp = sub.add_parser(name, help=helptext,
                            description="%s; unknown flags pass through to %s.py." % (helptext, name))
        sp.add_argument("--tier", help="serve tier from the manifest (e.g. heavy, fast); "
                                       "fills --base-url/--model.")
        sp.add_argument("--manifest", help="serves manifest TOML used with --tier "
                                           "(default: bundled reference manifest).")
        sp.add_argument("--base-url", help="override the endpoint base URL "
                                           "(skips the tier reachability gate).")
        sp.add_argument("--model", help="override the served model id.")

    spp = sub.add_parser("planning", help="planning-capability bake-off (offline re-grade by default)")
    spp.add_argument("--offline", action="store_true", default=True,
                     help="re-grade committed eval-data only (the default; no serves needed).")
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
    # parse_known_args so preflight/benchmark can pass extra flags through WITHOUT a
    # `--` separator; other verbs reject unknowns explicitly.
    a, unknown = p.parse_known_args(argv)
    if a.kind in ("preflight", "benchmark"):
        if unknown and unknown[0] == "--":   # tolerate an explicit separator too
            unknown = unknown[1:]
        return _run_endpoint_eval(a.kind + ".py", a, unknown)
    if unknown:
        p.error("unrecognized arguments: %s" % " ".join(unknown))
    if a.kind == "planning":
        return _run_planning(a)
    if a.kind == "bootstrap":
        return _run_bootstrap(a)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
