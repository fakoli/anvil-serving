"""Cross-host fleet visibility for the declared operator topology.

Feature 8 of docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md ("Undetected version
skew"). On 2026-08-08 a fleet host ran a release two minors behind the
operator host; the older code resolved transports differently and produced
an error naming the wrong cause. Nothing reported that the hosts had drifted
apart. This module gives that a name: ``anvil-serving fleet version``.

Feature 7, ``anvil-serving fleet drift``, is the sibling report: a host's
live operator home diverging from the repository snapshot for that host.
Observed live on the same day -- a live home six commits behind its repo
while serving production, and a second host whose live home was a wholesale
byte-copy of the wrong host's home.
"""

from __future__ import annotations

import argparse
import hashlib
import base64
import json
import os
import socket
import subprocess
import sys

from .cli import _installed_version
from .operator_output import CommandResult, OperatorError, UsageError
from .paths import config_home

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


def probe_host(
    host_id: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, _run=subprocess.run
) -> dict:
    """Probe one declared host's installed ``anvil-serving`` version over SSH.

    Never raises: every failure mode (unreachable, not installed, timed out,
    unparsable output) is reported back as a row, not an exception.
    """
    argv = ["ssh", "-n", "-o", "BatchMode=yes", host_id, "anvil-serving", "--version"]
    try:
        result = _run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "host": host_id,
            "state": "timeout",
            "version": None,
            "detail": "ssh timed out after %ss" % timeout,
        }
    except FileNotFoundError:
        return {
            "host": host_id,
            "state": "unreachable",
            "version": None,
            "detail": "ssh is not available locally",
        }

    stderr_first = next(iter((result.stderr or "").strip().splitlines()), "")
    if result.returncode != 0:
        if "not found" in (result.stderr or "").lower():
            return {
                "host": host_id,
                "state": "not-installed",
                "version": None,
                "detail": stderr_first or "anvil-serving not found",
            }
        return {
            "host": host_id,
            "state": "unreachable",
            "version": None,
            "detail": stderr_first or "ssh exited %s" % result.returncode,
        }

    version = _parse_version(result.stdout)
    if version is None:
        return {
            "host": host_id,
            "state": "unreachable",
            "version": None,
            "detail": "anvil-serving --version produced no parsable output",
        }
    return {"host": host_id, "state": "ok", "version": version, "detail": None}


def collect_fleet_versions(
    hosts, local, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, _run=subprocess.run
) -> dict:
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
    # Token match, not prefix and not bare containment: a repo may key a
    # host by its short name ("dark") while the machine reports
    # "fakoli-dark", so prefix matching classified the local host as remote
    # and ssh'd to itself (observed live). Bare containment over-corrects —
    # "w" is a substring of "elsewhere" (caught by test) — so the short name
    # must match a whole '-'/'.'-separated token of the longer name.
    # Compare on the first DNS label: macOS reports "fakoli-mini.local".
    label = local_hostname.split(".")[0]

    def _tokens(value):
        return value.split("-")

    if label == host_id:
        return True
    return host_id in _tokens(label) or label in _tokens(host_id)


def declared_remote_hosts(topology, *, hostname: str | None = None):
    """Return (remote_host_ids, note) from a loaded topology.

    ``note`` is set (and every host is treated as remote) when the local
    hostname could not be matched to any declared host id -- this is
    reported, never silently assumed.
    """
    local_hostname = hostname if hostname is not None else socket.gethostname()
    matched = {
        host.id for host in topology.hosts if _local_hostname_matches(local_hostname, host.id)
    }
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


def cmd_version(
    hosts,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    as_json: bool = False,
    local=None,
    _run=subprocess.run,
) -> int:
    """Report per-host version and skew. Print human or JSON; return exit code."""
    result = fleet_version_result(
        hosts,
        timeout=timeout,
        local=local,
        _run=_run,
    )
    if as_json:
        print(json.dumps(result.data, sort_keys=True))
    else:
        sys.stdout.write(result.human_stdout)
    return result.exit_code


