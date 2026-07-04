"""anvil-serving serves — lifecycle for the local model serves
(status / up / down / rm / adopt).

The router (`anvil-serving serve`) only *connects* to model backends; it never
controls their containers. This verb fills that gap: a small, declarative way to
stop, start, and inspect the GPU-backed model serves — so you can free the cards
between sessions (`serves down`) and bring them back (`serves up`) without
remembering two different launch mechanisms.

Three companion verbs handle the messier day-to-day around experiments:
  - `serves rm <name-or-container>...` force-removes container(s) — and crucially works
    for a container that is NOT in the manifest (an experiment squatting a port): a token
    matching a manifest serve's name/container removes that serve's container, any other
    token is treated literally as a container name. An absent container is a no-op success.
  - `serves adopt <name>...` brings an externally-started (non-compose-managed) manifest
    serve under compose management by recreating it via its manifest `up` (the `--recreate`
    path: `docker rm -f` + `up`).
  - `serves up --compose <file> [service...]` brings up an ad-hoc/experiment serve straight
    from a compose file that is NOT in the manifest (`docker compose -f <file> up -d
    [service...]`) — independent of serves.toml; with `--compose`, `names` are compose
    SERVICE names.

It reads a manifest (default `./serves.toml` — what `deploy`/`init` write; the shipped
reference is `examples/fakoli-dark/serves.toml`) that declares
each serve's container name, port, health path, declared `model` (served-model-name),
and an optional `up` command. Bringing a serve up is drift-safe: when `up` is a
`docker compose up -d`, that command IS the (re)start and is run UNCONDITIONALLY — even
when the container is already running — because compose recreates the container when its
config changed and fast-(re)starts it (a cheap no-op) when not, so editing the compose
file and re-running `serves up` recreates the container to match and a stale model is
never resurrected by a blind `docker start`. A one-shot `docker run` *script* serve can't
be re-run over an existing container, so it is `docker start`ed — with a loud warning if
it drifted from the declared `model` (fix: `--recreate`, or, better, convert it to a
compose file). A paused serve (either kind) is `docker unpause`d. `--recreate` forces a
clean `docker rm -f` + `up` for any serve. stdlib-only: `subprocess` to docker, `urllib`
for the health probe, `tomllib` to read the manifest.

TRUST BOUNDARY: a serve's `up` command from the manifest is EXECUTED. It is parsed
with `shlex` and run as an argv list (no shell), so `{dir}` paths with spaces are
safe and there is no shell-injection sink — but pointing `--manifest` at an
untrusted file still means running whatever programs its `up` lines name. Treat the
manifest as trusted, like a Makefile. A `bash {dir}/...sh` fresh-create `up` also
requires `bash` on PATH (Git Bash / WSL on Windows); a stopped container is just
`docker start`ed and needs none of this.
"""
import argparse
import os
import shlex
import subprocess
import sys
import urllib.request

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - guarded by requires-python >=3.11
    tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# genericity:T012 — the default manifest is the CWD's own serves.toml (what
# `anvil-serving deploy`/`init` write there), not the shipped fakoli-dark
# example. EXAMPLE_MANIFEST keeps a name for the shipped reference topology
# (tests, docs) now that DEFAULT_MANIFEST no longer points at it.
DEFAULT_MANIFEST = "./serves.toml"
EXAMPLE_MANIFEST = os.path.join(REPO, "examples", "fakoli-dark", "serves.toml")

# States meaning the container exists but is already stopped (nothing to free).
_STOPPED = ("exited", "created", "dead")


def load_manifest(path):
    """Parse the serves manifest into a list of serve dicts.

    Each serve's `up` is parsed with `shlex` into an argv list, then `{dir}` is
    resolved to the manifest's own directory PER TOKEN — so a repo path with
    spaces stays one argument and there is no shell to inject into.
    """
    if tomllib is None:
        raise RuntimeError("tomllib unavailable (need Python >= 3.11)")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    mdir = os.path.dirname(os.path.abspath(path))
    serves = []
    for raw in data.get("serve", []):
        s = dict(raw)
        if not s.get("name") or not s.get("container") or "port" not in s:
            raise ValueError(f"serve entry missing name/container/port: {raw!r}")
        s.setdefault("health", "/health")
        if s.get("up"):
            # split the TEMPLATE (forward-slash, no backslashes) then substitute,
            # so a backslashed/spaced {dir} never re-splits.
            s["up"] = [tok.replace("{dir}", mdir) for tok in shlex.split(s["up"])]
        serves.append(s)
    return serves


