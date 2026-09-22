"""Thin local CLI boundary; library operations return structured metadata."""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys

from ..operator_output import CommandResult, OperatorError, PartialResultError, UsageError
from .config import ManifestError
from .command_specs import USER_OPERATIONS

_AUTHELIA_UPGRADE_HINT = "Authelia migration recovery is pending; rerun the identical connect up command with --upgrade --confirm"


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        # argparse normally echoes operands; paths or malformed input can carry
        # private data, so this boundary returns a fixed classified error.
        raise UsageError(f"Invalid Connect command arguments. Run {self.prog} --help for usage.", code="connect_arguments_invalid")


class _Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error("repeated argument")
        setattr(namespace, self.dest, values)


def _users_parser(actions) -> None:
    users = actions.add_parser(
        "users", allow_abbrev=False, help="Inspect accounts, invite users and manage access.",
        description="Local account administration. Start with resources to find exact grant IDs.",
        epilog="Use users COMMAND --help for examples and relevant options. Mutations preview unless --confirm is supplied.",
    )
    operations = users.add_subparsers(dest="operation", required=True, parser_class=_Parser)
    options = {
        "email": {"help": "Creation email for password setup and identity verification.", "required": True},
        "role": {"choices": ("member", "admin"), "help": "Authelia group only; defaults to member. Does not grant Connect operator access."},
        "grant": {"action": "append", "metavar": "RESOURCE:ROLE", "help": "Exact browser resource:member or resource:admin; repeat for each service. Discover IDs with resources."},
        "output": {"help": "Exclusive private handoff file (filesystem delivery only)."},
        "input": {"help": "Private authentication archive.", "required": True},
        "sha256": {"help": "Independently retained archive checksum.", "required": True},
        "destination": {"help": "Fresh private recovery directory; never activates accounts.", "required": True},
        "include-gateway": {"action": "store_true", "help": "Also retain gateway authority after the authentication snapshot."},
    }
    for operation, (summary, names) in USER_OPERATIONS.items():
        leaf = operations.add_parser(operation, allow_abbrev=False, help=summary, description=summary)
        leaf.set_defaults(username=None, manifest=None, email=None, role=None, grant=None,
                          output=None, input=None, sha256=None, destination=None, include_gateway=False)
        if operation not in {"list", "backup", "schedule", "deletion-schedule", "process-deletions", "restore"}:
            leaf.add_argument("username", help="Exact local username.")
        if operation != "restore":
            leaf.add_argument("--manifest", action=_Once, help="Defaults to /etc/anvil-connect/deployment.json.")
        for name in names:
            kwargs = {"action": _Once, **options[name]}
            if name == "grant" and operation == "access":
                kwargs["required"] = True
            leaf.add_argument("--" + name, **kwargs)
        if operation not in {"list", "show"}:
            leaf.add_argument("--dry-run", action="store_true", help="Preview only, even with --confirm.")
            leaf.add_argument("--confirm", action="store_true", help="Apply; omission previews the operation.")
        if operation in {"create", "access"}:
            example = "developer --email developer@example.test" if operation == "create" else "developer"
            leaf.epilog = f"Example (preview): {leaf.prog} {example} --grant SERVICE:member. Replace SERVICE with an ID from resources; add --confirm to apply."
        elif operation == "show":
            leaf.epilog = "Account enabled status does not prove Connect access. Inspect current grants through Manage access on the service home."


