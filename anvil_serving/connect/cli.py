"""Thin local CLI boundary; library operations return structured metadata."""

from __future__ import annotations

import argparse
import subprocess

from ..operator_output import CommandResult, OperatorError, PartialResultError, UsageError
from .config import ManifestError


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        # argparse normally echoes operands; paths or malformed input can carry
        # private data, so this boundary returns a fixed classified error.
        raise UsageError("Invalid Connect command arguments.", code="connect_arguments_invalid")


class _Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error("repeated argument")
        setattr(namespace, self.dest, values)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="anvil-serving connect", allow_abbrev=False)
    actions = parser.add_subparsers(dest="action", required=True, parser_class=_Parser)
    for action in ("validate", "render", "up", "down", "status", "doctor", "logs", "init", "identity", "admin", "keygen"):
        leaf = actions.add_parser(action, allow_abbrev=False)
        leaf.add_argument("--manifest", required=True, action=_Once)
        if action not in {"render", "admin"}:
            leaf.add_argument("--service", required=action not in {"validate", "status", "doctor"}, action=_Once)
        if action in {"render", "up", "down", "init", "admin", "keygen"}:
            leaf.add_argument("--dry-run", action="store_true")
            leaf.add_argument("--confirm", action="store_true")
        if action == "logs":
            leaf.add_argument("--tail", type=int, action=_Once)
        if action == "init":
            leaf.add_argument("--bundle", action=_Once)
        if action == "admin":
            leaf.add_argument("--request", required=True, action=_Once)
        if action in {"admin", "keygen"}:
            leaf.add_argument("--output", required=action == "keygen", action=_Once)
    return parser


def dispatch(argv: list[str] | None = None) -> CommandResult:
    try:
        args = _parser().parse_args(argv)
        from . import manage  # help/importing the registry never starts discovery

        target = manage.Target.parse(args.service) if getattr(args, "service", None) else None
        apply = bool(getattr(args, "confirm", False) and not getattr(args, "dry_run", False))
        action = args.action
        if action in {"validate", "status", "doctor"}:
            result = getattr(manage, action)(args.manifest, target=target)
        elif action == "render":
            result = manage.render(args.manifest, apply=apply)
        elif action in {"up", "down"}:
            result = getattr(manage, action)(args.manifest, target=target, apply=apply)
        elif action == "logs":
            tail = 200 if args.tail is None else args.tail
            if not 1 <= tail <= 200:
                raise UsageError("Tail must be between 1 and 200.", code="connect_arguments_invalid")
            result = manage.logs(args.manifest, target=target, tail=tail)
        elif action == "init":
            result = manage.native_init(args.manifest, target, bundle=args.bundle, apply=apply)
        elif action == "identity":
            result = manage.identity(args.manifest, target)
        elif action == "admin":
            result = manage.admin(args.manifest, request_path=args.request, output_path=args.output, apply=apply)
        else:
            result = manage.keygen(args.manifest, target, output_path=args.output, apply=apply)
        return CommandResult(data=result)
    except UsageError as exc:
        return CommandResult(error=exc)
    except ManifestError:
        return CommandResult(error=UsageError("Invalid Connect deployment declaration.", code="connect_manifest_invalid"))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        partial = getattr(exc, "may_have_executed", False) is True
        error_type = PartialResultError if partial else OperatorError
        return CommandResult(error=error_type(
            "Connect operation failed; inspect declared ownership and component status.",
            code="connect_operation_partial" if partial else "connect_operation_failed",
            details={"may_have_executed": partial},
        ))
