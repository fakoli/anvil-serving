"""anvil-serving router — manage the DEPLOYED (ADR-0004 containerized) anvil-router.

`anvil-serving serve` runs the router in-process; `anvil-serving serves` manages the
GPU model serves. Neither manages the *deployed*, token-authed router container from
ADR-0004 — until now operators reached for raw `docker`. This verb fills that gap.

Subcommands:
  up / down / restart   lifecycle (compose up -d / compose stop / docker restart)
  reload                config is read at ROUTER STARTUP, so a reload IS a restart —
                        this restarts the container and prints that note (no in-process
                        reload exists).
  status                docker state + whether it's running + a loopback health probe
  logs                  docker logs for the router container (--tail/--since/--follow) — so
                        diagnosing a crash-loop doesn't mean reaching for raw docker
  token                 print the router bearer token (ANVIL_ROUTER_TOKEN) — a local
                        secret the operator owns — or report auth is unset
  promote               THE HIGH-VALUE VERB: the containerized profile/config write-back.
                        The router's config volume is mounted READ-ONLY into the running
                        router, so writes go through a ROOT side-container. promote
                        validates the profile against the DEPLOYED image's OWN loader
                        (version-safe), backs up the current profile, ATOMICALLY writes
                        the new one, reloads, and ROLLS BACK on a crash-loop.

stdlib-only: `subprocess` to docker (argv LISTS — no shell string, so no injection sink
and no Windows/MSYS path mangling), `urllib` for the health probe, `tomllib`/`json` for
the config/profile basic-checks. Docker + HTTP + sleep are dependency-injected
(`_run`/`_open`/`_sleep`) so tests run with no docker, no network, and no real waiting.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - guarded by requires-python >=3.11
    tomllib = None

# Reuse the tested container-state probe from the serves verb (distinguishes
# running / absent / error) instead of re-deriving it here.
from .serves import docker_state

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

DEFAULT_COMPOSE = os.path.join(REPO, "examples", "fakoli-dark", "docker-compose.yml")
DEFAULT_CONTAINER = "anvil-router"
DEFAULT_SERVICE = "router"
DEFAULT_CFG_VOLUME = "anvil-router-cfg"
# Pinned to the DEPLOYED image (ADR-0004 / the compose file): validating and writing
# with the LIVE image's own loader is what makes promote version-safe — a newer local
# checkout must not re-verdict a profile the deployed router would reject. Keep this in
# lockstep with the `router` service image in examples/fakoli-dark/docker-compose.yml.
DEFAULT_IMAGE = "anvil-serving:0.9.0"
# The router READS its config volume mounted at ROUTER_CFG_MOUNT (/etc/anvil); the
# side-container mounts the SAME volume at _SIDE_MOUNT (/cfg). `_volume_path` translates a
# router-visible dest to its /cfg path, PRESERVING subdirectories (so /etc/anvil/x/p.json
# -> /cfg/x/p.json, not a flattened /cfg/p.json the router would never read).
ROUTER_CFG_MOUNT = "/etc/anvil"
DEFAULT_PROFILE_DEST = ROUTER_CFG_MOUNT + "/profile.json"
DEFAULT_CONFIG_DEST = ROUTER_CFG_MOUNT + "/config.toml"

# Mount point for the config volume inside the ROOT side-container (writes go here).
_SIDE_MOUNT = "/cfg"

# Dest paths are interpolated into a root `sh -c`, so restrict them to safe characters
# (no shell metacharacters, no `..`) BEFORE building any command string.
_SAFE_DEST = re.compile(r"^[A-Za-z0-9._/-]+$")


def _volume_path(dest):
    """Router-visible dest under ROUTER_CFG_MOUNT -> its path inside the /cfg side-container
    mount of the SAME volume, PRESERVING subdirectories. Raises ValueError if dest is not
    under ROUTER_CFG_MOUNT, contains shell-unsafe characters, or uses `..`."""
    if not _SAFE_DEST.match(dest or "") or ".." in dest.split("/"):
        raise ValueError("unsafe destination %r (allowed [A-Za-z0-9._/-], no '..')" % dest)
    root = ROUTER_CFG_MOUNT.rstrip("/") + "/"
    if not dest.startswith(root):
        raise ValueError("destination %r must be under the router config mount %s/"
                         % (dest, ROUTER_CFG_MOUNT))
    return _SIDE_MOUNT.rstrip("/") + "/" + dest[len(root):]

# Validate a profile document against the DEPLOYED image's OWN loader: import
# store_from_profile and run it on the profile fed via stdin. A non-zero exit /
# raised exception (schema mismatch, bad shape) => ABORT before touching the volume,
# which is exactly what prevents the fail-fast router crash-loop on a bad profile.
_VALIDATOR = (
    "import sys, json; "
    "from anvil_serving.router.profile_bootstrap import store_from_profile; "
    "store_from_profile(json.load(sys.stdin))"
)


# --------------------------------------------------------------------------- #
# lifecycle verbs
# --------------------------------------------------------------------------- #

def _run_argv(argv, _run, desc=None, dry_run=False, **kw):
    """Print `desc`, then run `argv` (unless dry_run). Returns 0 on success, 1 on
    failure. All docker calls funnel through here (or an inline `_run`)."""
    if desc:
        print("  " + desc)
    if dry_run:
        return 0
    try:
        r = _run(argv, capture_output=True, text=True, **kw)
    except FileNotFoundError:
        # docker not installed -> clean message, not a raw traceback (matches
        # cmd_token / docker_state / promote's validate and all of serves.py).
        print("  FAILED: docker not available")
        return 1
    if r.returncode != 0:
        print("  FAILED: %s" % (r.stderr or r.stdout or "").strip())
        return 1
    return 0


def _default_env_file():
    """First existing conventional deploy env-file — `~/.anvil_env` preferred, then `~/.env` — so
    `router up` picks up ANVIL_ROUTER_TOKEN / ROUTER_PUBLISH without the operator re-exporting them
    each deploy (the router fail-closes without its token, and reverts to loopback without the
    publish). None if neither exists."""
    for name in (".anvil_env", ".env"):
        p = os.path.join(os.path.expanduser("~"), name)
        if os.path.isfile(p):
            return p
    return None


def cmd_up(compose, service, env_file=None, dry_run=False, _run=subprocess.run):
    # `--env-file` FIRST (before -f) so compose interpolates ${ANVIL_ROUTER_TOKEN}/${ROUTER_PUBLISH}
    # from the persisted deploy env; without it a bare `up` recreates the router with an empty token
    # (fail-closed crash) and loopback binding.
    argv = ["docker", "compose"]
    if env_file:
        # ABSOLUTE path: compose resolves a RELATIVE --env-file in its own project context (the
        # compose file's dir via -f), not the operator's CWD — so `router up --env-file .env` from
        # elsewhere could silently miss the file and start the router without the token/publish.
        argv += ["--env-file", os.path.abspath(os.path.expanduser(env_file))]
    argv += ["-f", compose, "up", "-d", service]
    return _run_argv(argv, _run, desc="up %s: %s" % (service, " ".join(argv)),
                     dry_run=dry_run)


def cmd_down(compose, service, dry_run=False, _run=subprocess.run):
    # `stop` (not `down`): free the router without deleting the network/volumes the
    # model serves and config depend on.
    argv = ["docker", "compose", "-f", compose, "stop", service]
    return _run_argv(argv, _run, desc="down %s: %s" % (service, " ".join(argv)),
                     dry_run=dry_run)


def cmd_restart(container, dry_run=False, _run=subprocess.run):
    argv = ["docker", "restart", container]
    return _run_argv(argv, _run, desc="restart %s" % container, dry_run=dry_run)


def cmd_reload(container, dry_run=False, _run=subprocess.run):
    # The router reads its config + profile ONCE at startup; there is no in-process
    # reload signal. So a reload IS a restart — say so, then restart.
    print("  note: the router loads config/profile at STARTUP; there is no live "
          "reload, so `reload` restarts the container to pick up changes.")
    return cmd_restart(container, dry_run=dry_run, _run=_run)


def cmd_logs(container, tail="200", since=None, follow=False, _run=subprocess.run):
    """`docker logs` for the router container, so operators (and this session) don't reach for
    raw docker to diagnose it. Checks the container exists first — a clean message beats docker's
    raw error. `--follow` streams to the terminal (no capture, or it would block)."""
    st = docker_state(container, _run=_run)
    if st == "error":
        print("cannot read logs: docker not available / daemon down / permission?", file=sys.stderr)
        return 1
    if st == "absent":
        print("cannot read logs: container %s does not exist (bring it up first)." % container,
              file=sys.stderr)
        return 1
    argv = ["docker", "logs", "--tail", str(tail)]
    if since:
        argv += ["--since", since]
    if follow:
        argv.append("--follow")
    argv.append(container)
    try:
        if follow:
            # stream straight to the terminal (Ctrl-C to stop); capturing would block forever.
            return _run(argv).returncode
        r = _run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        print("cannot read logs: docker not available", file=sys.stderr)
        return 1
    # docker routes container stdout -> stdout and stderr -> stderr; surface BOTH (the router's
    # startup errors — e.g. a missing auth token — go to stderr).
    sys.stdout.write(r.stdout or "")
    sys.stderr.write(r.stderr or "")
    return r.returncode


def _health(_open, port=8000, path="/"):
    url = "http://127.0.0.1:%s%s" % (port, path)
    try:
        with _open(url, timeout=3) as resp:
            return getattr(resp, "status", None) or resp.getcode()
    except Exception:
        return None


def cmd_status(container, _run=subprocess.run, _open=urllib.request.urlopen):
    st = docker_state(container, _run=_run)
    print("router container: %s" % container)
    print("docker state:     %s" % st)
    if st == "error":
        print("status:           UNKNOWN (docker missing / daemon down / permission?)")
        return 1
    running = st == "running"
    print("running:          %s" % ("yes" if running else "no"))
    if running:
        # Any HTTP response at all means the front door is up (even 401 from auth).
        code = _health(_open)
        print("health (:8000/):  %s" % (code if code else "no response"))
    return 0


def cmd_token(container, _run=subprocess.run):
    # Rule out "can't reach the container" FIRST, so a stopped/absent container or a docker
    # error is reported as such — not silently as "auth UNSET" (a false success).
    st = docker_state(container, _run=_run)
    if st == "error":
        print("cannot read token: docker not available / daemon down / permission?")
        return 1
    if st != "running":
        print("cannot read token: container %s is %s (not running)." % (container, st))
        return 1
    try:
        r = _run(["docker", "exec", container, "printenv", "ANVIL_ROUTER_TOKEN"],
                 capture_output=True, text=True)
    except FileNotFoundError:
        print("cannot read token: docker not available")
        return 1
    token = (r.stdout or "").strip()
    if r.returncode == 0 and token:
        print(token)
        return 0
    # The container IS running (checked above), so a non-zero printenv / empty value means
    # the var is genuinely UNSET (auth off) — not an unreachable-container false positive.
    print("auth is UNSET: ANVIL_ROUTER_TOKEN is not set on %s (the router accepts "
          "unauthenticated requests — see SECURITY.md)." % container)
    return 0


# --------------------------------------------------------------------------- #
# promote — the containerized write-back / promotion path
# --------------------------------------------------------------------------- #

def _restart_count(container, _run):
    """Container's ``.State.RestartCount``, or None if it can't be read. A MANUAL
    ``docker restart`` does NOT bump this; only the restart POLICY does. So a count
    that grows after our restart means the policy bounced a crashed container."""
    try:
        r = _run(["docker", "inspect", "-f", "{{.RestartCount}}", container],
                 capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if r.returncode != 0:
        return None
    try:
        return int((r.stdout or "").strip())
    except ValueError:
        return None


def _await_running(container, _run, _sleep, baseline_restarts=None,
                   settle=3.0, checks=4, delay=2.0):
    """After a restart, verify the container comes up AND STAYS up (not crash-looping).

    A single 'running' read right after ``docker restart`` is NOT proof of health: a
    fail-fast router (e.g. a bad profile) exits within ~1-2s, and the deployed compose's
    ``restart: unless-stopped`` immediately bounces it back to 'running' — so a naive
    first-'running' check almost always MISSES a crash-loop and the rollback never fires.
    So we (1) sleep `settle` seconds first, then (2) require `checks` CONSECUTIVE 'running'
    samples, treating any non-running sample OR a RestartCount that grew past
    `baseline_restarts` (the policy bounced it) as a crash. Returns (ok, last_state)."""
    _sleep(settle)
    st = None
    for _ in range(checks):
        st = docker_state(container, _run=_run)
        if st != "running":
            return False, st  # exited / restarting / created -> crashed
        rc = _restart_count(container, _run)
        if baseline_restarts is not None and rc is not None and rc > baseline_restarts:
            return False, "restarting"  # policy re-launched it since our restart -> crash-loop
        _sleep(delay)
    return True, st


def cmd_promote(profile_path, *, config_path=None, container=DEFAULT_CONTAINER,
                cfg_volume=DEFAULT_CFG_VOLUME, image=DEFAULT_IMAGE,
                profile_dest=DEFAULT_PROFILE_DEST, config_dest=DEFAULT_CONFIG_DEST,
                no_reload=False, dry_run=False,
                _run=subprocess.run, _sleep=time.sleep):
    """Validate -> back up -> atomically write -> reload -> verify (rollback on crash).

    Every step prints; under `dry_run` nothing is executed (docker is never called).
    Returns 0 on success, 1 on any failure (validation abort, write failure, or a
    crash-loop that triggered rollback).
    """
    # -- read + local sanity (JSON well-formed) -------------------------------
    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            profile_text = f.read()
    except OSError as e:
        print("cannot read profile %s: %s" % (profile_path, e))
        return 1
    try:
        json.loads(profile_text)
    except ValueError as e:
        print("profile is not valid JSON: %s" % e)
        return 1

    config_text = None
    if config_path is not None:
        try:
            with open(config_path, "rb") as f:
                raw = f.read()
        except OSError as e:
            print("cannot read config %s: %s" % (config_path, e))
            return 1
        # basic-check: valid TOML that sets [router].profile_path.
        if tomllib is None:  # pragma: no cover - requires-python >=3.11
            print("cannot check config: tomllib unavailable (need Python >= 3.11)")
            return 1
        try:
            cfg = tomllib.loads(raw.decode("utf-8"))
        except Exception as e:
            print("config is not valid TOML: %s" % e)
            return 1
        if not (isinstance(cfg.get("router"), dict) and cfg["router"].get("profile_path")):
            print("config %s does not set [router].profile_path — refusing to promote "
                  "a config the router would ignore the profile for." % config_path)
            return 1
        config_text = raw.decode("utf-8")

    # In-volume paths: translate each router-visible dest to the /cfg side mount (PRESERVING
    # subdirs), so the temp+mv stays within the SAME filesystem (the volume) and is atomic.
    try:
        prof = _volume_path(profile_dest)
        cfg_file = _volume_path(config_dest)
    except ValueError as e:
        print("  ABORT: %s" % e)
        return 1
    prof_bak, prof_new = prof + ".bak", prof + ".new"
    cfg_bak, cfg_new = cfg_file + ".bak", cfg_file + ".new"
    prof_dir, cfg_dir = prof.rsplit("/", 1)[0], cfg_file.rsplit("/", 1)[0]

    vol_mount = cfg_volume + ":" + _SIDE_MOUNT

    def _rollback(reason):
        """Restore the pre-promote profile/config from backups (mv .bak back, or rm the new
        file if there was no prior — first-ever promote). Returns True iff it ran OK."""
        rb = ["if [ -f %s ]; then mv %s %s; else rm -f %s; fi" % (prof_bak, prof_bak, prof, prof)]
        if config_text is not None:
            rb.append("if [ -f %s ]; then mv %s %s; else rm -f %s; fi"
                      % (cfg_bak, cfg_bak, cfg_file, cfg_file))
        r = _run(["docker", "run", "--rm", "--user", "0", "-v", vol_mount,
                  "--entrypoint", "sh", image, "-c", " ; ".join(rb)],
                 capture_output=True, text=True)
        if r.returncode != 0:
            print("  WARNING: rollback (%s) may have FAILED: %s — inspect the %s volume by hand."
                  % (reason, (r.stderr or r.stdout or "").strip(), cfg_volume))
            return False
        return True

    # -- (a) VALIDATE against the DEPLOYED image's own loader ------------------
    val_argv = ["docker", "run", "--rm", "-i", "--entrypoint", "python",
                image, "-c", _VALIDATOR]
    print("  validate: %s (against %s loader)" % (profile_path, image))
    if not dry_run:
        try:
            r = _run(val_argv, input=profile_text, capture_output=True, text=True)
        except FileNotFoundError:
            print("  ABORT: docker not available")
            return 1
        if r.returncode != 0:
            print("  ABORT: profile rejected by the deployed router's loader "
                  "(exit %s): %s" % (r.returncode, (r.stderr or r.stdout or "").strip()))
            print("  (no volume was touched — this prevents a crash-loop on a bad profile.)")
            return 1

    # -- (b) BACK UP the current profile (and config, if promoting one) -------
    # so a crash-loop can be rolled back symmetrically. `.bak` absent => there was
    # no prior file (first-ever promote); the rollback below handles that case.
    bak_cmds = ["[ -f %s ] && cp %s %s || true" % (prof, prof, prof_bak)]
    if config_text is not None:
        bak_cmds.append("[ -f %s ] && cp %s %s || true" % (cfg_file, cfg_file, cfg_bak))
    # `--entrypoint sh`: the router IMAGE's default entrypoint is `anvil-serving serve`,
    # so without this the side-container would try to START the router (and fail) instead
    # of running our file ops. (The validate step above overrides with `--entrypoint python`.)
    backup_argv = ["docker", "run", "--rm", "--user", "0", "-v", vol_mount,
                   "--entrypoint", "sh", image, "-c", " ; ".join(bak_cmds)]
    if _run_argv(backup_argv, _run, desc="backup current profile/config (if present)",
                 dry_run=dry_run):
        return 1

    # -- (c) ATOMICALLY write the new profile (and config, if given) ----------
    write_argv = ["docker", "run", "--rm", "-i", "--user", "0", "-v", vol_mount,
                  "--entrypoint", "sh", image, "-c",
                  "mkdir -p %s && cat > %s && mv %s %s" % (prof_dir, prof_new, prof_new, prof)]
    print("  write profile: %s (atomic mv within the volume)" % prof)
    if not dry_run:
        r = _run(write_argv, input=profile_text, capture_output=True, text=True)
        if r.returncode != 0:
            print("  FAILED to write profile: %s" % (r.stderr or r.stdout or "").strip())
            return 1

    if config_text is not None:
        write_cfg_argv = ["docker", "run", "--rm", "-i", "--user", "0", "-v", vol_mount,
                          "--entrypoint", "sh", image, "-c",
                          "mkdir -p %s && cat > %s && mv %s %s" % (cfg_dir, cfg_new, cfg_new, cfg_file)]
        print("  write config: %s (atomic mv within the volume)" % cfg_file)
        if not dry_run:
            r = _run(write_cfg_argv, input=config_text, capture_output=True, text=True)
            if r.returncode != 0:
                print("  FAILED to write config: %s" % (r.stderr or r.stdout or "").strip())
                # The profile was already written; restore it so the volume isn't left with a
                # NEW profile paired with the OLD config (a mixed promotion on the next reload).
                _rollback("config write failed")
                return 1

    # -- (d) reload + verify it didn't crash-loop -----------------------------
    if no_reload:
        print("  --no-reload: wrote the new profile but did NOT restart; it takes "
              "effect on the next `router reload`/restart.")
        return 0

    restart_argv = ["docker", "restart", container]
    print("  reload (restart %s)" % container)
    if dry_run:
        return 0
    # Snapshot the policy restart count BEFORE our manual restart, so a crash-loop
    # (the unless-stopped policy re-launching a crashed container) is detectable.
    base_restarts = _restart_count(container, _run)
    r = _run(restart_argv, capture_output=True, text=True)
    if r.returncode != 0:
        print("  FAILED to restart: %s" % (r.stderr or r.stdout or "").strip())
        return 1

    ok, st = _await_running(container, _run, _sleep, baseline_restarts=base_restarts)
    if ok:
        print("  OK: %s is running with the promoted profile." % container)
        return 0

    # Crash-loop: restore the previous profile/config (via _rollback — which handles the
    # first-ever case by REMOVING the bad file) and restart again, reporting any failure.
    print("  CRASH: %s did not stay up (state %r) — rolling back." % (container, st))
    rolled = _rollback("crash-loop")
    rr = _run(restart_argv, capture_output=True, text=True)
    if rr.returncode != 0:
        print("  WARNING: restart after rollback FAILED: %s — %s may be DOWN; check it."
              % ((rr.stderr or rr.stdout or "").strip(), container))
    elif rolled:
        print("  rolled back to the previous profile/config and restarted %s; promote FAILED."
              % container)
    return 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        prog="anvil-serving router",
        description="Manage the DEPLOYED (containerized, ADR-0004) anvil-router: "
                    "lifecycle, token, status, and the promote write-back path.")
    p.add_argument("action",
                   choices=["up", "down", "restart", "reload", "status", "logs", "token", "promote"],
                   help="up/down/restart/reload lifecycle; status; logs (docker logs); token "
                        "(print the bearer token); promote (validate + write a new profile/config).")
    p.add_argument("--container", default=DEFAULT_CONTAINER,
                   help="router container name (default: %(default)s).")
    p.add_argument("--compose", default=DEFAULT_COMPOSE,
                   help="docker-compose.yml for up/down (default: the fakoli-dark example).")
    p.add_argument("--service", default=DEFAULT_SERVICE,
                   help="compose service name for the router (default: %(default)s).")
    p.add_argument("--env-file", default=None,
                   help="env file for `up` (docker compose --env-file), so ANVIL_ROUTER_TOKEN / "
                        "ROUTER_PUBLISH persist across deploys. Default: ~/.anvil_env or ~/.env if "
                        "present (pass '' to disable).")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would run without executing any docker command.")
    # logs-only options
    p.add_argument("--tail", default="200",
                   help="logs: number of trailing lines to show (default: %(default)s; 'all').")
    p.add_argument("--since",
                   help="logs: only show logs since a timestamp or relative time (e.g. 10m, 1h).")
    p.add_argument("--follow", action="store_true",
                   help="logs: stream new log output (Ctrl-C to stop).")
    # promote-only options
    p.add_argument("--profile", help="promote: path to the profile.json to promote.")
    p.add_argument("--config", help="promote: optional config.toml to write alongside it.")
    p.add_argument("--cfg-volume", default=DEFAULT_CFG_VOLUME,
                   help="promote: router config docker volume (default: %(default)s).")
    p.add_argument("--image", default=DEFAULT_IMAGE,
                   help="promote: deployed router image used to validate + write "
                        "(default: %(default)s).")
    p.add_argument("--profile-dest", default=DEFAULT_PROFILE_DEST,
                   help="promote: router-visible profile path (default: %(default)s).")
    p.add_argument("--config-dest", default=DEFAULT_CONFIG_DEST,
                   help="promote: router-visible config path (default: %(default)s).")
    p.add_argument("--no-reload", action="store_true",
                   help="promote: write the profile but don't restart the router.")
    a = p.parse_args(argv)

    if a.action == "up":
        # explicit --env-file wins; unset -> auto-detect ~/.anvil_env/~/.env; '' -> disable.
        env_file = _default_env_file() if a.env_file is None else (a.env_file or None)
        return cmd_up(a.compose, a.service, env_file=env_file, dry_run=a.dry_run)
    if a.action == "down":
        return cmd_down(a.compose, a.service, dry_run=a.dry_run)
    if a.action == "restart":
        return cmd_restart(a.container, dry_run=a.dry_run)
    if a.action == "reload":
        return cmd_reload(a.container, dry_run=a.dry_run)
    if a.action == "status":
        return cmd_status(a.container)
    if a.action == "logs":
        return cmd_logs(a.container, tail=a.tail, since=a.since, follow=a.follow)
    if a.action == "token":
        return cmd_token(a.container)
    if a.action == "promote":
        if not a.profile:
            print("promote requires --profile <profile.json>", file=sys.stderr)
            return 2
        return cmd_promote(
            a.profile, config_path=a.config, container=a.container,
            cfg_volume=a.cfg_volume, image=a.image, profile_dest=a.profile_dest,
            config_dest=a.config_dest, no_reload=a.no_reload, dry_run=a.dry_run)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