def _parser(prog: str = "anvil-serving connect") -> argparse.ArgumentParser:
    parser = _Parser(
        prog=prog, allow_abbrev=False,
        description="Anvil Connect: inspect services, invite users and manage the local access gateway.",
        epilog=f"Start here: {prog} resources | {prog} users list | {prog} users create --help. Changes preview unless --confirm is supplied.",
    )
    actions = parser.add_subparsers(dest="action", required=True, parser_class=_Parser)
    qualification = actions.add_parser("qualify", allow_abbrev=False, help="Run an isolated qualification lane.")
    qualify_mode = qualification.add_mutually_exclusive_group()
    qualify_mode.add_argument("--lane", choices=("baseline", "container-baseline", "device", "revocation", "isolation"), action=_Once)
    qualify_mode.add_argument("--prepare-container", action="store_true")
    qualify_mode.add_argument("--prepare-vm", action="store_true")
    qualification.add_argument("--config", action=_Once)
    _users_parser(actions)
    resources = actions.add_parser("resources", allow_abbrev=False,
                                   help="List declared browser services and copyable grant values.")
    resources.add_argument("--manifest", action=_Once, help="Defaults to /etc/anvil-connect/deployment.json.")
    for action in ("validate", "render", "up", "down", "status", "doctor", "logs", "init", "identity", "admin", "keygen", "backup", "restore", "migration", "edge-status", "edge-apply", "extend"):
        leaf = actions.add_parser(action, allow_abbrev=False, help={
            "validate": "Validate the deployment declaration.", "render": "Preview or stage configuration.",
            "up": "Preview or start selected services.", "down": "Preview or stop a selected service.",
            "status": "Inspect service state.", "doctor": "Check paths, ownership and components.",
            "logs": "Read bounded service events.", "init": "Initialize or enroll explicitly.",
            "identity": "Inspect a connector fingerprint.", "admin": "Send a local authority request.",
            "keygen": "Create a private local SDK key.", "backup": "Back up stopped gateway authority.",
            "restore": "Restore gateway authority into a fresh directory.", "migration": "Preview Observatory migration.",
            "edge-status": "Compare declared resources with the Cloudflare edge.",
            "edge-apply": "Apply declared Cloudflare edge changes.", "extend": "Extend an enrolled connector.",
        }[action])
        leaf.add_argument("--manifest", required=True, action=_Once)
        if action == "up":
            selected = leaf.add_mutually_exclusive_group(required=True)
            selected.add_argument("--service", action=_Once)
            selected.add_argument("--services", action=_Once)
            leaf.add_argument("--upgrade", action="store_true")
        elif action in {"edge-status", "edge-apply"}:
            leaf.add_argument(
                "--edge-config", action=_Once,
                help="Private edge-publishing configuration; defaults to the operator-home connect/edge-cloudflare.json.",
            )
        if action == "edge-apply":
            leaf.add_argument(
                "--retire-orphans", action="store_true",
                help="Retire DNS records and ingress rules for hosts the declaration withdrew; the default reports them and leaves them routed.",
            )
        if action not in {"render", "admin", "backup", "restore", "migration", "up", "edge-status", "edge-apply"}:
            leaf.add_argument("--service", required=action not in {"validate", "status", "doctor"}, action=_Once)
        if action in {"render", "up", "down", "init", "admin", "keygen", "backup", "restore", "edge-apply", "extend"}:
            leaf.add_argument("--dry-run", action="store_true")
            leaf.add_argument("--confirm", action="store_true")
        if action == "logs":
            leaf.add_argument("--tail", type=int, action=_Once)
        if action == "init":
            leaf.add_argument("--bundle", action=_Once)
        if action == "admin":
            leaf.add_argument("--request", required=True, action=_Once)
        if action in {"admin", "keygen", "backup"}:
            leaf.add_argument("--output", required=action in {"keygen", "backup"}, action=_Once)
        if action == "restore":
            for option in ("--input", "--destination", "--sha256", "--native-sha256"):
                leaf.add_argument(option, required=True, action=_Once)
        if action == "migration":
            leaf.add_argument("--observatory-config", required=True, action=_Once)
            leaf.add_argument("--resource", required=True, action=_Once)
    return parser


