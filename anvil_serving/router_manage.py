"""anvil-serving router — manage the DEPLOYED (ADR-0004 containerized) anvil-router.

`anvil-serving router run` runs the router in-process; `anvil-serving serves` manages the
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
import ipaddress
import json
import os
import re
import subprocess
import sys
import time

from . import guard
import urllib.request
import urllib.error
import urllib.parse

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - guarded by requires-python >=3.11
    tomllib = None

# Reuse the tested container-state probe from the serves verb (distinguishes
# running / absent / error) instead of re-deriving it here.
from .paths import config_path
from .serves import docker_state

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

DEFAULT_COMPOSE = os.path.join(REPO, "examples", "fakoli-dark", "docker-compose.yml")
CONFIG_HOME_COMPOSE = "~/.anvil-serving/docker-compose.yml"
DEFAULT_CONTAINER = "anvil-router"
DEFAULT_SERVICE = "router"
DEFAULT_CFG_VOLUME = "anvil-router-cfg"
# Pinned to the DEPLOYED image (ADR-0004 / the compose file): validating and writing
# with the LIVE image's own loader is what makes promote version-safe — a newer local
# checkout must not re-verdict a profile the deployed router would reject. Keep this in
# lockstep with the `router` service image in examples/fakoli-dark/docker-compose.yml.
DEFAULT_IMAGE = "anvil-serving:0.12.0"
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
DEFAULT_ROUTER_URL = "http://127.0.0.1:8000"
TRANSITION_PATH = "/v1/admin/transition"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _safe_router_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("router_url must be an HTTP(S) URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        raise ValueError("router_url must use a literal private IP address") from None
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    if not (address.is_loopback or address.is_private or address in cgnat):
        raise ValueError("router_url must use a loopback, private, or tailnet address")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("router_url must not contain credentials, query, or fragment")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def transition_request(
    action,
    *,
    tier_id=None,
    timeout=None,
    router_url=None,
    confirm=False,
    dry_run=True,
    reason="promotion",
    env=None,
    _open=None,
):
    """Call the router-owned transition boundary and return a dictionary."""
    if action not in ("status", "quiesce", "drain", "readmit"):
        raise ValueError("unsupported transition action")
    if action != "status" and (not isinstance(tier_id, str) or not tier_id):
        raise ValueError("tier_id is required")
    environ = os.environ if env is None else env
    base = _safe_router_url(
        router_url or environ.get("ANVIL_ROUTER_URL") or DEFAULT_ROUTER_URL
    )
    if action in ("quiesce", "readmit") and (not confirm or dry_run):
        return {
            "applied": False,
            "dry_run": True,
            "action": action,
            "tier_id": tier_id,
            "router_url": base,
        }
    token = environ.get("ANVIL_ROUTER_TOKEN") or ""
    if not token:
        raise ValueError("ANVIL_ROUTER_TOKEN is required")
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + token,
    }
    if _open is None:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        ).open
    else:
        opener = _open
    request_timeout = 5.0
    if action == "status":
        query = "" if tier_id is None else "?" + urllib.parse.urlencode({"tier_id": tier_id})
        request = urllib.request.Request(base + TRANSITION_PATH + query, headers=headers)
    else:
        body = {
            "action": action,
            "tier_id": tier_id,
            "confirm": bool(confirm),
            "dry_run": bool(dry_run),
            "reason": reason,
        }
        if action == "drain":
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or timeout <= 0
                or timeout > 3600
            ):
                raise ValueError("timeout must be between 0 and 3600 seconds")
            body["timeout"] = float(timeout)
            request_timeout = float(timeout) + 5.0
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            base + TRANSITION_PATH, data=payload, headers=headers, method="POST"
        )
    try:
        with opener(request, timeout=request_timeout) as response:
            raw = response.read(256 * 1024 + 1)
            status = getattr(response, "status", None) or response.getcode()
    except urllib.error.HTTPError as exc:
        raise ValueError("router transition request failed with HTTP %s" % exc.code) from None
    except Exception as exc:
        raise ValueError(
            "router transition transport failed (%s)" % type(exc).__name__
        ) from None
    if len(raw) > 256 * 1024:
        raise ValueError("router transition response was oversized")
    if not isinstance(status, int) or not 200 <= status < 300:
        raise ValueError("router transition request failed")
    try:
        result = json.loads(raw)
    except Exception:
        raise ValueError("router transition response was malformed") from None
    if not isinstance(result, dict):
        raise ValueError("router transition response was malformed")
    return result


def default_compose_candidates():
    return [config_path("docker-compose.yml"), DEFAULT_COMPOSE]


def resolve_compose_path(path=None):
    if path:
        return path
    for candidate in default_compose_candidates():
        if os.path.isfile(os.path.expanduser(candidate)):
            return candidate
    return DEFAULT_COMPOSE


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


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_router_config(path):
    if tomllib is None:  # pragma: no cover - requires-python >=3.11
        raise RuntimeError("tomllib unavailable (need Python >= 3.11)")
    with open(path, "rb") as f:
        raw = f.read()
    return tomllib.loads(raw.decode("utf-8"))


def _profile_rows(profile):
    rows = profile.get("entries")
    if rows is None:
        rows = profile.get("rows", [])
    return rows if isinstance(rows, list) else []


def _profile_row_map(profile):
    mapped = {}
    for row in _profile_rows(profile):
        if not isinstance(row, dict):
            continue
        key = (str(row.get("tier_id", "")), str(row.get("work_class", "")))
        if key[0] and key[1]:
            mapped[key] = {
                "tier_id": key[0],
                "work_class": key[1],
                "decision": row.get("decision"),
                "quality_score": row.get("quality_score"),
                "sample_n": row.get("sample_n"),
                "fingerprint": row.get("fingerprint"),
            }
    return mapped


def _profile_summary(profile):
    rows = _profile_row_map(profile)
    decisions = {}
    for row in rows.values():
        decision = str(row.get("decision") or "-")
        decisions[decision] = decisions.get(decision, 0) + 1
    return {
        "schema": profile.get("schema"),
        "mode": profile.get("mode"),
        "row_count": len(rows),
        "tiers": sorted({key[0] for key in rows}),
        "work_classes": sorted({key[1] for key in rows}),
        "decisions": dict(sorted(decisions.items())),
    }


def _profile_diff(current, candidate, limit=50):
    before = _profile_row_map(current)
    after = _profile_row_map(candidate)
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    changed = []
    for key in sorted(before_keys & after_keys):
        old = before[key]
        new = after[key]
        fields = [
            name for name in ("decision", "quality_score", "sample_n", "fingerprint")
            if old.get(name) != new.get(name)
        ]
        if fields:
            changed.append({
                "tier_id": key[0],
                "work_class": key[1],
                "fields": fields,
                "before": {name: old.get(name) for name in fields},
                "after": {name: new.get(name) for name in fields},
            })
    return {
        "current_profile_provided": True,
        "added": [{"tier_id": k[0], "work_class": k[1]} for k in added[:limit]],
        "removed": [{"tier_id": k[0], "work_class": k[1]} for k in removed[:limit]],
        "changed": changed[:limit],
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "truncated": len(added) > limit or len(removed) > limit or len(changed) > limit,
    }


def promotion_preview(profile_path, *, config_path=None, current_profile_path=None,
                      current_config_path=None, profile_dest=DEFAULT_PROFILE_DEST,
                      config_dest=DEFAULT_CONFIG_DEST, diff_limit=50):
    """Validate a candidate promotion and return a compact, non-mutating diff."""
    from .router.profile_bootstrap import store_from_profile

    profile = _read_json(profile_path)
    store_from_profile(profile)
    config = None
    config_summary = None
    if config_path is not None:
        config = _read_router_config(config_path)
        router_section = config.get("router") if isinstance(config, dict) else None
        if not (isinstance(router_section, dict) and router_section.get("profile_path")):
            raise ValueError(
                "config %s does not set [router].profile_path" % config_path
            )
        if router_section.get("profile_path") != profile_dest:
            raise ValueError(
                "config %s sets [router].profile_path=%r, but profile_dest is %r"
                % (config_path, router_section.get("profile_path"), profile_dest)
            )
        config_summary = {
            "profile_path": router_section.get("profile_path"),
            "mapping_version": router_section.get("mapping_version"),
        }

    prof_volume_path = _volume_path(profile_dest)
    cfg_volume_path = _volume_path(config_dest)
    diff = {"current_profile_provided": False}
    if current_profile_path:
        current_profile = _read_json(current_profile_path)
        store_from_profile(current_profile)
        diff = _profile_diff(current_profile, profile, limit=diff_limit)

    config_diff = {"current_config_provided": False}
    if current_config_path and config is not None:
        current_config = _read_router_config(current_config_path)
        current_router = current_config.get("router") if isinstance(current_config, dict) else {}
        next_router = config.get("router") if isinstance(config, dict) else {}
        config_diff = {
            "current_config_provided": True,
            "profile_path": {
                "before": current_router.get("profile_path") if isinstance(current_router, dict) else None,
                "after": next_router.get("profile_path") if isinstance(next_router, dict) else None,
            },
            "mapping_version": {
                "before": current_router.get("mapping_version") if isinstance(current_router, dict) else None,
                "after": next_router.get("mapping_version") if isinstance(next_router, dict) else None,
            },
        }

    return {
        "valid": True,
        "profile_path": os.path.abspath(profile_path),
        "config_path": os.path.abspath(config_path) if config_path else None,
        "profile": _profile_summary(profile),
        "config": config_summary,
        "destinations": {
            "profile_dest": profile_dest,
            "profile_volume_path": prof_volume_path,
            "config_dest": config_dest if config_path else None,
            "config_volume_path": cfg_volume_path if config_path else None,
        },
        "diff": diff,
        "config_diff": config_diff,
    }

# Validate a profile document against the DEPLOYED image's OWN loader: import
# store_from_profile and run it on the profile fed via stdin. A non-zero exit /
# raised exception (schema mismatch, bad shape) => ABORT before touching the volume,
# which is exactly what prevents the fail-fast router crash-loop on a bad profile.
_VALIDATOR = (
    "import sys, json; "
    "from anvil_serving.router.profile_bootstrap import store_from_profile; "
    "store_from_profile(json.load(sys.stdin))"
)
_ARTIFACT_VALIDATOR = (
    "import sys, json, os, tempfile; "
    "from anvil_serving.router.profile_bootstrap import store_from_profile; "
    "from anvil_serving.router.config import load, load_server_config; "
    "x=json.load(sys.stdin); store_from_profile(x['profile']); "
    "f=tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False, encoding='utf-8'); "
    "f.write(x['config']); f.close(); load(f.name); load_server_config(f.name); os.unlink(f.name)"
)


def _volume_text(text):
    """Normalize text written into the Linux router config volume.

    PowerShell/Git checkouts on Windows can leave repo TOML files with CRLF
    endings. The deployed router image reads TOML inside Linux, and its parser
    rejects literal ``\r`` bytes in config.toml, so the promotion side-container
    must write LF-normalized payloads.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


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
    """First existing conventional deploy env-file.

    Prefer the anvil-serving operator home, then the legacy `~/.anvil_env`, then
    `~/.env`, so `router up` picks up ANVIL_ROUTER_TOKEN / ROUTER_PUBLISH without
    the operator re-exporting them each deploy.
    """
    candidates = [
        config_path(".env"),
        os.path.join(os.path.expanduser("~"), ".anvil_env"),
        os.path.join(os.path.expanduser("~"), ".env"),
    ]
    for p in candidates:
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
    # --no-deps: manage ONLY the router. Without it, `compose up router` re-runs its `depends_on`
    # and RECREATES the model serves when their resolved config drifts (e.g. a changed --env-file) —
    # a gpt-oss-120b reload is minutes of 503s. The serves are `serves`' job, not the router verb's.
    argv += ["-f", compose, "up", "-d", "--no-deps", service]
    return _run_argv(argv, _run, desc="up %s: %s" % (service, " ".join(argv)),
                     dry_run=dry_run)


