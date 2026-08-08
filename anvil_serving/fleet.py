"""Cross-host fleet visibility for the declared operator topology.

Feature 8 of docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md ("Undetected version
skew"). On 2026-08-08 a fleet host ran a release two minors behind the
operator host; the older code resolved transports differently and produced
an error naming the wrong cause. Nothing reported that the hosts had drifted
apart. This module gives that a name: ``anvil-serving fleet version``.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys

from .cli import _installed_version

DEFAULT_TIMEOUT_SECONDS = 10.0


def local_version() -> str:
    return _installed_version()


def _parse_version(stdout: str) -> str | None:
    """Parse the trailing version token out of ``anvil-serving X.Y.Z`` output.

    Windows OpenSSH clients can leave a trailing ``\\r`` on captured text-mode
    output even after Python's own universal-newline handling, so the token
    is stripped explicitly rather than trusting ``str.split`` alone.
    """
    text = (stdout or "").strip()
    if not text:
        return None
    line = text.splitlines()[0]
    parts = line.split()
    if not parts:
        return None
    return parts[-1].strip("\r")


def probe_host(host_id: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
               _run=subprocess.run) -> dict:
    """Probe one declared host's installed ``anvil-serving`` version over SSH.

    Never raises: every failure mode (unreachable, not installed, timed out,
    unparsable output) is reported back as a row, not an exception.
    """
    argv = ["ssh", "-n", "-o", "BatchMode=yes", host_id, "anvil-serving", "--version"]
    try:
        result = _run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"host": host_id, "state": "timeout", "version": None,
                "detail": "ssh timed out after %ss" % timeout}
    except FileNotFoundError:
        return {"host": host_id, "state": "unreachable", "version": None,
                "detail": "ssh is not available locally"}

    stderr_first = next(iter((result.stderr or "").strip().splitlines()), "")
    if result.returncode != 0:
        if "not found" in (result.stderr or "").lower():
            return {"host": host_id, "state": "not-installed", "version": None,
                     "detail": stderr_first or "anvil-serving not found"}
        return {"host": host_id, "state": "unreachable", "version": None,
                 "detail": stderr_first or "ssh exited %s" % result.returncode}

    version = _parse_version(result.stdout)
    if version is None:
        return {"host": host_id, "state": "unreachable", "version": None,
                 "detail": "anvil-serving --version produced no parsable output"}
    return {"host": host_id, "state": "ok", "version": version, "detail": None}


def collect_fleet_versions(hosts, local, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                            _run=subprocess.run) -> dict:
    """Pure collection: probe every host and summarize skew. No printing."""
    rows = []
    for host_id in hosts:
        row = probe_host(host_id, timeout=timeout, _run=_run)
        row["skew"] = row["state"] == "ok" and row["version"] != local
        rows.append(row)
    return {
        "local_version": local,
        "hosts": rows,
        "host_count": len(rows),
        "skewed": sum(1 for row in rows if row["skew"]),
        "unreachable": sum(1 for row in rows if row["state"] == "unreachable"),
        "not_installed": sum(1 for row in rows if row["state"] == "not-installed"),
        "timeout": sum(1 for row in rows if row["state"] == "timeout"),
    }


def _local_hostname_matches(local_hostname: str, host_id: str) -> bool:
    local_hostname = local_hostname.lower()
    host_id = host_id.lower()
    return local_hostname.startswith(host_id) or host_id.startswith(local_hostname)


def declared_remote_hosts(topology, *, hostname: str | None = None):
    """Return (remote_host_ids, note) from a loaded topology.

    ``note`` is set (and every host is treated as remote) when the local
    hostname could not be matched to any declared host id -- this is
    reported, never silently assumed.
    """
    local_hostname = hostname if hostname is not None else socket.gethostname()
    matched = {host.id for host in topology.hosts if _local_hostname_matches(local_hostname, host.id)}
    if not matched:
        return [host.id for host in topology.hosts], (
            "could not match local hostname %r to a declared host id; "
            "treating every declared host as remote" % local_hostname
        )
    return [host.id for host in topology.hosts if host.id not in matched], None


def _topology_remote_hosts():
    """Resolve remote hosts from the operator topology's default location.

    Returns (hosts, note) or (None, None) when no topology exists at all.
    """
    from .paths import resolve_topology_path
    from .topology import TopologyValidationError, load_topology

    path = resolve_topology_path(None)
    try:
        topology = load_topology(path)
    except TopologyValidationError:
        return None, None
    return declared_remote_hosts(topology)


def cmd_version(hosts, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, as_json: bool = False,
                 local=None, _run=subprocess.run) -> int:
    """Report per-host version and skew. Print human or JSON; return exit code."""
    local = local_version() if local is None else local

    if not hosts:
        if as_json:
            print(json.dumps({
                "local_version": local, "hosts": [], "host_count": 0, "skewed": 0,
                "unreachable": 0, "not_installed": 0, "timeout": 0,
                "note": "no remote hosts declared",
            }, sort_keys=True))
        else:
            print("local anvil-serving version: %s" % local)
            print("no remote hosts declared")
        return 0

    report = collect_fleet_versions(hosts, local, timeout=timeout, _run=_run)
    if as_json:
        print(json.dumps(report, sort_keys=True))
    else:
        print("%-24s %-13s %-14s %s" % ("HOST", "STATE", "VERSION", "SKEW"))
        for row in report["hosts"]:
            print("%-24s %-13s %-14s %s" % (
                row["host"], row["state"], row["version"] or "-",
                "yes" if row["skew"] else "no"))
        print()
        print("fleet version: local %s, %d host(s), %d skewed, %d unreachable" % (
            local, report["host_count"], report["skewed"], report["unreachable"]))

    # ponytail: an unreachable host is not skew -- a sleeping laptop is an
    # availability gap, not evidence of divergent code (STRATEGY-MAKE-
    # DIVERGENCE-LOUD's availability-class reasoning). Only a *reachable* host
    # running a different version, or a host missing the CLI outright, fails
    # the gate.
    return 1 if (report["skewed"] or report["not_installed"]) else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving fleet")
    actions = parser.add_subparsers(dest="action", required=True)
    version = actions.add_parser("version")
    version.add_argument(
        "--host", action="append", default=None,
        help="Repeatable declared host id; overrides topology-derived hosts.",
    )
    version.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    version.add_argument("--json", action="store_true", dest="json_out")
    return parser


def main(argv=None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)

    if args.action != "version":
        return 2

    if args.host:
        hosts = list(args.host)
    else:
        hosts, note = _topology_remote_hosts()
        if hosts is None:
            print(
                "no operator topology found and no --host given; "
                "pass --host NAME at least once",
                file=sys.stderr,
            )
            return 2
        if note:
            print(note, file=sys.stderr)

    return cmd_version(hosts, timeout=args.timeout, as_json=args.json_out)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