def _qualify(args: argparse.Namespace) -> CommandResult:
    from .qualification import QualificationError, qualify

    if args.prepare_container or args.prepare_vm:
        if args.prepare_vm:
            from .qualification_vm import prepare
            preparation_kind = "vm"
        else:
            from .qualification_container import prepare
            preparation_kind = "container"

        try:
            return CommandResult(data=prepare(args.config))
        except QualificationError as exc:
            return CommandResult(data={
                "schema": f"anvil-connect.qualification-{preparation_kind}/v1", "ok": False,
                "error_code": exc.code,
            }, error=OperatorError(f"Connect qualification {preparation_kind} preparation failed.", code=exc.code))
    expected_count = 2
    if args.lane in {"device", "revocation"}:
        from .qualification_container_run import _DEVICE_TESTS, _REVOCATION_TESTS
        expected_count = len(_DEVICE_TESTS if args.lane == "device" else _REVOCATION_TESTS)
    elif args.lane == "isolation":
        from .qualification_vm_run import _CASES
        expected_count = len(_CASES)

    def unavailable_counts() -> dict[str, int]:
        return {"passed": 0, "failed": 0, "skipped": 0, "not_run": 0, "unavailable": expected_count}

    try:
        if args.lane == "isolation":
            from .qualification_vm_run import qualify as isolation_qualify
            result = isolation_qualify(args.config)
        elif args.lane in {"container-baseline", "device", "revocation"}:
            from .qualification_container_run import qualify as container_qualify
            result = container_qualify(args.config, lane=args.lane)
        else:
            result = qualify(args.config, lane=args.lane or "baseline")
    except KeyboardInterrupt as exc:
        if args.lane != "isolation":
            raise
        started = bool(getattr(exc, "execution_started", True))
        counts = getattr(exc, "case_counts", None)
        if counts is None:
            counts = unavailable_counts() if started else {"passed": 0, "failed": 0, "skipped": 0, "not_run": expected_count}
        return CommandResult(data={
            "schema": "anvil-connect.qualification/v1", "ok": False,
            "state": "failed" if started else "not-run", "error_code": "runner-interrupted",
            "counts": counts, "stage": getattr(exc, "stage", "execution"),
        }, error=OperatorError(
            "Connect isolation qualification was interrupted.",
            code="runner-interrupted",
        ))
    except QualificationError as exc:
        started = bool(getattr(exc, "execution_started", False))
        state = "failed" if started else "not-run"
        counts = getattr(exc, "case_counts", None) if started else {"passed": 0, "failed": 0, "skipped": 0, "not_run": expected_count}
        if args.lane == "isolation" and started and counts is None:
            counts = unavailable_counts()
        return CommandResult(data={
            "schema": "anvil-connect.qualification/v1", "ok": False, "state": state,
            "error_code": exc.code, "counts": counts, "stage": getattr(exc, "stage", "preflight"),
        }, error=OperatorError(
            "Connect qualification failed during execution or cleanup." if started else "Connect qualification preflight failed; check the saved qualification settings and pinned prerequisites.",
            code=exc.code,
        ))
    if result.get("ok") is not True:
        return CommandResult(data=result, error=OperatorError(
            "Connect qualification failed; inspect the redacted result artifact.",
            code="connect_qualification_failed",
        ))
    return CommandResult(data=result)


def _legacy_user_options(argv: list[str]) -> list[str]:
    """Keep the former flat parser's options-before-operation spelling working."""
    if len(argv) < 2 or argv[0] != "users" or not argv[1].startswith("-"):
        return argv
    value_options = {"--manifest", "--email", "--role", "--grant", "--output", "--input", "--sha256", "--destination"}
    switches = {"--confirm", "--dry-run", "--include-gateway"}
    index = 1
    while index < len(argv) and argv[index].startswith("-"):
        flag, inline, _ = argv[index].partition("=")
        if flag in value_options:
            index += 1 if inline else 2
        elif flag in switches and not inline:
            index += 1
        else:
            return argv  # Help and malformed arguments stay with the closed parser.
    if index < len(argv) and argv[index] in USER_OPERATIONS:
        return ["users", argv[index], *argv[1:index], *argv[index + 1:]]
    return argv