def cmd_down(compose, service, dry_run=False, _run=subprocess.run):
    # `stop` (not `down`): free the router without deleting the network/volumes the
    # model serves and config depend on.
    argv = ["docker", "compose", "-f", compose, "stop", service]
    return _run_argv(argv, _run, desc="down %s: %s" % (service, " ".join(argv)),
                     dry_run=dry_run)


def cmd_restart(container, dry_run=False, verify=True, _run=subprocess.run,
                _sleep=time.sleep):
    # Baseline BEFORE the restart so the crash-loop check can tell "the policy
    # bounced it since our restart" from historical restarts.
    baseline = None if (dry_run or not verify) else _restart_count(container, _run)
    argv = ["docker", "restart", container]
    rc = _run_argv(argv, _run, desc="restart %s" % container, dry_run=dry_run)
    if rc != 0 or dry_run or not verify:
        return rc
    # Verify it STAYED up (~11s: settle + consecutive samples) — the same
    # crash-loop check promote uses. A router that fail-fasts on a bad config
    # is bounced back to 'running' by `restart: unless-stopped` before a naive
    # single read, so restart used to report success while it crash-looped.
    # --no-verify opts out for latency-sensitive iteration loops.
    ok, state = _await_running(container, _run, _sleep, baseline_restarts=baseline)
    if not ok:
        print("  FAILED: %s is not staying up after restart (last state: %s) - "
              "check `router logs`; if a recent profile/config change caused "
              "this, `router promote` rollback or a config revert is the fix"
              % (container, state))
        return 1
    print("  %s: running (stable)" % container)
    return 0


