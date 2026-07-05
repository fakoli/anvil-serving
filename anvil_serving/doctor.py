"""anvil-serving doctor — environment preflight (genericity:T015).

Checks the local environment a router deploy depends on and prints a
per-check PASS/WARN/FAIL report:

  - Python >= 3.11 (required: `tomllib` powers the router config loader)
  - docker present (required)
  - `docker compose` v2 (required)
  - NVIDIA container runtime registered with docker (advisory — you may be
    running the router only, with serves elsewhere)
  - GPU visibility via `nvidia-smi` (advisory, same reason)
  - each tier's `/health` in a router config (default `./router.toml`, the
    file `anvil-serving init` writes; skipped quietly if absent and not
    explicitly requested — advisory either way, a tier can be legitimately
    stopped between sessions)

Exits non-zero iff a REQUIRED check failed. Every external call (`_run` for
subprocess, `_gpu_run` for `nvidia-smi`, `_open` for the health probe) is
injectable, so this runs deterministically with no docker/GPU/network.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

from . import gpus as _gpus

DEFAULT_CONFIG = "./router.toml"


class Check:
    """One diagnostic result: PASS (`ok`), FAIL (`required and not ok`), or
    WARN (`not required and not ok`)."""

    def __init__(self, name, ok, detail="", required=True):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.required = required

    @property
    def status(self):
        if self.ok:
            return "PASS"
        return "FAIL" if self.required else "WARN"

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Check({self.name!r}, {self.status}, {self.detail!r})"


def check_python(min_version=(3, 11)):
    ok = sys.version_info[:2] >= min_version
    detail = "%d.%d.%d" % sys.version_info[:3]
    return Check("Python >= %d.%d" % min_version, ok, detail, required=True)


def check_docker(_run=subprocess.run):
    try:
        r = _run(["docker", "--version"], capture_output=True, text=True)
    except FileNotFoundError:
        return Check("docker", False, "not found on PATH", required=True)
    except Exception as e:
        return Check("docker", False, str(e), required=True)
    if r.returncode != 0:
        return Check("docker", False, (r.stderr or r.stdout or "").strip(), required=True)
    return Check("docker", True, (r.stdout or "").strip(), required=True)


def check_docker_compose(_run=subprocess.run):
    try:
        r = _run(["docker", "compose", "version"], capture_output=True, text=True)
    except FileNotFoundError:
        return Check("docker compose v2", False, "not found", required=True)
    except Exception as e:
        return Check("docker compose v2", False, str(e), required=True)
    if r.returncode != 0:
        return Check("docker compose v2", False, (r.stderr or r.stdout or "").strip(), required=True)
    return Check("docker compose v2", True, (r.stdout or "").strip(), required=True)


def check_nvidia_runtime(_run=subprocess.run):
    try:
        r = _run(["docker", "info", "--format", "{{json .Runtimes}}"],
                 capture_output=True, text=True)
    except FileNotFoundError:
        return Check("NVIDIA container runtime", False, "docker not found", required=False)
    except Exception as e:
        return Check("NVIDIA container runtime", False, str(e), required=False)
    if r.returncode != 0:
        return Check("NVIDIA container runtime", False,
                     (r.stderr or r.stdout or "").strip(), required=False)
    out = (r.stdout or "").strip()
    # `docker info --format '{{json .Runtimes}}'` is {"<name>": {...OCI feature
    # blob...}, ...} — parse it so the reported detail is just the top-level
    # runtime NAMES, not a wall of nested JSON. Fall back to a raw substring
    # check if the output isn't valid JSON (unexpected docker version/format).
    try:
        names = sorted(json.loads(out).keys())
        ok = "nvidia" in names
        detail = "registered: " + ", ".join(names) if names else "no runtimes registered"
    except Exception:
        ok = "nvidia" in out
        detail = out[:200]
    return Check("NVIDIA container runtime", ok, detail, required=False)


def check_gpu_visibility(_run=subprocess.check_output):
    rows = _gpus.list_gpus(_run=_run)
    if rows:
        detail = ", ".join(f"{g['index']}:{g['name']}" for g in rows)
        return Check("GPU visibility (nvidia-smi)", True, detail, required=False)
    return Check("GPU visibility (nvidia-smi)", False,
                "no GPU visible (nvidia-smi absent, or reports none)", required=False)


def check_tier_health(tier_id, base_url, health_path="/health", _open=urllib.request.urlopen):
    """Probe `<base_url without a trailing /v1><health_path>`: `/health` lives
    at the server root, not under `/v1` (matches `serves.py`'s `_health`)."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    url = root + health_path
    try:
        with _open(url, timeout=3):
            return Check(f"tier {tier_id!r} {health_path}", True, url, required=False)
    except Exception as e:
        return Check(f"tier {tier_id!r} {health_path}", False, f"{url}: {e}", required=False)


def run_checks(config_path=None, config_explicit=False, _run=subprocess.run,
               _gpu_run=subprocess.check_output, _open=urllib.request.urlopen):
    """The full check list. `config_path=None` skips the tier-health section
    entirely; a missing (non-explicit) default config is skipped quietly —
    only an EXPLICITLY requested missing config is a FAILED check."""
    checks = [
        check_python(),
        check_docker(_run=_run),
        check_docker_compose(_run=_run),
        check_nvidia_runtime(_run=_run),
        check_gpu_visibility(_run=_gpu_run),
    ]
    if config_path is None:
        return checks
    if not os.path.isfile(config_path):
        if config_explicit:
            checks.append(Check(f"router config {config_path!r}", False, "not found", required=True))
        return checks
    from .router import config as router_config
    try:
        cfg = router_config.load(config_path)
    except Exception as e:
        checks.append(Check(f"router config {config_path!r}", False, str(e), required=True))
        return checks
    for tier in cfg.tiers:
        checks.append(check_tier_health(tier.id, tier.base_url, _open=_open))
    return checks


def checks_summary(config_path=None, config_explicit=False, _run=subprocess.run,
                   _gpu_run=subprocess.check_output, _open=urllib.request.urlopen):
    """Machine-readable doctor summary for MCP/automation."""
    checks = run_checks(config_path=config_path, config_explicit=config_explicit,
                        _run=_run, _gpu_run=_gpu_run, _open=_open)
    ok = all((not c.required) or c.ok for c in checks)
    return {
        "ok": ok,
        "checks": [{
            "name": c.name,
            "ok": c.ok,
            "status": c.status,
            "detail": c.detail,
            "required": c.required,
        } for c in checks],
    }


def main(argv):
    ap = argparse.ArgumentParser(
        prog="anvil-serving doctor",
        description="Diagnose the local environment: Python, docker, GPU, and "
                    "configured tier health. Non-zero exit on a failed required check.")
    ap.add_argument("--config", default=None,
                    help="router config to probe tier /health from "
                         "(default: %s if present)" % DEFAULT_CONFIG)
    ap.add_argument("--no-config", action="store_true",
                    help="skip the tier /health checks entirely")
    a = ap.parse_args(argv)

    explicit = a.config is not None
    config_path = None if a.no_config else (a.config or DEFAULT_CONFIG)

    checks = run_checks(config_path=config_path, config_explicit=explicit)
    ok = True
    for c in checks:
        line = f"[{c.status}] {c.name}"
        if c.detail:
            line += f" — {c.detail}"
        print(line)
        if c.required and not c.ok:
            ok = False
    print()
    print("OK" if ok else "FAILED (a required check did not pass)")
    return 0 if ok else 1
