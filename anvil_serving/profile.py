"""`anvil-serving eval usage` - turn your Claude Code logs into a usage baseline + sizing inputs."""
import os
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from . import config
HERE = os.path.dirname(__file__)


def _staging_path(directory, label):
    fd, path = tempfile.mkstemp(
        prefix=".%s." % label, suffix=".tmp", dir=directory
    )
    os.close(fd)
    os.unlink(path)
    return path


def _valid_json_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("analysis output must be a JSON object: %s" % path)


def _commit_pair(staged_agg, agg, staged_rs, rs):
    """Replace the usage artifact pair, rolling back if the second replace fails."""
    backups = []
    installed = []
    try:
        for target in (agg, rs):
            if os.path.exists(target):
                backup = _staging_path(
                    os.path.dirname(target), os.path.basename(target) + ".bak"
                )
                shutil.copy2(target, backup)
            else:
                backup = None
            backups.append(backup)
        try:
            for staged, target in ((staged_agg, agg), (staged_rs, rs)):
                os.replace(staged, target)
                installed.append(target)
        except OSError:
            rollback_errors = []
            for target, backup in zip((agg, rs), backups):
                try:
                    if backup:
                        os.replace(backup, target)
                    elif target in installed:
                        os.unlink(target)
                except OSError as exc:
                    rollback_errors.append("%s: %s" % (target, exc))
            if rollback_errors:
                raise OSError(
                    "commit failed and rollback was incomplete: %s"
                    % "; ".join(rollback_errors)
                )
            raise
    finally:
        for backup in backups:
            if backup:
                try:
                    os.unlink(backup)
                except FileNotFoundError:
                    pass

def main(argv):
    ap = argparse.ArgumentParser(
        prog="anvil-serving eval usage",
        description=(
            "Summarize Claude Code logs into local-serving usage and role artifacts.\n\n"
            "Examples:\n"
            "  anvil-serving eval usage --out-dir .anvil/usage --dry-run\n"
            "  anvil-serving eval usage --logs-dir ~/.claude/projects "
            "--out-dir .anvil/usage --confirm"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--logs-dir", default=config.claude_logs_dir(),
                    help="Claude Code session logs (default: %(default)s; env ANVIL_CLAUDE_LOGS)")
    ap.add_argument("--out-dir", default=os.getcwd(),
                    help="existing output directory (default: invocation directory)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print resolved inputs and outputs without reading logs or writing")
    ap.add_argument("--analysis-timeout", type=float, default=300.0,
                    help="timeout for each analysis child process, 1..3600 seconds")
    a = ap.parse_args(argv)
    logs_dir = os.path.abspath(os.path.expanduser(a.logs_dir))
    out_dir = os.path.abspath(os.path.expanduser(a.out_dir))
    if not os.path.isdir(logs_dir):
        ap.error("logs directory does not exist: %s" % logs_dir)
    if not os.path.isdir(out_dir):
        ap.error("output directory does not exist: %s" % out_dir)
    if not 1 <= a.analysis_timeout <= 3600:
        ap.error("--analysis-timeout must be from 1 through 3600 seconds")
    agg = os.path.join(out_dir, "usage_aggregate.json")
    rs = os.path.join(out_dir, "role_split.json")
    if a.dry_run:
        print("usage analysis plan")
        print("  logs: %s" % logs_dir)
        print("  outputs: %s, %s" % (agg, rs))
        print("  deferred: log scan, artifact writes")
        return 0
    env = dict(os.environ, ANVIL_CLAUDE_LOGS=logs_dir)
    staged_agg = _staging_path(out_dir, "usage_aggregate.json")
    staged_rs = _staging_path(out_dir, "role_split.json")
    try:
        try:
            aggregate_rc = subprocess.call(
                [sys.executable, os.path.join(HERE, "_aggregate_usage.py"),
                 "--out", staged_agg], env=env, timeout=a.analysis_timeout
            )
            role_rc = subprocess.call(
                [sys.executable, os.path.join(HERE, "_role_split.py"),
                 "--out", staged_rs], env=env, timeout=a.analysis_timeout
            )
        except subprocess.TimeoutExpired:
            print(
                "anvil-serving eval usage: analysis timed out after %.1fs; "
                "existing outputs were preserved" % a.analysis_timeout,
                file=sys.stderr,
            )
            return 1
        if aggregate_rc or role_rc:
            print(
                "anvil-serving eval usage: analysis failed "
                "(aggregate=%d, role-split=%d); existing outputs were preserved"
                % (aggregate_rc, role_rc),
                file=sys.stderr,
            )
            return 1
        try:
            _valid_json_file(staged_agg)
            _valid_json_file(staged_rs)
            _commit_pair(staged_agg, agg, staged_rs, rs)
            staged_agg = None
            staged_rs = None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                "anvil-serving eval usage: cannot commit analysis outputs: %s" % exc,
                file=sys.stderr,
            )
            return 1
    finally:
        for staged in (staged_agg, staged_rs):
            if staged:
                try:
                    os.unlink(staged)
                except FileNotFoundError:
                    pass
    print("wrote %s and %s" % (agg, rs))
    print("Next: review docs/cli/eval.md#usage-analysis for sizing guidance.")
    return 0