def _fleet_version_human(report: dict) -> str:
    """Render the stable human view of one typed Fleet version report."""
    local = report["local_version"]
    if not report["hosts"]:
        return "local anvil-serving version: %s\nno remote hosts declared\n" % local
    lines = ["%-24s %-13s %-14s %s" % ("HOST", "STATE", "VERSION", "SKEW")]
    for row in report["hosts"]:
        lines.append(
            "%-24s %-13s %-14s %s"
            % (
                row["host"],
                row["state"],
                row["version"] or "-",
                "yes" if row["skew"] else "no",
            )
        )
    lines.extend(
        (
            "",
            (
                "fleet version: local %s, %d host(s), %d skewed, %d not installed, "
                "%d unreachable, %d timed out"
            )
            % (
                local,
                report["host_count"],
                report["skewed"],
                report["not_installed"],
                report["unreachable"],
                report["timeout"],
            ),
        )
    )
    return "\n".join(lines) + "\n"


def fleet_version_result(
    hosts,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    local=None,
    _run=subprocess.run,
    warnings=(),
) -> CommandResult:
    """Return the typed report and release-gate disposition without printing."""
    local = local_version() if local is None else local
    report = collect_fleet_versions(hosts, local, timeout=timeout, _run=_run)
    if not hosts:
        report["note"] = "no remote hosts declared"

    error = None
    # An unavailable host is not proof of divergent code. A reachable host
    # running another version, or one missing the CLI, fails the release gate.
    if report["skewed"] or report["not_installed"]:
        reasons = []
        if report["skewed"]:
            reasons.append("%d version-skewed" % report["skewed"])
        if report["not_installed"]:
            reasons.append("%d missing installation" % report["not_installed"])
        error = OperatorError(
            "Fleet version gate failed: " + ", ".join(reasons),
            code="fleet_version_gate_failed",
            details={
                "skewed": report["skewed"],
                "not_installed": report["not_installed"],
            },
        )
    return CommandResult(
        data=report,
        error=error,
        warnings=tuple(warnings),
        human_stdout=_fleet_version_human(report),
    )


def _sha256_normalized(data: bytes) -> str:
    # ponytail: newline-normalize before hashing so a Windows CRLF checkout of
    # the repo snapshot never reads as "differs" against a POSIX live home
    # (or vice versa) when the bytes are otherwise identical.
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def repo_operator_home(repo: str, host_id: str) -> str:
    return os.path.join(repo, "hosts", host_id, "operator-home")


def discover_repo_hosts(repo: str) -> list[str]:
    """Every host directory under ``repo/hosts`` that has an operator-home/."""
    hosts_root = os.path.join(repo, "hosts")
    if not os.path.isdir(hosts_root):
        return []
    return sorted(
        name
        for name in os.listdir(hosts_root)
        if os.path.isdir(os.path.join(hosts_root, name, "operator-home"))
    )


def repo_tracked_files(operator_home_dir: str) -> list[str]:
    """Sorted repo-relative posix paths of every file under an operator-home/.

    Only files that exist in the repository snapshot are ever compared or
    read live -- this is what keeps ``.env``, backups, and lock files (which
    are expected to exist live and never in the repo) out of scope, without
    needing a live-directory listing or a credential-content read.
    """
    out = []
    for root, _dirs, files in os.walk(operator_home_dir):
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), operator_home_dir)
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def _repo_hashes(operator_home_dir: str, names: list[str]) -> dict:
    hashes = {}
    for name in names:
        path = os.path.join(operator_home_dir, *name.split("/"))
        with open(path, "rb") as handle:
            hashes[name] = _sha256_normalized(handle.read())
    return hashes


def _diff_from_hashes(repo_hashes: dict, live_hashes: dict, names: list[str]) -> list[dict]:
    files = []
    for name in names:
        live_hash = live_hashes.get(name)
        if live_hash is None:
            files.append({"path": name, "status": "missing-live"})
        elif live_hash == repo_hashes[name]:
            files.append({"path": name, "status": "identical"})
        else:
            files.append({"path": name, "status": "differs"})
    return files


def _local_live_hashes(live_home: str, names: list[str]) -> dict:
    hashes = {}
    for name in names:
        path = os.path.join(live_home, *name.split("/"))
        try:
            with open(path, "rb") as handle:
                hashes[name] = _sha256_normalized(handle.read())
        except OSError:
            hashes[name] = None
    return hashes