def _select(serves, names):
    """Serves matching any of `names` (by name or container); all if empty."""
    if not names:
        return list(serves)
    want = set(names)
    return [s for s in serves if s["name"] in want or s["container"] in want]


def docker_state(container, _run=subprocess.run):
    """Container state, distinguishing genuine absence from a docker error.

    Returns the raw docker status (running/exited/created/paused/restarting/...),
    or 'absent' (no such container), or 'error' (docker missing / daemon down /
    permission denied — i.e. we could NOT determine state, so callers must not
    claim success).
    """
    try:
        r = _run(["docker", "inspect", "-f", "{{.State.Status}}", container],
                 capture_output=True, text=True)
    except FileNotFoundError:
        return "error"  # docker not installed -> cannot manage containers
    if r.returncode != 0:
        return "absent" if "no such" in (r.stderr or "").lower() else "error"
    return (r.stdout or "").strip() or "unknown"


def _health(port, path, _open=urllib.request.urlopen):
    url = "http://127.0.0.1:%s%s" % (port, path)
    try:
        with _open(url, timeout=3) as resp:
            return getattr(resp, "status", None) or resp.getcode()
    except Exception:
        return None


def _gpu_lines(_run=subprocess.run):
    try:
        r = _run(["nvidia-smi", "--query-gpu=index,memory.used,memory.total",
                  "--format=csv,noheader,nounits"], capture_output=True, text=True)
    except FileNotFoundError:
        return []
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]


def cmd_status(serves, _run=subprocess.run, _open=urllib.request.urlopen):
    print("%-16s %-16s %-6s %-9s %s" % ("SERVE", "CONTAINER", "PORT", "DOCKER", "HEALTH"))
    for s in serves:
        st = docker_state(s["container"], _run=_run)
        health = _health(s["port"], s["health"], _open=_open) if st == "running" else None
        print("%-16s %-16s %-6s %-9s %s" % (
            s["name"], s["container"], s["port"], st, health if health else "-"))
    gpus = _gpu_lines(_run=_run)
    if gpus:
        print("\nGPU memory (index, used MiB, total MiB):")
        for g in gpus:
            print("  " + g)
    return 0


def cmd_down(serves, names, dry_run=False, _run=subprocess.run):
    targets = _select(serves, names)
    if not targets:
        print("no matching serves in manifest")
        return 1
    rc = 0
    for s in targets:
        st = docker_state(s["container"], _run=_run)
        if st == "error":
            print("  %s: cannot determine state (docker missing / daemon down / "
                  "permission?)" % s["container"])
            rc = 1
            continue
        if st == "absent" or st in _STOPPED:
            print("  %s: %s (nothing to stop)" % (s["container"], st))
            continue
        # running / paused / restarting / removing / unknown -> stop (frees the GPU).
        # Honor --dry-run: `down` is state-changing (it frees GPUs / kills in-flight
        # serving), so a preview must NOT actually stop anything.
        print("  stop %s" % s["container"])
        if dry_run:
            continue
        r = _run(["docker", "stop", s["container"]], capture_output=True, text=True)
        if r.returncode == 0:
            print("  stopped %s" % s["container"])
        else:
            print("  FAILED to stop %s: %s" % (s["container"], (r.stderr or "").strip()))
            rc = 1
    return rc


# Flags whose value names the model a container was created to serve. We prefer
# --served-model-name (what the OpenAI API advertises, what the manifest's `model`
# is), falling back to the weights id in --model / --model-path.
_SERVED_NAME_FLAGS = ("--served-model-name", "--served_model_name")
_MODEL_PATH_FLAGS = ("--model", "--model-path", "--model_path")


