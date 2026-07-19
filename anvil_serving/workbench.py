"""Lifecycle wrapper for the optional, separate Anvil Workbench hub.

This module owns only Docker Compose lifecycle invocation. Workbench's API,
database schema, bridge, UI, and delivery logic live in the standalone
``anvil-workbench`` product and never load into the router process.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence


DEFAULT_COMPOSE = Path(__file__).with_name("_scaffold_templates") / "docker-compose.workbench.yml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving workbench", description="Manage the optional private Anvil Workbench hub.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for name in ("up", "down", "status", "logs"):
        child = subparsers.add_parser(name)
        child.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE, help="Workbench Compose file.")
        child.add_argument("--env-file", type=Path, help="Compose environment file; secrets remain outside this command output.")
        child.add_argument("--project-name", default="anvil-workbench", help="Docker Compose project name.")
        if name in {"up", "down"}:
            child.add_argument("--confirm", action="store_true", help="Confirm the lifecycle mutation.")
            child.add_argument("--dry-run", action="store_true", help="Print the exact Compose command without running it.")
        if name == "logs":
            child.add_argument("--tail", type=int, default=200, help="Maximum log lines per service.")
            child.add_argument("--follow", action="store_true", help="Follow logs in the foreground.")
    return parser


def compose_command(args: argparse.Namespace) -> list[str]:
    compose = args.compose.expanduser().resolve()
    if not compose.is_file():
        raise ValueError(f"Compose file does not exist: {compose}")
    command = ["docker", "compose", "--project-name", args.project_name]
    if args.env_file is not None:
        env_file = args.env_file.expanduser().resolve()
        if not env_file.is_file():
            raise ValueError(f"environment file does not exist: {env_file}")
        command.extend(["--env-file", str(env_file)])
    command.extend(["-f", str(compose)])
    if args.action == "up":
        command.extend(["up", "--detach"])
    elif args.action == "down":
        command.append("down")
    elif args.action == "status":
        command.extend(["ps", "--format", "json"])
    else:
        command.extend(["logs", "--tail", str(args.tail)])
        if args.follow:
            command.append("--follow")
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        command = compose_command(args)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    # The canonical ``anvil-serving`` dispatcher consumes --confirm before it
    # invokes a leaf handler, after enforcing the human gate. Keeping the flag
    # in this parser makes direct help clear; lifecycle authorization lives in
    # the shared dispatcher like every other mutable CLI verb.
    if getattr(args, "dry_run", False):
        print(json.dumps({"ok": True, "dry_run": True, "command": command}))
        return 0
    try:
        completed = subprocess.run(command, check=False, text=True)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"cannot start Docker Compose: {exc}"}), file=sys.stderr)
        return 1
    return int(completed.returncode)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
