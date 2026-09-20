"""Short local operator commands for the optional Anvil Jev bridge."""

import argparse
import os
from pathlib import Path
import shutil
import tempfile

from . import guard, jev
from .benchmarking.artifacts import atomic_write_json
from .operator_output import CommandResult, SafetyError, UsageError


def main(argv=None):
    parser = argparse.ArgumentParser(prog="anvil-serving workbench jev")
    sub = parser.add_subparsers(dest="action", required=True)
    setup = sub.add_parser("setup", help="Record a trusted installed Anvil binary; leave Jev off.")
    setup.add_argument("--anvil-binary")
    sub.add_parser("status", help="Show policy without reading credentials or starting Anvil.")
    enable = sub.add_parser("enable", help="Enable only one named capability.")
    enable.add_argument("capability", choices=jev.CAPABILITIES)
    enable.add_argument("--allow-api", action="store_true")
    enable.add_argument("--allow-export", action="store_true")
    disable = sub.add_parser("disable")
    disable.add_argument("capability", choices=jev.CAPABILITIES, nargs="?")
    advise = sub.add_parser("advise")
    advise.add_argument("capability", choices=jev.CAPABILITIES)
    advise.add_argument("--input", required=True, type=Path)
    advise.add_argument("--allow-export", action="store_true")
    advise.add_argument("--no-jev", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            return CommandResult(data=jev.status())
        if args.action == "advise":
            policy = jev.load_policy()
            blocked = jev.gate(policy, args.capability, allow_export=args.allow_export, disabled=args.no_jev)
            if blocked:
                return CommandResult(data=blocked)
            if args.input.name.startswith(".env") or args.input.is_symlink() or not args.input.is_file():
                raise ValueError("Use selected JSON, never a secret file or symlink")
            raw = jev.read_regular(args.input, jev.MAX_INPUT)
            value = jev.validate_input(args.capability, jev.decode_json(raw))
            annotation = jev.advise(args.capability, value, allow_export=args.allow_export)
            return CommandResult(data=jev.advice_view(annotation, value))
        if not guard.confirmation_authorized():
            return CommandResult(error=SafetyError("Confirm the Jev policy change through anvil-serving with --confirm."))
        return _change(args)
    except (OSError, ValueError, TypeError, RecursionError):
        return CommandResult(error=UsageError("Jev policy or selected input is invalid or changed concurrently. Use setup, explicit API/export permission, and bounded JSON input; secrets remain in protected environment storage."))


def _change(args):
    from .service_runtime.operations import _lock
    path = jev.policy_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The shared owner lock is cross-process and nonblocking. Every supported
    # writer captures its source only after obtaining exclusive publication.
    with _lock(path):
        expected = jev.load_policy()
        policy = dict(expected)
        if args.action == "setup":
            binary = args.anvil_binary or shutil.which("anvil")
            if not binary or not Path(binary).is_absolute() or not Path(binary).is_file():
                raise ValueError("Install Anvil separately and select its absolute executable with --anvil-binary")
            policy["anvil_binary"] = str(Path(binary).absolute())
        elif args.action == "enable":
            if not args.allow_api or not args.allow_export:
                raise ValueError("Enabling cloud advice requires --allow-api and --allow-export")
            if not policy["anvil_binary"]:
                raise ValueError("Run workbench jev setup first")
            policy.update(enabled=True, allow_api=True, allow_export=True)
            policy["capabilities"] = sorted(set(policy["capabilities"]) | {args.capability})
        elif args.capability:
            policy["capabilities"] = [name for name in policy["capabilities"] if name != args.capability]
        else:
            policy.update(enabled=False, allow_api=False, allow_export=False)
        policy = jev.validate_policy(policy)
        if expected.raw is not None and dict(expected) == policy:
            return CommandResult(data=jev.status() | {"changed": False})
        _save(path, policy, expected)
        return CommandResult(data=jev.status() | {"changed": True})


def _save(path, policy, expected):
    descriptor, temporary = tempfile.mkstemp(prefix=".jev-policy-", dir=path.parent)
    os.close(descriptor)
    try:
        atomic_write_json(temporary, policy)
        if jev.load_policy() != expected:
            raise ValueError("Policy changed before publication")
        if expected.raw is not None:
            # Back up the safely opened snapshot, never reread a swapped path.
            descriptor = os.open(guard.next_backup(str(path)), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as backup:
                backup.write(expected.raw)
        if jev.load_policy() != expected:
            raise ValueError("Policy changed before publication")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