def _created_argv(container, _run=subprocess.run):
    """The argv a container was CREATED with (Config.Cmd + Args), one token per
    line. Empty list if docker is unavailable or inspect fails — callers must
    treat 'unknown' as 'no drift' and never block on uncertainty.
    """
    tmpl = "{{range .Config.Cmd}}{{println .}}{{end}}{{range .Args}}{{println .}}{{end}}"
    try:
        r = _run(["docker", "inspect", "-f", tmpl, container],
                 capture_output=True, text=True)
    except FileNotFoundError:
        return []
    if r.returncode != 0:
        return []
    return [ln for ln in (r.stdout or "").splitlines() if ln.strip()]


def _model_from_argv(tokens):
    """Best-effort served-model identifier from a container's created argv: the
    value of --served-model-name (preferred) else --model / --model-path. Handles
    both `--flag value` and `--flag=value`. None if neither flag is present.
    """
    def _value(flags):
        for i, tok in enumerate(tokens):
            for fl in flags:
                if tok == fl and i + 1 < len(tokens):
                    return tokens[i + 1]
                if tok.startswith(fl + "="):
                    return tok.split("=", 1)[1]
        return None
    return _value(_SERVED_NAME_FLAGS) or _value(_MODEL_PATH_FLAGS)


def _served_model(container, _run=subprocess.run):
    """The model an EXISTING container was created to serve, or None if it can't
    be determined (docker down, inspect failed, or no model flag on its argv).
    """
    return _model_from_argv(_created_argv(container, _run=_run))


def _is_compose_up(up):
    """True if the manifest `up` is a `docker compose up` — idempotent and drift-safe
    (it recreates the container when the compose config changed and fast-(re)starts it
    when unchanged), unlike a one-shot `docker run` script that can't be re-run over an
    existing container.
    """
    if not up:
        return False
    return up[:2] == ["docker", "compose"] or up[0] == "docker-compose"


def _warn_drift(s, _run=subprocess.run):
    """Loudly warn if an EXISTING (script-serve) container was created serving a
    different model than the manifest declares — a `docker start` would resurrect the
    STALE model. Best-effort: silent if the declared/served model can't be determined
    (never block on uncertainty). Compose serves don't need this: `up -d` self-heals.
    """
    declared = s.get("model")
    if not declared:
        return
    served = _served_model(s["container"], _run=_run)
    if served and served != declared:
        print("  WARNING: %s was created serving %r but the manifest declares %r — "
              "`docker start` will resurrect the STALE model; run `up --recreate` (or "
              "convert this serve to a compose file) to fix."
              % (s["container"], served, declared))


