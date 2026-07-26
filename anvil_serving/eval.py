"""anvil-serving eval — one entry point for the project's evaluations.

There are four evals in this repo, with three different invocation styles. This
verb makes them uniform and fills in the fakoli-dark topology so the common case
is one line:

  eval preflight [--tier heavy|fast] [extra flags...]   correctness gate vs a live endpoint
  eval benchmark [--tier heavy|fast] [extra flags...]   throughput / request-replay
  eval benchmark [--tier primary] [extra flags...]      throughput / request-replay

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
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
def _tiers(manifest=None):
    """tier name -> {base_url, model, port, health, container} from the manifest.

    Lets manifest errors propagate (the caller surfaces them) so a broken manifest
    is reported as a parse error, not as "no tiers".
    """
    from . import serves
    manifest_path = serves.resolve_manifest_path(manifest)
    if not os.path.isfile(os.path.expanduser(manifest_path)):
        from importlib import resources
        manifest_path = os.fspath(
            resources.files("anvil_serving").joinpath(
                "_scaffold_templates", "serves.toml"
            )
        )
    return {s["name"]: {
                "base_url": "http://127.0.0.1:%s/v1" % s["port"], "model": s["model"],
                "port": s["port"], "health": s.get("health", "/health"),
                "container": s["container"], "engine": s.get("engine"),
                "gpu_role": s.get("gpu_role")}
            for s in serves.load_manifest(manifest_path) if s.get("model")}


def resolve_endpoint_target(
        *, tier=None, manifest=None, base_url=None, model=None,
        recipe=None, registry=None):
    """Resolve one eval target from either endpoint or manifest inputs."""
    if recipe:
        if any(value is not None for value in (tier, manifest, base_url, model)):
            raise ValueError(
                "--recipe cannot be combined with --tier, --manifest, --base-url, or --model"
            )
        from . import serve_recipes, serves
        registry_path = serves.resolve_recipe_registry_path(registry)
        catalog = serve_recipes.load_registry(registry_path)
        selected_recipe = serve_recipes.find_recipe(catalog, recipe)
        if selected_recipe is None:
            choices = ", ".join(
                str(item.get("model")) for item in catalog.get("recipe", [])
                if item.get("model")
            )
            raise ValueError(
                "unknown recipe %r; available recipes: %s"
                % (recipe, choices or "(none)")
            )
        serve = selected_recipe.get("serve") or {}
        port = serve.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("recipe %r does not declare a usable serve.port" % recipe)
        selected = {
            "base_url": "http://127.0.0.1:%d/v1" % port,
            "model": serve.get("served_model_name") or selected_recipe["model"],
            "port": port,
            "health": serve.get("health", "/health"),
            "container": None,
            "engine": serve.get("engine"),
            "gpu_role": (selected_recipe.get("hardware") or {}).get("gpu_role"),
            "source_recipe": os.path.abspath(os.path.expanduser(registry_path)) + "#" + recipe,
        }
        return selected["base_url"], selected["model"], selected
    if registry and not recipe:
        raise ValueError("--registry requires --recipe")
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
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("--base-url must be an absolute http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("--base-url must not contain userinfo; use --api-key-env for auth")
    if parsed.query or parsed.fragment:
        raise ValueError("--base-url must not contain a query string or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("--base-url has an invalid port: %s" % exc) from exc
    if parsed.hostname.casefold() == "localhost":
        raise ValueError("--base-url must use 127.0.0.1 instead of localhost")
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
        dry_run = any(
            token == "--dry-run" or token.startswith("--dry-run=")
            for token in extra
        )
        if not a.base_url and not dry_run and not _reachable(
                t["port"], t["health"], _open=_open):
            print("tier %r (%s) is not reachable at %s\n  start it:  anvil-serving serves up %s"
                  % (a.tier, t["container"], base_url, a.tier), file=sys.stderr)
            return 3
    if not base_url or not model:
        print("need --tier [--manifest PATH], or both --base-url and --model", file=sys.stderr)
        return 2
    argv = ["--base-url", base_url, "--model", model] + list(extra)
    return _call([sys.executable, os.path.join(HERE, script)] + argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        prog="anvil-serving eval",
        description="Run the project's endpoint evaluations (preflight / benchmark).")
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

    if not argv:
        p.print_help()
        return 0
    # parse_known_args so preflight/benchmark can pass extra flags through WITHOUT a
    # `--` separator; other verbs reject unknowns explicitly.
    try:
        a, unknown = p.parse_known_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)
    if a.kind in ("preflight", "benchmark"):
        if unknown and unknown[0] == "--":   # tolerate an explicit separator too
            unknown = unknown[1:]
        return _run_endpoint_eval(a.kind + ".py", a, unknown)
    if unknown:
        p.error("unrecognized arguments: %s" % " ".join(unknown))
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
