"""Lifecycle wrapper for the optional, separate Anvil Workbench hub.

This module owns only Docker Compose lifecycle invocation. Workbench's API,
database schema, bridge, UI, and delivery logic live in the standalone
``anvil-workbench`` product and never load into the router process.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .guard import confirmation_authorized


DEFAULT_COMPOSE = Path(__file__).with_name("_scaffold_templates") / "docker-compose.workbench.yml"
DEFAULT_SOURCE = Path.home() / "ai-code" / "anvil-workbench"
DEFAULT_IMAGE = "anvil-workbench:local"
_MAX_LOG_TAIL = 5_000
DEFAULT_WAIT_TIMEOUT_SECONDS = 180


def _bounded_tail(value: str) -> int:
    """Parse a finite, operator-readable Compose log tail."""
    try:
        tail = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("tail must be an integer") from exc
    if tail < 1 or tail > _MAX_LOG_TAIL:
        raise argparse.ArgumentTypeError(
            f"tail must be between 1 and {_MAX_LOG_TAIL}"
        )
    return tail


def _bounded_wait_timeout(value: str) -> int:
    try:
        timeout = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("wait timeout must be an integer") from exc
    if not 1 <= timeout <= 600:
        raise argparse.ArgumentTypeError(
            "wait timeout must be between 1 and 600 seconds"
        )
    return timeout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving workbench", description="Manage the optional private Anvil Workbench hub.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    build = subparsers.add_parser("build")
    build.add_argument(
        "--source",
        type=Path,
        default=Path(os.environ.get("ANVIL_WORKBENCH_SOURCE", DEFAULT_SOURCE)),
        help="Anvil Workbench source checkout containing deploy/Dockerfile.hub.",
    )
    build.add_argument(
        "--image",
        default=os.environ.get("ANVIL_WORKBENCH_IMAGE", DEFAULT_IMAGE),
        help=f"Local image tag (default: {DEFAULT_IMAGE}).",
    )
    build.add_argument("--confirm", action="store_true", help="Confirm the local image build.")
    build.add_argument("--dry-run", action="store_true", help="Print the exact Docker build command without running it.")
    for name in ("up", "down", "status", "logs"):
        child = subparsers.add_parser(name)
        child.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE, help="Workbench Compose file.")
        child.add_argument("--env-file", type=Path, help="Compose environment file; secrets remain outside this command output.")
        child.add_argument("--project-name", default="anvil-workbench", help="Docker Compose project name.")
        if name in {"up", "down"}:
            child.add_argument("--confirm", action="store_true", help="Confirm the lifecycle mutation.")
            child.add_argument("--dry-run", action="store_true", help="Print the exact Compose command without running it.")
        if name == "up":
            child.add_argument(
                "--wait-timeout",
                type=_bounded_wait_timeout,
                default=DEFAULT_WAIT_TIMEOUT_SECONDS,
                metavar="SECONDS",
                help="Fail unless every service is running/healthy within 1 through 600 seconds.",
            )
        if name == "logs":
            child.add_argument(
                "--tail",
                type=_bounded_tail,
                default=200,
                help=f"Maximum log lines per service (1 through {_MAX_LOG_TAIL}).",
            )
            child.add_argument("--follow", action="store_true", help="Follow logs in the foreground.")
    return parser


def build_command(args: argparse.Namespace) -> list[str]:
    source = args.source.expanduser().resolve()
    dockerfile = source / "deploy" / "Dockerfile.hub"
    if not source.is_dir():
        raise ValueError(f"Workbench source directory does not exist: {source}")
    if not dockerfile.is_file():
        raise ValueError(f"Workbench hub Dockerfile does not exist: {dockerfile}")
    image = args.image.strip()
    if not image or any(char.isspace() for char in image):
        raise ValueError("Workbench image tag must be non-empty and contain no whitespace")
    return [
        "docker",
        "build",
        "--file",
        str(dockerfile),
        "--tag",
        image,
        str(source),
    ]


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
        command.extend([
            "up", "--detach", "--wait", "--wait-timeout", str(args.wait_timeout)
        ])
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
        command = build_command(args) if args.action == "build" else compose_command(args)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    if getattr(args, "dry_run", False):
        print(json.dumps({"ok": True, "dry_run": True, "command": command}))
        return 0
    # The canonical dispatcher strips ``--confirm`` before invoking a leaf, then
    # installs a thread-local authorization scope. Requiring that scope here
    # prevents ``python -m anvil_serving.workbench up`` or an accidental direct
    # handler call from bypassing the shared mutation gate.
    if args.action in {"build", "up", "down"} and not confirmation_authorized():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "confirmation required; invoke through anvil-serving workbench with --confirm",
                }
            ),
            file=sys.stderr,
        )
        return 3
    try:
        completed = subprocess.run(command, check=False, text=True)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"cannot start Docker Compose: {exc}"}), file=sys.stderr)
        return 1
    return int(completed.returncode)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
