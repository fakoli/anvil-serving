"""anvil-serving serves — lifecycle for the local model serves (status / up / down).

The router (`anvil-serving serve`) only *connects* to model backends; it never
controls their containers. This verb fills that gap: a small, declarative way to
stop, start, and inspect the GPU-backed model serves — so you can free the cards
between sessions (`serves down`) and bring them back (`serves up`) without
remembering two different launch mechanisms.

It reads a manifest (default `examples/fakoli-dark/serves.toml`) that declares
each serve's container name, port, health path, and an optional `up` command for
a *fresh* create. An already-created-but-stopped container is restarted with
`docker start` (no manifest `up` needed). stdlib-only: `subprocess` to docker,
`urllib` for the health probe, `tomllib` to read the manifest.
"""
import argparse
import os
import subprocess
import sys
import urllib.request

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - guarded by requires-python >=3.11
    tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_MANIFEST = os.path.join(REPO, "examples", "fakoli-dark", "serves.toml")


def load_manifest(path):
    """Parse the serves manifest into a list of serve dicts.

    `{dir}` in an `up` command is resolved to the manifest's own directory so
    relative repo artifacts (compose files, launch scripts) work from anywhere.
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
            s["up"] = s["up"].replace("{dir}", mdir)
        serves.append(s)
    return serves


def _select(serves, names):
    """Serves matching any of `names` (by name or container); all if empty."""
    if not names:
        return list(serves)
    want = set(names)
    return [s for s in serves if s["name"] in want or s["container"] in want]


def docker_state(container, _run=subprocess.run):
    """'running' | 'exited' | 'created' | 'absent' (docker missing -> 'absent')."""
    try:
        r = _run(["docker", "inspect", "-f", "{{.State.Status}}", container],
                 capture_output=True, text=True)
    except FileNotFoundError:
        return "absent"
    if r.returncode != 0:
        return "absent"
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


def cmd_down(serves, names, _run=subprocess.run):
    targets = _select(serves, names)
    if not targets:
        print("no matching serves in manifest")
        return 1
    rc = 0
    for s in targets:
        st = docker_state(s["container"], _run=_run)
        if st != "running":
            print("  %s: %s (nothing to stop)" % (s["container"], st))
            continue
        r = _run(["docker", "stop", s["container"]], capture_output=True, text=True)
        if r.returncode == 0:
            print("  stopped %s" % s["container"])
        else:
            print("  FAILED to stop %s: %s" % (s["container"], (r.stderr or "").strip()))
            rc = 1
    return rc


def cmd_up(serves, names, dry_run=False, _run=subprocess.run):
    targets = _select(serves, names)
    if not targets:
        print("no matching serves in manifest")
        return 1
    rc = 0
    for s in targets:
        st = docker_state(s["container"], _run=_run)
        if st == "running":
            print("  %s: already running" % s["container"])
            continue
        if st in ("exited", "created"):
            # Container already exists — restart it (no fresh create needed).
            action = ["docker", "start", s["container"]]
            print("  start %s (restart existing container)" % s["container"])
            if dry_run:
                continue
            r = _run(action, capture_output=True, text=True)
        else:  # absent -> fresh create via the manifest `up` command
            up = s.get("up")
            if not up:
                print("  %s: absent and no `up` command in manifest — start it "
                      "manually (see examples/fakoli-dark/)" % s["name"])
                rc = 1
                continue
            print("  up %s: %s" % (s["name"], up))
            if dry_run:
                continue
            r = _run(up, shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            print("  FAILED: %s" % (r.stderr or r.stdout or "").strip())
            rc = 1
    return rc


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        prog="anvil-serving serves",
        description="Stop/start/inspect the local GPU model serves (declared in a "
                    "serves manifest). The router connects to these; this manages them.")
    p.add_argument("action", choices=["status", "up", "down"],
                   help="status: show docker + health; up: start (restart if stopped, "
                        "else run the manifest `up`); down: docker stop the serves.")
    p.add_argument("names", nargs="*",
                   help="serve names/containers to act on (default: all in the manifest).")
    p.add_argument("--manifest", default=DEFAULT_MANIFEST,
                   help="path to the serves manifest TOML (default: %(default)s).")
    p.add_argument("--dry-run", action="store_true",
                   help="for `up`: print what would run without starting any container.")
    a = p.parse_args(argv)

    try:
        serves = load_manifest(a.manifest)
    except FileNotFoundError:
        print("manifest not found: %s" % a.manifest, file=sys.stderr)
        return 2
    except Exception as e:  # malformed manifest
        print("bad manifest %s: %s" % (a.manifest, e), file=sys.stderr)
        return 2

    if a.action == "status":
        return cmd_status(serves)
    if a.action == "down":
        return cmd_down(serves, a.names)
    if a.action == "up":
        return cmd_up(serves, a.names, dry_run=a.dry_run)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
