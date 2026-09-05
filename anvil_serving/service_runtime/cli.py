"""CLI projection for the declared portable service lifecycle."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .contracts import READ_ACTIONS, MUTATING_ACTIONS, ServiceError
from .operations import execute
from ..operator_output import CommandResult, UsageError, classify_error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and operate declared host services.")
    parser.add_argument("action", choices=READ_ACTIONS + MUTATING_ACTIONS)
    parser.add_argument("service", nargs="?")
    parser.add_argument("--manifest", help="Local services.toml override.")
    parser.add_argument("--topology")
    parser.add_argument("--topology-overlay")
    parser.add_argument("--command-host")
    parser.add_argument("--command-runtime")
    parser.add_argument("--target")
    parser.add_argument("--transport", choices=("auto", "local", "controller", "ssh"), default="local")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--tail", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--manager", choices=("launchd", "docker"))
    parser.add_argument("--service-label")
    parser.add_argument("--resource")
    parser.add_argument("--engine")
    parser.add_argument("--support", choices=("supported", "legacy"), default="supported")
    parser.add_argument("--container")
    parser.add_argument("--endpoint")
    parser.add_argument("--model")
    parser.add_argument("--health-path")
    parser.add_argument("--models-path")
    parser.add_argument("--feature")
    parser.add_argument("--startup-policy", choices=("always", "unless-stopped"))
    parser.add_argument("--memory-mib", type=int)
    parser.add_argument("--serve")
    parser.add_argument("--serve-manifest")
    return parser


def _binding(args: argparse.Namespace) -> dict | None:
    if args.action != "adopt":
        return None
    if not args.service or not args.manager or not args.resource or not args.engine:
        raise ServiceError(
            "bad_argument",
            "adopt requires SERVICE, --manager, --resource, and --engine",
        )
    binding = {
        "id": args.service,
        "resource": args.resource,
        "manager": args.manager,
        "engine": args.engine,
        "support": args.support,
    }
    if args.manager == "launchd":
        if not args.service_label:
            raise ServiceError("bad_argument", "launchd adoption requires --service-label")
        binding.update(
            {
                "label": args.service_label,
                "owner_uid": os.getuid(),
                "definition": str(
                    Path.home() / "Library" / "LaunchAgents" / (args.service_label + ".plist")
                ),
            }
        )
    else:
        if not args.container:
            raise ServiceError("bad_argument", "Docker adoption requires --container")
        binding["container"] = args.container
    for key in (
        "endpoint",
        "model",
        "health_path",
        "models_path",
        "feature",
        "startup_policy",
        "memory_mib",
        "serve",
        "serve_manifest",
    ):
        value = getattr(args, key)
        if value is not None:
            binding[key] = value
    return binding


def run(argv: list[str] | None = None) -> CommandResult:
    args = _parser().parse_args(argv)
    if not 1 <= args.tail <= 1000:
        return CommandResult(error=UsageError("--tail must be between 1 and 1000", code="bad_argument"))
    if not 1 <= args.timeout_seconds <= 7200:
        return CommandResult(error=UsageError("--timeout-seconds must be between 1 and 7200", code="bad_argument"))
    try:
        result = execute(
            args.action,
            args.service,
            manifest=args.manifest,
            topology=args.topology,
            topology_overlay=args.topology_overlay,
            command_host=args.command_host,
            command_runtime=args.command_runtime,
            target=args.target,
            transport=args.transport,
            dry_run=args.dry_run,
            confirm=args.confirm,
            tail=args.tail,
            timeout_seconds=args.timeout_seconds,
            binding=_binding(args),
            remote=False,
        )
    except ServiceError as exc:
        return CommandResult(error=classify_error(exc))
    return CommandResult(data=result)


def main(argv: list[str] | None = None) -> int:
    """Standalone module projection; the public dispatcher consumes run()."""
    result = run(argv)
    if result.error:
        _print_error(result.error.code, str(result.error), result.error.details)
        return 2
    print(json.dumps(result.data, sort_keys=True))
    return 0


def _print_error(code: str, message: str, details=None) -> None:
    result = {"code": code, "message": message}
    if details:
        result["details"] = details
    print(json.dumps(result, sort_keys=True), file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