def _remote_hash_script(names: list[str]) -> str:
    """A standalone ``python3 -c`` script printing ``{name: sha256|null}`` as JSON.

    Duplicates paths.config_home()'s env-var-or-default resolution instead of
    importing anvil_serving remotely: only the ``anvil-serving`` console
    script is assumed to be on the remote PATH (matching ``fleet version``'s
    assumption), not an importable package on whatever ``python3`` resolves
    to. One ``ssh`` call per host, batching every repo-tracked filename into
    a single probe rather than one round trip per file.
    """
    return (
        "import hashlib,json,os\n"
        "home=os.environ.get('ANVIL_SERVING_HOME') or os.path.expanduser('~/.anvil-serving')\n"
        "home=os.path.abspath(os.path.expanduser(home))\n"
        "out={}\n"
        "for name in %r:\n"
        "    p=os.path.join(home,*name.split('/'))\n"
        "    try:\n"
        "        f=open(p,'rb')\n"
        "    except OSError:\n"
        "        out[name]=None\n"
        "        continue\n"
        "    data=f.read()\n"
        "    f.close()\n"
        "    out[name]=hashlib.sha256(data.replace(b'\\r\\n', b'\\n')).hexdigest()\n"
        "print(json.dumps(out))\n"
    ) % (list(names),)


def probe_drift_host(
    host_id: str,
    repo_hashes: dict,
    names: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    _run=subprocess.run,
) -> dict:
    """Fetch one remote host's live file hashes over SSH and diff them.

    Never raises: every failure mode is reported back as a row, not an
    exception (same contract as ``probe_host``).
    """
    # Windows hosts ship `python`, not `python3` (observed live: the voice
    # host answered "not recognized" and read as unreachable). Try python3
    # first, fall back to python on a launcher miss — never on a script error.
    script = _remote_hash_script(names)
    # The remote shell on a Windows host is cmd.exe, which shreds a multi-line
    # `-c` script (observed live: quoting mangled, host read unreachable).
    # Base64 makes the payload a single argument with no shell-active
    # characters on any remote shell; the wrapper is plain ASCII either side.
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    wrapper = "import base64;exec(base64.b64decode('%s').decode('utf-8'))" % encoded
    result = None
    stderr_first = ""
    for launcher in ("python3", "python"):
        # The wrapper contains a space ("import base64;..."), and ssh joins
        # argv with spaces for the REMOTE shell to re-split -- on Windows,
        # cmd.exe hands python only the first token (observed live). Quote it
        # client-side; harmless on POSIX remotes.
        argv = ["ssh", "-n", "-o", "BatchMode=yes", host_id, launcher, "-c", '"%s"' % wrapper]
        try:
            result = _run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {
                "host": host_id,
                "state": "timeout",
                "detail": "ssh timed out after %ss" % timeout,
                "files": [],
            }
        except FileNotFoundError:
            return {
                "host": host_id,
                "state": "unreachable",
                "detail": "ssh is not available locally",
                "files": [],
            }
        stderr_lines = [
            line
            for line in (result.stderr or "").strip().splitlines()
            if not line.startswith("** ")  # client-side ssh advisory banners
        ]
        stderr_first = next(iter(stderr_lines), "")
        if result.returncode == 0:
            break
        lowered = (result.stderr or "").lower()
        if "not recognized" not in lowered and "not found" not in lowered:
            break  # a real failure, not a launcher miss -- do not retry
    if result.returncode != 0:
        return {
            "host": host_id,
            "state": "unreachable",
            "detail": stderr_first or "ssh exited %s" % result.returncode,
            "files": [],
        }

    try:
        live_hashes = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {
            "host": host_id,
            "state": "unreachable",
            "detail": "drift probe produced no parsable output",
            "files": [],
        }

    return {
        "host": host_id,
        "state": "ok",
        "detail": None,
        "files": _diff_from_hashes(repo_hashes, live_hashes, names),
    }


def collect_host_drift(
    host_id: str,
    repo_home: str,
    *,
    home: str | None = None,
    hostname: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    _run=subprocess.run,
) -> dict:
    """Diff one host's repo snapshot against its live operator home.

    LOCAL is decided by the same hostname-prefix match ``fleet version``
    uses; local files are read directly, remote hosts are probed over SSH.
    """
    names = repo_tracked_files(repo_home)
    repo_hashes = _repo_hashes(repo_home, names)
    local_hostname = hostname if hostname is not None else socket.gethostname()
    if _local_hostname_matches(local_hostname, host_id):
        live_home = home if home is not None else config_home()
        live_hashes = _local_live_hashes(live_home, names)
        row = {
            "host": host_id,
            "state": "ok",
            "detail": None,
            "files": _diff_from_hashes(repo_hashes, live_hashes, names),
        }
    else:
        row = probe_drift_host(host_id, repo_hashes, names, timeout=timeout, _run=_run)
    row["compared"] = len(names)
    row["differs"] = sum(1 for f in row["files"] if f["status"] == "differs")
    row["missing"] = sum(1 for f in row["files"] if f["status"] == "missing-live")
    return row