def dispatch(argv: list[str] | None = None, *, prog: str = "anvil-serving connect") -> CommandResult:
    try:
        argv = _legacy_user_options(list(sys.argv[1:] if argv is None else argv))
        parser = _parser(prog)
        if not argv or argv == ["users"]:
            parser.parse_args([*argv, "--help"])
        args = parser.parse_args(argv)
        if args.action == "qualify":
            return _qualify(args)
        from . import manage  # help/importing the registry never starts discovery

        if not manage.supported_platform():
            return CommandResult(error=OperatorError(
                "Connect native operations require Linux amd64.",
                code="connect_platform_unsupported",
            ))
        target = manage.Target.parse(args.service) if getattr(args, "service", None) else None
        apply = bool(getattr(args, "confirm", False) and not getattr(args, "dry_run", False))
        action = args.action
        if action == "resources":
            from .inventory import resources
            from .users import DEFAULT_MANIFEST
            result = resources(args.manifest or DEFAULT_MANIFEST)
        elif action == "users":
            from .users import DEFAULT_MANIFEST, operate
            if args.operation in {"list", "show"}:
                from .inventory import accounts
                result = accounts(args.manifest or DEFAULT_MANIFEST, args.username)
            elif args.operation in {"schedule", "deletion-schedule", "process-deletions"}:
                if args.include_gateway or any((args.username, args.email, args.role, args.output, args.input, args.sha256, args.destination, args.grant)):
                    raise UsageError("Use users schedule --confirm to install the daily authentication backup timer.", code="connect_users_invalid")
                if args.operation == "process-deletions":
                    from .user_delete import process_pending
                    result = process_pending(args.manifest or DEFAULT_MANIFEST, apply=apply)
                else:
                    from .user_schedule import schedule
                    options = {"deletions": True} if args.operation == "deletion-schedule" else {}
                    result = schedule(args.manifest or DEFAULT_MANIFEST, apply=apply, **options)
            elif args.operation == "restore":
                if not args.input or not args.destination or not args.sha256 or args.include_gateway or any((args.username, args.manifest, args.email, args.role, args.output, args.grant)):
                    raise UsageError("Use users restore --input ARCHIVE --sha256 DIGEST --destination NEW_DIRECTORY.", code="connect_users_invalid")
                from .user_backup import restore
                result = restore(args.input, args.destination, sha256=args.sha256, apply=apply)
            else:
                if args.input or args.destination or args.sha256:
                    raise UsageError("--input, --sha256 and --destination are only accepted for restore.", code="connect_users_invalid")
                options = {"email": args.email, "role": args.role, "grants": args.grant,
                           "output": args.output, "apply": apply}
                if args.include_gateway:
                    options["include_gateway"] = True
                result = operate(args.manifest or DEFAULT_MANIFEST, args.operation, args.username, **options)
        elif action in {"validate", "status", "doctor"}:
            result = getattr(manage, action)(args.manifest, target=target)
        elif action == "render":
            result = manage.render(args.manifest, apply=apply)
        elif action == "up" and (args.services or args.upgrade):
            selections = args.services.split(",") if args.services else [args.service]
            if not selections or len(selections) > 129 or len(set(selections)) != len(selections) or any(not item for item in selections):
                raise UsageError("Select unique declared Connect services.", code="connect_arguments_invalid")
            targets = tuple(manage.Target.parse(value) for value in selections)
            result = manage.up_many(args.manifest, targets, upgrade=args.upgrade, apply=apply)
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
        elif action == "backup":
            from .recovery import backup
            result = backup(args.manifest, output_path=args.output, apply=apply)
        elif action == "restore":
            from .recovery import restore
            result = restore(args.manifest, input_path=args.input, destination=args.destination, sha256=args.sha256, native_sha256=args.native_sha256, apply=apply)
        elif action == "migration":
            from .migration import preview_observatory
            result = preview_observatory(args.manifest, args.observatory_config, resource_id=args.resource)
        elif action == "edge-status":
            from .edge import EdgeError, load_config, plan
            try:
                result = plan(load_config(getattr(args, "edge_config", None)), args.manifest)
            except EdgeError as exc:
                return CommandResult(error=OperatorError(str(exc), code="connect_edge_invalid"))
        elif action == "edge-apply":
            from .edge import EdgeError, apply as edge_apply, load_config
            try:
                result = edge_apply(
                    load_config(getattr(args, "edge_config", None)), args.manifest,
                    confirm=apply, retire_orphans=bool(getattr(args, "retire_orphans", False)),
                )
            except EdgeError as exc:
                return CommandResult(
                    error=OperatorError(str(exc), code="connect_edge_invalid"),
                    data={"applied": list(exc.applied), "failed_step": exc.failed_step} if exc.applied else None,
                )
        elif action == "extend":
            from .extend import ExtendError, extend as extend_connector
            try:
                result = extend_connector(args.manifest, target, confirm=apply)
            except ExtendError as exc:
                return CommandResult(
                    error=OperatorError(str(exc), code="connect_extend_invalid"),
                    data={"plan": exc.plan} if getattr(exc, "plan", None) else None,
                )
        else:
            result = manage.keygen(args.manifest, target, output_path=args.output, apply=apply)
        return CommandResult(data=result)
    except UsageError as exc:
        return CommandResult(error=exc)
    except ManifestError:
        return CommandResult(error=UsageError("Invalid Connect deployment declaration.", code="connect_manifest_invalid"))
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        partial = getattr(exc, "may_have_executed", False) is True
        error_type = PartialResultError if partial else OperatorError
        recovery = getattr(exc, "recovery", None)
        warning = ()
        if (isinstance(recovery, dict) and recovery.get("recovery_required") is True
                and recovery.get("recovery_hint") == _AUTHELIA_UPGRADE_HINT):
            warning = (_AUTHELIA_UPGRADE_HINT,)
        return CommandResult(data=recovery, warnings=warning, error=error_type(
            "Connect operation failed; inspect declared ownership and component status.",
            code="connect_operation_partial" if partial else "connect_operation_failed",
            details={"may_have_executed": partial},
        ))