def cmd_up(serves, names, dry_run=False, recreate=False, _run=subprocess.run):
    targets = _select(serves, names)
    if not targets:
        print("no matching serves in manifest")
        return 1
    rc = 0
    for s in targets:
        st = docker_state(s["container"], _run=_run)
        if st == "error":
            print("  %s: cannot determine state (docker missing / daemon down / "
                  "permission?)" % s["container"])
            rc = 1
            continue
        if st in ("restarting", "removing", "dead", "unknown") and not (recreate and st == "dead"):
            # exotic / transitional state -> don't fresh-create (collision/destroy risk).
            # Exception: an explicit `--recreate` may rescue a `dead` container — it's a
            # terminal (not running) state, so a `docker rm -f` + fresh `up` is safe. The
            # other states stay hands-off even under --recreate.
            print("  %s: in state %r — not auto-started; resolve manually" % (s["container"], st))
            rc = 1
            continue

        up = s.get("up")
        compose = _is_compose_up(up)

        if recreate:
            # Explicit clean recreate from `up` (compose OR script): force-remove the
            # existing container, then run the fresh-create `up`.
            if not up:
                print("  %s: --recreate requested but no `up` command in manifest — "
                      "cannot recreate; resolve manually" % s["container"])
                rc = 1
                continue
            if st == "absent":
                # Nothing to remove — a `docker rm -f` of a nonexistent container errors
                # (exit 1) and would abort the fresh `up`. So `--recreate` also bootstraps
                # a serve that isn't there yet: just run `up`.
                steps = [up]
                desc = "up %s (--recreate, none present): %s" % (s["name"], " ".join(up))
            else:
                steps = [["docker", "rm", "-f", s["container"]], up]
                desc = "recreate %s: docker rm -f + %s" % (s["container"], " ".join(up))
        elif st == "absent":
            if not up:
                print("  %s: absent and no `up` command in manifest — start it "
                      "manually (see examples/fakoli-dark/)" % s["name"])
                rc = 1
                continue
            steps, desc = [up], "up %s: %s" % (s["name"], " ".join(up))
        elif st == "paused":
            # A paused container (compose OR script) still pins 100% of its VRAM; resume
            # it with `docker unpause`. Handled BEFORE the compose branch so a paused
            # compose serve isn't routed through `docker compose up -d` (which would not
            # unpause it) and left stuck paused.
            steps, desc = [["docker", "unpause", s["container"]]], "unpause %s" % s["container"]
        elif compose:
            # `docker compose up -d` natively recreates the container when its compose
            # config changed and fast-(re)starts it (a cheap no-op) otherwise — so we run
            # `up` UNCONDITIONALLY, even when the container is already running. That is the
            # whole point of ADR-0002: edit the compose file, re-run `serves up`, and the
            # container is recreated to match, instead of a blind "already running" skip or
            # a `docker start` silently resurrecting the container's STALE model. Drift-
            # safety for free; no bespoke config-hashing needed.
            steps = [up]
            desc = "compose up %s: %s" % (s["name"], " ".join(up))
        elif st == "running":
            _warn_drift(s, _run=_run)  # script serve: can't self-heal, so at least warn
            print("  %s: already running" % s["container"])
            continue
        else:  # exited / created -- a `docker run` script serve
            # A `docker run` script can't be re-run over an existing container (name
            # clash), so we `docker start` it — but that resurrects whatever model it
            # was CREATED with. Warn loudly on drift; the fix is `--recreate` or compose.
            _warn_drift(s, _run=_run)
            steps = [["docker", "start", s["container"]]]
            desc = ("start %s (restart existing container; convert to a compose serve "
                    "or use --recreate for drift-safety)" % s["container"])

        print("  " + desc)
        if dry_run:
            continue
        for step in steps:
            r = _run(step, capture_output=True, text=True)
            if r.returncode != 0:
                print("  FAILED: %s" % (r.stderr or r.stdout or "").strip())
                rc = 1
                break
    return rc


def cmd_rm(serves, names, dry_run=False, _run=subprocess.run):
    """Force-remove serve container(s) — `docker rm -f <container>`.

    THE key case: this works for a container that is NOT in the manifest — an experiment
    container squatting a serve's port. Each token is resolved independently: if it matches
    a manifest serve's name/container (via `_select`), that serve's container is removed;
    otherwise the token is treated LITERALLY as a container name. A container that's already
    'absent' is a no-op success ("nothing to remove"); an 'error' state (docker down /
    daemon unreachable) is NOT reported as success. Docker calls are argv lists (no shell).
    """
    if not names:
        print("no containers named to remove")
        return 1
    # resolve tokens -> container names: manifest name/container match wins, else literal.
    containers = []
    for tok in names:
        matched = _select(serves, [tok])
        if matched:
            for s in matched:
                if s["container"] not in containers:
                    containers.append(s["container"])
        elif tok not in containers:
            containers.append(tok)
    rc = 0
    for container in containers:
        st = docker_state(container, _run=_run)
        if st == "error":
            print("  %s: cannot determine state (docker missing / daemon down / "
                  "permission?)" % container)
            rc = 1
            continue
        if st == "absent":
            print("  %s: absent (nothing to remove)" % container)
            continue
        print("  rm -f %s" % container)
        if dry_run:
            continue
        r = _run(["docker", "rm", "-f", container], capture_output=True, text=True)
        if r.returncode == 0:
            print("  removed %s" % container)
        else:
            print("  FAILED to remove %s: %s" % (container, (r.stderr or "").strip()))
            rc = 1
    return rc


def cmd_adopt(serves, names, dry_run=False, _run=subprocess.run):
    """Bring externally-started (non-compose-managed) manifest serve(s) under compose
    management by recreating them via their manifest `up` — i.e. the `cmd_up` recreate
    path (`docker rm -f` + `up`). Use when a serve was started by hand / outside compose
    and you want compose to own its lifecycle going forward.
    """
    targets = _select(serves, names)
    if not targets:
        print("no matching serves in manifest")
        return 1
    for s in targets:
        print("  adopting %s under compose management "
              "(recreate via manifest `up`)" % s["name"])
    # reuse the recreate path: `docker rm -f` the hand-started container + fresh `up`.
    return cmd_up(serves, names, dry_run=dry_run, recreate=True, _run=_run)