def cmd_reload(container, dry_run=False, verify=True, _run=subprocess.run,
               _sleep=time.sleep):
    # The router reads its config + profile ONCE at startup; there is no in-process
    # reload signal. So a reload IS a restart — say so, then restart.
    print("  note: the router loads config/profile at STARTUP; there is no live "
          "reload, so `reload` restarts the container to pick up changes.")
    return cmd_restart(container, dry_run=dry_run, verify=verify, _run=_run,
                       _sleep=_sleep)


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


def status_summary(container, _run=subprocess.run, _open=urllib.request.urlopen, port=8000):
    """Machine-readable router container status for MCP/automation."""
    st = docker_state(container, _run=_run)
    running = st == "running"
    code = _health(_open, port=port) if running else None
    return {
        "container": container,
        "docker_state": st,
        "running": running,
        "health_status": code,
        "health_url": "http://127.0.0.1:%s/" % port if running else None,
        "ok": st != "error",
    }


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


def cmd_token(container, *, reveal=False, _run=subprocess.run):
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
        if not reveal:
            print(
                "auth is SET: ANVIL_ROUTER_TOKEN is configured on %s "
                "(pass --reveal --confirm to print it)." % container
            )
            return 0
        if not guard.confirm("Reveal the deployed router bearer token?"):
            print("token reveal declined", file=sys.stderr)
            return 1
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
    `baseline_restarts` (the policy bounced it) as a crash. Returns (ok, last_state).

    The settle+consecutive-samples discipline itself lives in guard.await_stable
    (the shared verify primitive); this wraps it with the docker-specific state
    read and the RestartCount refinement."""
    last_state = {"st": None}

    def _sample():
        st = docker_state(container, _run=_run)
        last_state["st"] = st
        if st != "running":
            return None  # falsy -> crashed (exited / restarting / created)
        rc = _restart_count(container, _run)
        if baseline_restarts is not None and rc is not None and rc > baseline_restarts:
            # policy re-launched it since our restart -> report as a crash-loop
            last_state["st"] = "restarting"
            return None
        return st

    ok, _ = guard.await_stable(_sample, settle=settle, checks=checks,
                               delay=delay, _sleep=_sleep)
    return ok, last_state["st"]


def cmd_promote(profile_path, *, config_path=None, container=DEFAULT_CONTAINER,
                cfg_volume=DEFAULT_CFG_VOLUME, image=DEFAULT_IMAGE,
                profile_dest=DEFAULT_PROFILE_DEST, config_dest=DEFAULT_CONFIG_DEST,
                no_reload=False, validate_only=False, dry_run=False,
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
    profile_text = _volume_text(profile_text)

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
        config_text = _volume_text(raw.decode("utf-8"))

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
    validator = _ARTIFACT_VALIDATOR if config_text is not None else _VALIDATOR
    validation_text = (
        json.dumps({"profile": json.loads(profile_text), "config": config_text})
        if config_text is not None else profile_text
    )
    val_argv = ["docker", "run", "--rm", "-i", "--entrypoint", "python",
                image, "-c", validator]
    print("  validate: %s (against %s loader)" % (profile_path, image))
    if not dry_run:
        try:
            r = _run(
                val_argv,
                input=validation_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except FileNotFoundError:
            print("  ABORT: docker not available")
            return 1
        if r.returncode != 0:
            print("  ABORT: profile rejected by the deployed router's loader "
                  "(exit %s): %s" % (r.returncode, (r.stderr or r.stdout or "").strip()))
            print("  (no volume was touched — this prevents a crash-loop on a bad profile.)")
            return 1

    if validate_only:
        print("  OK: profile/config accepted by the deployed router image; no state changed.")
        return 0

    # -- (b) BACK UP the current profile (and config, if promoting one) -------
    # so a crash-loop can be rolled back symmetrically. `.bak` absent => there was
    # no prior file (first-ever promote); the rollback below handles that case.
    bak_cmds = ["[ -f %s ] && cp %s %s || true" % (prof, prof, prof_bak)]
    if config_text is not None:
        bak_cmds.append("[ -f %s ] && cp %s %s || true" % (cfg_file, cfg_file, cfg_bak))
    # `--entrypoint sh`: the router IMAGE's default entrypoint is `anvil-serving router run`,
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
        r = _run(
            write_argv,
            input=profile_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if r.returncode != 0:
            print("  FAILED to write profile: %s" % (r.stderr or r.stdout or "").strip())
            return 1

    if config_text is not None:
        write_cfg_argv = ["docker", "run", "--rm", "-i", "--user", "0", "-v", vol_mount,
                          "--entrypoint", "sh", image, "-c",
                          "mkdir -p %s && cat > %s && mv %s %s" % (cfg_dir, cfg_new, cfg_new, cfg_file)]
        print("  write config: %s (atomic mv within the volume)" % cfg_file)
        if not dry_run:
            r = _run(
                write_cfg_argv,
                input=config_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
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

def _normalize_leading_options(argv, actions, option_arity):
    """Allow legacy `router --container X status` while using action subparsers."""
    action_index = None
    for i, arg in enumerate(argv):
        if arg in actions:
            action_index = i
            break
    if not action_index:
        return argv
    leading = argv[:action_index]
    rest = argv[action_index:]
    normalized = [rest[0]]
    i = 0
    while i < len(leading):
        arg = leading[i]
        normalized.append(arg)
        if arg.startswith("--") and "=" not in arg:
            arity = option_arity.get(arg, 0)
            for _ in range(arity):
                i += 1
                if i < len(leading):
                    normalized.append(leading[i])
        i += 1
    normalized.extend(rest[1:])
    return normalized


def _build_parser():
    p = argparse.ArgumentParser(
        prog="anvil-serving router",
        description="Manage the DEPLOYED (containerized, ADR-0004) anvil-router: "
                    "lifecycle, token, status, and the promote write-back path.")
    sub = p.add_subparsers(dest="action", required=True)

    def add_container(sp):
        sp.add_argument("--container", default=DEFAULT_CONTAINER,
                        help="router container name (default: %(default)s).")

    def add_dry_run(sp):
        sp.add_argument("--dry-run", action="store_true",
                        help="print what would run without executing any docker command.")

    for action, help_text in (
        ("up", "Bring up the deployed router through docker compose."),
        ("down", "Stop the deployed router compose service."),
    ):
        sp = sub.add_parser(action, help=help_text, description=help_text)
        sp.add_argument("--compose",
                        help="docker-compose.yml (default: ~/.anvil-serving/docker-compose.yml if present, else the fakoli-dark example).")
        sp.add_argument("--service", default=DEFAULT_SERVICE,
                        help="compose service name for the router (default: %(default)s).")
        add_dry_run(sp)
        if action == "up":
            sp.add_argument("--env-file", default=None,
                            help="env file for docker compose --env-file; default: ~/.anvil_env or ~/.env if present (pass '' to disable).")

    for action, help_text in (
        ("restart", "Restart the deployed router container."),
        ("reload", "Restart the deployed router so it reloads startup-read config."),
    ):
        sp = sub.add_parser(action, help=help_text, description=help_text)
        add_container(sp)
        add_dry_run(sp)
        sp.add_argument("--no-verify", action="store_true",
                        help="skip the ~11s stay-up verification (settle + consecutive "
                             "running samples) after the restart; use for rapid "
                             "iteration loops where you check health yourself.")

    sp = sub.add_parser("status", help="Show deployed router container and health status.")
    add_container(sp)

    for action, help_text in (
        ("transition-status", "Show router-owned tier transition state."),
        ("quiesce", "Stop new requests from entering one router tier."),
        ("drain", "Wait for a quiesced tier's active requests to finish."),
        ("readmit", "Readmit a tier only after current readiness passes."),
    ):
        sp = sub.add_parser(action, help=help_text, description=help_text)
        sp.add_argument("--tier", required=action != "transition-status")
        sp.add_argument("--router-url", default=None,
                        help="private router base URL (default: ANVIL_ROUTER_URL or http://127.0.0.1:8000).")
        if action == "drain":
            sp.add_argument("--timeout", type=float, required=True)
        if action in ("quiesce", "readmit"):
            sp.add_argument("--confirm", action="store_true")
            sp.add_argument("--dry-run", action="store_true", default=False)

    sp = sub.add_parser("logs", help="Show docker logs for the deployed router container.")
    add_container(sp)
    sp.add_argument("--tail", default="200", help="number of trailing lines to show (default: %(default)s; 'all').")
    sp.add_argument("--since", help="only show logs since a timestamp or relative time (e.g. 10m, 1h).")
    sp.add_argument("--follow", action="store_true", help="stream new log output (Ctrl-C to stop).")

    sp = sub.add_parser("token", help="Print the deployed router bearer token.")
    add_container(sp)
    sp.add_argument(
        "--reveal",
        action="store_true",
        help="print the token value after an interactive or dispatcher confirmation",
    )

    sp = sub.add_parser("promote", help="Validate and write a reviewed profile/config into the deployed router config volume.")
    add_container(sp)
    add_dry_run(sp)
    sp.add_argument("--profile", required=True, help="path to the profile.json to promote.")
    sp.add_argument("--config", help="optional config.toml to write alongside it.")
    sp.add_argument("--cfg-volume", default=DEFAULT_CFG_VOLUME,
                    help="router config docker volume (default: %(default)s).")
    sp.add_argument("--image", default=DEFAULT_IMAGE,
                    help="deployed router image used to validate + write (default: %(default)s).")
    sp.add_argument("--profile-dest", default=DEFAULT_PROFILE_DEST,
                    help="router-visible profile path (default: %(default)s).")
    sp.add_argument("--config-dest", default=DEFAULT_CONFIG_DEST,
                    help="router-visible config path (default: %(default)s).")
    sp.add_argument("--no-reload", action="store_true",
                    help="write the profile but don't restart the router.")
    sp.add_argument("--validate-only", action="store_true",
                    help="validate profile/config with the deployed image without writing or reloading.")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = _normalize_leading_options(
        argv,
        {"up", "down", "restart", "reload", "status", "logs", "token", "promote",
         "transition-status", "quiesce", "drain", "readmit"},
        {
            "--container": 1, "--compose": 1, "--service": 1, "--env-file": 1,
            "--tail": 1, "--since": 1, "--profile": 1, "--config": 1,
            "--cfg-volume": 1, "--image": 1, "--profile-dest": 1,
            "--config-dest": 1,
        },
    )
    p = _build_parser()
    try:
        a = p.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            raise
        return int(exc.code or 2)

    if a.action == "up":
        # explicit --env-file wins; unset -> auto-detect conventional env files; '' -> disable.
        env_file = _default_env_file() if a.env_file is None else (a.env_file or None)
        return cmd_up(resolve_compose_path(a.compose), a.service, env_file=env_file, dry_run=a.dry_run)
    if a.action == "down":
        return cmd_down(resolve_compose_path(a.compose), a.service, dry_run=a.dry_run)
    if a.action == "restart":
        return cmd_restart(a.container, dry_run=a.dry_run, verify=not a.no_verify)
    if a.action == "reload":
        return cmd_reload(a.container, dry_run=a.dry_run, verify=not a.no_verify)
    if a.action == "status":
        return cmd_status(a.container)
    if a.action in {"transition-status", "quiesce", "drain", "readmit"}:
        action = "status" if a.action == "transition-status" else a.action
        try:
            result = transition_request(
                action,
                tier_id=getattr(a, "tier", None),
                timeout=getattr(a, "timeout", None),
                router_url=a.router_url,
                confirm=getattr(a, "confirm", False),
                dry_run=(getattr(a, "dry_run", False) or not getattr(a, "confirm", False))
                if action in ("quiesce", "readmit") else False,
            )
        except ValueError as exc:
            print("router transition failed: %s" % exc, file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        if action == "drain":
            drain = result.get("result", result)
            if isinstance(drain, dict) and not drain.get("drained", False):
                return 1
        if action == "status":
            rows = result.get("tiers", [])
            if isinstance(rows, list) and any(
                isinstance(row, dict) and row.get("ready") is False for row in rows
            ):
                return 1
        if action == "readmit":
            readmit = result.get("result", result)
            if isinstance(readmit, dict) and readmit.get("readmitted") is False:
                return 1
        return 0
    if a.action == "logs":
        return cmd_logs(a.container, tail=a.tail, since=a.since, follow=a.follow)
    if a.action == "token":
        return cmd_token(a.container, reveal=a.reveal)
    if a.action == "promote":
        return cmd_promote(
            a.profile, config_path=a.config, container=a.container,
            cfg_volume=a.cfg_volume, image=a.image, profile_dest=a.profile_dest,
            config_dest=a.config_dest, no_reload=a.no_reload,
            validate_only=a.validate_only, dry_run=a.dry_run)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
