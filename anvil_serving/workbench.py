"""Managed Pi runner setup and lifecycle of the optional companion hub.

The integrated portal lives in ``workbench_app`` and the Observatory facade,
separate from the inference router process. The standalone companion hub is
an optional integration with its own Compose lifecycle.
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
PI_IMAGE = "anvil-pi-runner:0.85.1"
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
    parser = argparse.ArgumentParser(prog="anvil-serving workbench", description="Manage Pi runner setup and the optional private companion hub.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--runner", choices=("hub", "pi"), default="hub", help="Build the companion hub or packaged isolated Pi runner.")
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
    egress = subparsers.add_parser("pi-egress", help="Set up and verify an exact-provider Pi egress proxy.")
    egress.add_argument("--config", type=Path, required=True, help="Private Workbench JSON configuration.")
    egress.add_argument("--provider", required=True, help="Declared Pi provider identity.")
    egress.add_argument("--confirm", action="store_true")
    egress.add_argument("--remove", action="store_true", help="Remove the proven proxy/network after all Pi runners stop.")
    egress.add_argument("--dry-run", action="store_true")
    storage = subparsers.add_parser("pi-storage", help="Provision the fixed, bounded Pi runner storage pool.")
    storage.add_argument("--config", type=Path, required=True, help="Absolute private Workbench JSON configuration.")
    storage.add_argument("--confirm", action="store_true", help="Confirm creation and systemd installation of the configured pool.")
    storage.add_argument("--dry-run", action="store_true", help="Print the exact bounded storage plan without changing the host.")
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
    piweb = subparsers.add_parser(
        "pi-web",
        help="Manage the pinned Pi Web session UI service on loopback.",
    )
    piweb_sub = piweb.add_subparsers(dest="pi_web_action", required=True)
    for name, description in (
        ("install", "Install the pinned Pi Web release and its reviewed systemd unit."),
        ("up", "Start the managed Pi Web service."),
        ("down", "Stop the managed Pi Web service."),
        ("status", "Show the managed Pi Web service state and loopback readiness."),
        ("logs", "Read bounded managed Pi Web service journal logs."),
    ):
        child = piweb_sub.add_parser(name, help=description)
        child.add_argument(
            "--config",
            type=Path,
            help="Private Pi Web configuration; defaults to the operator-home workbench/pi-web.json.",
        )
        if name in {"install", "up", "down"}:
            child.add_argument("--confirm", action="store_true", help="Confirm the guarded mutation.")
            child.add_argument("--dry-run", action="store_true", help="Preview the exact commands without changing state.")
        if name == "logs":
            child.add_argument("--tail", type=_bounded_tail, default=200, help=f"Maximum journal lines (1 through {_MAX_LOG_TAIL}).")
    return parser


def build_command(args: argparse.Namespace) -> list[str]:
    pi = args.runner == "pi"
    source = Path(__file__).with_name("_pi_runner") if pi else args.source.expanduser().resolve()
    dockerfile = source / "Dockerfile" if pi else source / "deploy" / "Dockerfile.hub"
    if not source.is_dir():
        raise ValueError(f"Workbench source directory does not exist: {source}")
    if not dockerfile.is_file():
        raise ValueError(f"Workbench hub Dockerfile does not exist: {dockerfile}")
    image = PI_IMAGE if pi and args.image == DEFAULT_IMAGE else args.image.strip()
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


def _pi_web_main(args: argparse.Namespace) -> int:
    """Dispatch one managed Pi Web action; library operations return, CLI prints."""
    from .workbench_app import pi_web

    action = args.pi_web_action
    try:
        config = pi_web.load_config(getattr(args, "config", None), required=action == "install")
        if action == "install":
            confirmed = confirmation_authorized() and not args.dry_run
            if args.confirm and not args.dry_run and not confirmed:
                print(
                    json.dumps({"ok": False, "error": "confirmation required; invoke through anvil-serving workbench with --confirm"}),
                    file=sys.stderr,
                )
                return 3
            result = pi_web.PiWebInstaller(config).install(confirm=confirmed)
            print(json.dumps({"ok": True, **result}))
            return 0
        if action in {"up", "down"}:
            if args.dry_run:
                verb = "start" if action == "up" else "stop"
                print(json.dumps({"ok": True, "dry_run": True, "command": ["systemctl", verb, pi_web.UNIT_NAME]}))
                return 0
            if not confirmation_authorized():
                print(
                    json.dumps({"ok": False, "error": "confirmation required; invoke through anvil-serving workbench with --confirm"}),
                    file=sys.stderr,
                )
                return 3
            if action == "up":
                pi_web.start_service()
            else:
                pi_web.stop_service()
            print(json.dumps({"ok": True, "action": action, "unit": pi_web.UNIT_NAME}))
            return 0
        if action == "status":
            print(json.dumps({"ok": True, **pi_web.status(config)}))
            return 0
        output = pi_web.logs(config, tail=args.tail)
        print(output, end="" if output.endswith("\n") else "\n")
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "pi-web":
        return _pi_web_main(args)
    if args.action == "pi-storage":
        from .workbench_app.config import validate_config
        from .workbench_app.pi_storage import PiStorageError, PiStorageManager
        try:
            if not args.config.is_absolute():
                raise PiStorageError("--config must be an absolute private path")
            config = json.loads(args.config.read_text(encoding="utf-8"))
            config = config.get("workbench", config)
            config = validate_config(config)
            confirmed = confirmation_authorized() and not args.dry_run
            if args.confirm and not args.dry_run and not confirmed:
                print(json.dumps({"ok": False, "error": "confirmation required; invoke through anvil-serving workbench with --confirm"}), file=sys.stderr)
                return 3
            result = PiStorageManager(config).provision(confirm=confirmed)
            print(json.dumps({"ok": True, **result}))
            return 0
        except (ValueError, OSError, KeyError, PiStorageError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 2
    if args.action == "pi-egress":
        from .workbench_app.config import validate_config
        from .workbench_app.pi_egress import PiEgress, PiEgressError
        try:
            config = json.loads(args.config.read_text())
            config = config.get("workbench", config)
            if "state_root" in config and "state_path" not in config:
                config = {"state_path": str(Path(config["state_root"]).parent / "workbench.sqlite"), "pi": config}
            config = validate_config(config)
            confirmed = confirmation_authorized() and not args.dry_run
            operation = PiEgress(config["pi"]).remove if args.remove else PiEgress(config["pi"]).setup
            result = operation(args.provider, confirm=confirmed)
            print(json.dumps({"ok": True, **result}))
            return 0
        except (ValueError, OSError, KeyError, PiEgressError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 2
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