def cmd_up_compose(compose_file, services, dry_run=False, _run=subprocess.run):
    """Bring up an ad-hoc/experiment serve from a compose file that is NOT in the manifest:
    `docker compose -f <file> up -d [service...]`. Fully independent of serves.toml — the
    file's services need not be declared there. argv list (no shell) for path/quoting safety.
    """
    argv = ["docker", "compose", "-f", compose_file, "up", "-d", *services]
    print("  compose up: %s" % " ".join(argv))
    if dry_run:
        return 0
    r = _run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        print("  FAILED: %s" % (r.stderr or r.stdout or "").strip())
        return 1
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        prog="anvil-serving serves",
        description="Stop/start/inspect the local GPU model serves (declared in a "
                    "serves manifest). The router connects to these; this manages them.")
    p.add_argument("action", choices=["status", "up", "down", "rm", "adopt"],
                   help="status: show docker + health; up: start (restart if stopped, "
                        "unpause if paused, else run the manifest `up`; with --compose, "
                        "run an ad-hoc compose file NOT in the manifest); down: docker "
                        "stop the serves; rm: `docker rm -f` container(s) — works for a "
                        "container NOT in the manifest (an experiment squatting a port); "
                        "adopt: bring an externally-started manifest serve under compose "
                        "management (recreate via its `up`).")
    p.add_argument("names", nargs="*",
                   help="serve names/containers to act on (default: all in the manifest). "
                        "For `rm`, an unrecognised name is treated literally as a container. "
                        "With `up --compose`, these are compose SERVICE names.")
    p.add_argument("--manifest", default=DEFAULT_MANIFEST,
                   help="path to the serves manifest TOML (default: %(default)s).")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would run without touching any container "
                        "(for up / down / rm / adopt).")
    p.add_argument("--recreate", action="store_true",
                   help="for `up`: force `docker rm -f` + a fresh `up` for an existing "
                        "container instead of `docker start`.")
    p.add_argument("--compose", metavar="FILE",
                   help="for `up`: bring up an ad-hoc/experiment serve from this compose "
                        "file (NOT in the manifest); `names` are compose service names.")
    # parse_intermixed_args (not parse_args): on py3.11 a `nargs="*"` positional that
    # follows an option-with-value (e.g. `up --compose FILE svc-a svc-b`) is dropped as
    # "unrecognized arguments" — py3.12 fixed plain parse_args, but intermixed is the
    # documented cross-version fix. No REMAINDER/subparsers here, so it's safe.
    a = p.parse_intermixed_args(argv)

    # `up --compose <file>`: ad-hoc/experiment serve from a compose file that is NOT in the
    # manifest — independent of serves.toml, so we neither require nor load a manifest here.
    if a.action == "up" and a.compose:
        if a.recreate:
            print("--recreate has no meaning with --compose (`docker compose up -d` already "
                  "recreates a service when its config changed)", file=sys.stderr)
            return 2
        return cmd_up_compose(a.compose, a.names, dry_run=a.dry_run)
    if a.compose:
        print("--compose is only valid with `up`", file=sys.stderr)
        return 2

    try:
        serves = load_manifest(a.manifest)
    except FileNotFoundError:
        print(
            "manifest not found: %s (run `anvil-serving init` to generate one, "
            "or pass --manifest to point at an existing serves.toml)" % a.manifest,
            file=sys.stderr,
        )
        return 2
    except Exception as e:  # malformed manifest
        print("bad manifest %s: %s" % (a.manifest, e), file=sys.stderr)
        return 2

    if a.action == "status":
        return cmd_status(serves)
    if a.action == "down":
        return cmd_down(serves, a.names, dry_run=a.dry_run)
    if a.action == "up":
        return cmd_up(serves, a.names, dry_run=a.dry_run, recreate=a.recreate)
    if a.action == "rm":
        return cmd_rm(serves, a.names, dry_run=a.dry_run)
    if a.action == "adopt":
        return cmd_adopt(serves, a.names, dry_run=a.dry_run)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