def cmd_drift(
    repo: str,
    hosts,
    *,
    home: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    as_json: bool = False,
    hostname: str | None = None,
    _run=subprocess.run,
) -> int:
    """Report per-host, per-file drift between repo snapshot and live home."""
    rows = [
        collect_host_drift(
            host_id,
            repo_operator_home(repo, host_id),
            home=home,
            hostname=hostname,
            timeout=timeout,
            _run=_run,
        )
        for host_id in hosts
    ]
    drifted = sum(1 for r in rows if r["state"] == "ok" and (r["differs"] or r["missing"]))
    unreachable = sum(1 for r in rows if r["state"] != "ok")
    report = {
        "repo": repo,
        "hosts": rows,
        "host_count": len(rows),
        "drifted": drifted,
        "unreachable": unreachable,
    }

    if as_json:
        print(json.dumps(report, sort_keys=True))
    else:
        print("%-24s %-13s %-9s %-8s %s" % ("HOST", "STATE", "COMPARED", "DIFFERS", "MISSING"))
        for row in rows:
            print(
                "%-24s %-13s %-9s %-8s %s"
                % (row["host"], row["state"], row["compared"], row["differs"], row["missing"])
            )
        for row in rows:
            for entry in row["files"]:
                if entry["status"] in ("differs", "missing-live"):
                    print("  %s: %s %s" % (row["host"], entry["status"], entry["path"]))
        print()
        print(
            "fleet drift: %d host(s), %d drifted, %d unreachable"
            % (len(rows), drifted, unreachable)
        )

    return 1 if drifted else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving fleet")
    actions = parser.add_subparsers(dest="action", required=True)
    version = actions.add_parser("version")
    version.add_argument(
        "--host",
        action="append",
        default=None,
        help="Repeatable declared host id; overrides topology-derived hosts.",
    )
    version.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    version.add_argument("--json", action="store_true", dest="json_out")

    drift = actions.add_parser("drift")
    drift.add_argument("--repo", required=True, help="Private operator repository root.")
    drift.add_argument(
        "--host",
        action="append",
        default=None,
        help="Repeatable host id; overrides repo-discovered hosts.",
    )
    drift.add_argument("--home", default=None, help="Override the local live operator home.")
    drift.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    drift.add_argument("--json", action="store_true", dest="json_out")
    return parser


def _fleet_version_result_from_args(args) -> CommandResult:
    if args.host:
        hosts = list(args.host)
        note = None
    else:
        hosts, note = _topology_remote_hosts()
        if hosts is None:
            message = (
                "no operator topology found and no --host given; pass --host NAME at least once"
            )
            error = UsageError(message, code="fleet_hosts_unavailable")
            return CommandResult(error=error, human_stderr=message + "\n")
    warnings = (note,) if note else ()
    return fleet_version_result(hosts, timeout=args.timeout, warnings=warnings)


def dispatch(argv=None):
    """Return typed results to the root dispatcher for supported Fleet paths."""
    args = _build_parser().parse_args(argv)
    if args.action == "version":
        return _fleet_version_result_from_args(args)
    return main(argv)


def main(argv=None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)

    if args.action == "drift":
        repo = os.path.abspath(os.path.expanduser(args.repo))
        if args.host:
            hosts = list(args.host)
            missing = [h for h in hosts if h not in discover_repo_hosts(repo)]
            if missing:
                print(
                    "no repository snapshot at hosts/%s/operator-home/ under %s"
                    % (missing[0], repo),
                    file=sys.stderr,
                )
                return 2
        else:
            hosts = discover_repo_hosts(repo)
            if not hosts:
                print(
                    "no host operator-home snapshots found under %s/hosts" % repo, file=sys.stderr
                )
                return 2
        return cmd_drift(repo, hosts, home=args.home, timeout=args.timeout, as_json=args.json_out)

    if args.action != "version":
        return 2

    if args.host:
        hosts = list(args.host)
    else:
        hosts, note = _topology_remote_hosts()
        if hosts is None:
            print(
                "no operator topology found and no --host given; pass --host NAME at least once",
                file=sys.stderr,
            )
            return 2
        if note:
            print(note, file=sys.stderr)

    return cmd_version(hosts, timeout=args.timeout, as_json=args.json_out)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
