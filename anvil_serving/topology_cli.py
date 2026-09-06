"""Focused CLI for offline topology inspection and execution-plan resolution."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys

from .commands import COMMAND_TREE, CommandNode
from .paths import default_topology_path, resolve_topology_path
from .targets import CommandSpec, resolve_execution_plan
from .topology import load_topology, load_topology_result
from .router.topology_validation import (
    DEPLOYMENT_IDENTITY_SOURCE,
    ERROR_ROUTER_CONFIG_INVALID,
    RUNTIME_DEPLOYMENT_IDENTITY_VERIFIED,
    SCHEMA_VERSION as ROUTER_VALIDATION_SCHEMA_VERSION,
    ReplicaTopologyValidationError,
    load_validated_router_snapshot,
)


class _ValidationArgumentError(ValueError):
    """A fixed parser failure for private router/topology path operands."""


class _ValidationParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise _ValidationArgumentError("invalid validation arguments")


class _SetOnce(argparse.Action):
    def __call__(self, parser, namespace, values, _option_string=None) -> None:
        if getattr(namespace, self.dest, None) is not None:
            parser.error("repeated validation argument")
        if type(values) is not str or not values:
            parser.error("invalid validation argument")
        setattr(namespace, self.dest, values)


def _validation_result(*, valid: bool, error_code: str | None = None, **counts: object) -> dict:
    return {
        "schema_version": ROUTER_VALIDATION_SCHEMA_VERSION,
        "valid": valid,
        "error_code": error_code,
        "config_sha256": counts.get("config_sha256"),
        "tier_count": counts.get("tier_count"),
        "replica_tier_count": counts.get("replica_tier_count"),
        "replica_member_count": counts.get("replica_member_count"),
        "deployment_identity_source": (
            DEPLOYMENT_IDENTITY_SOURCE if valid else None
        ),
        "runtime_deployment_identity_verified": (
            RUNTIME_DEPLOYMENT_IDENTITY_VERIFIED
        ),
    }


def _validation_parser() -> argparse.ArgumentParser:
    parser = _ValidationParser(
        prog="anvil-serving topology validate-router-config",
        description="Validate one router config against declared topology offline.",
        allow_abbrev=False,
    )
    parser.add_argument("--config", required=True, action=_SetOnce, metavar="PATH")
    parser.add_argument("--topology", action=_SetOnce, metavar="PATH")
    parser.add_argument("--topology-overlay", action=_SetOnce, metavar="PATH")
    return parser


def validate_router_config(argv: list[str]) -> dict:
    """Return the closed, input-free result for one exact snapshot validation."""
    try:
        args = _validation_parser().parse_args(argv)
    except _ValidationArgumentError:
        return _validation_result(valid=False, error_code=ERROR_ROUTER_CONFIG_INVALID)
    topology_arg = (
        default_topology_path() if args.topology is None else args.topology
    )
    try:
        snapshot = load_validated_router_snapshot(
            args.config,
            resolve_topology_path(topology_arg),
            (
                resolve_topology_path(args.topology_overlay)
                if args.topology_overlay is not None
                else None
            ),
        )
    except ReplicaTopologyValidationError as exc:
        return _validation_result(valid=False, error_code=exc.code)
    return snapshot.to_dict()


def validation_human(result: dict) -> str:
    """Render exactly one metadata-only line for the validation result."""
    if result.get("valid") is True:
        return (
            "router config topology valid: "
            f"tiers={result['tier_count']} replica_tiers={result['replica_tier_count']} "
            f"replica_members={result['replica_member_count']}\n"
        )
    return f"router config topology refused: {result.get('error_code')}\n"


def _command_node(command: str) -> tuple[tuple[CommandNode, ...], CommandNode]:
    parts = tuple(part for part in command.split() if part)
    if not parts:
        raise ValueError("--command must name a canonical command leaf")
    nodes = COMMAND_TREE.nodes
    path = []
    for part in parts:
        node = next((candidate for candidate in nodes if candidate.name == part), None)
        if node is None or not node.visible:
            raise ValueError("unknown canonical command %r" % command)
        path.append(node)
        nodes = node.children
    leaf = path[-1]
    if leaf.children or leaf.handler is None:
        raise ValueError("--command must name a canonical command leaf")
    return tuple(path), leaf


def _spec(path: tuple[CommandNode, ...], node: CommandNode) -> CommandSpec:
    return CommandSpec(
        name="-".join(item.name for item in path),
        resource_role=node.resource_role,
        supported_transports=node.transports,
        execution_runtime_roles=node.execution_runtime_roles,
        mutation_class=node.mutation_class,
        recovery_capable=node.recovery_capable,
        gpu_role_required=node.gpu_role_required,
        execution_host_os=node.execution_host_os,
        execution_policy=node.execution_policy,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving topology",
        description="Inspect, validate, or resolve a deployment topology without probing hosts.",
    )
    actions = parser.add_subparsers(dest="action", required=True)
    for action, help_text in (
        ("show", "show a validated topology summary"),
        ("validate", "validate topology and overlay files offline"),
        ("resolve", "resolve one canonical command without executing it"),
        ("drift", "compare the installed topology against a canonical fleet reference"),
    ):
        leaf = actions.add_parser(action, help=help_text)
        leaf.add_argument(
            "--topology",
            default=default_topology_path(),
            help=(
                "base topology TOML (default: "
                "$ANVIL_SERVING_HOME/operator-topology.toml)"
            ),
        )
        leaf.add_argument("--topology-overlay", help="partial deployment overlay TOML")
        if action == "drift":
            leaf.add_argument(
                "--canonical",
                required=True,
                help="canonical fleet topology TOML (the private-repo source of truth)",
            )
        if action == "resolve":
            leaf.add_argument("--command", required=True, help="canonical leaf, e.g. 'host status'")
            leaf.add_argument("--command-host")
            leaf.add_argument("--command-runtime")
            leaf.add_argument("--target")
            leaf.add_argument("--transport", choices=("auto", "local", "controller", "ssh"),
                              default="auto")
            leaf.add_argument("--experimental-model-workload", action="store_true")
    return parser


#: Per-host installed views may differ from the canonical fleet topology only
#: in their command identity and their own topology id (ADR-0033).
_DRIFT_ALLOWED_TOP_LEVEL = frozenset({"id", "command_host", "command_runtime"})


def _drift_report(installed, canonical) -> dict:
    """Metadata-only diff of two validated topologies (ADR-0033).

    Reports section/id/field names, never values, and never auto-fixes:
    cutover of a live host's installed file remains a reviewed operation.
    """
    differences: list[dict] = []
    sections = ("hosts", "runtimes", "gpu_roles", "resources", "transports", "capacity_policies")
    for section in sections:
        installed_by_id = {item.id: asdict(item) for item in getattr(installed, section)}
        canonical_by_id = {item.id: asdict(item) for item in getattr(canonical, section)}
        for item_id in sorted(set(canonical_by_id) - set(installed_by_id)):
            differences.append(
                {"section": section, "id": item_id, "kind": "missing_in_installed"}
            )
        for item_id in sorted(set(installed_by_id) - set(canonical_by_id)):
            differences.append(
                {"section": section, "id": item_id, "kind": "missing_in_canonical"}
            )
        for item_id in sorted(set(installed_by_id) & set(canonical_by_id)):
            fields = sorted(
                name
                for name, value in installed_by_id[item_id].items()
                if canonical_by_id[item_id].get(name) != value
            )
            if fields:
                differences.append(
                    {
                        "section": section,
                        "id": item_id,
                        "kind": "field_mismatch",
                        "fields": fields,
                    }
                )
    return {
        "in_sync": not differences,
        "differences": differences,
        "allowed_per_host_differences": sorted(_DRIFT_ALLOWED_TOP_LEVEL),
    }


def run(argv=None) -> dict:
    if argv and argv[0] == "validate-router-config":
        return validate_router_config(list(argv[1:]))
    args = _parser().parse_args(argv)
    topology_path = resolve_topology_path(args.topology)
    if args.action == "drift":
        installed = load_topology(topology_path, args.topology_overlay)
        canonical = load_topology(resolve_topology_path(args.canonical))
        report = _drift_report(installed, canonical)
        report.update(
            {
                "topology_path": topology_path,
                "canonical_path": resolve_topology_path(args.canonical),
                "installed_topology": installed.id,
                "canonical_topology": canonical.id,
            }
        )
        return report
    if args.action == "validate":
        result = load_topology_result(topology_path, args.topology_overlay)
        return {
            "valid": result.ok,
            "errors": [asdict(error) for error in result.errors],
            "topology": result.topology.id if result.topology else None,
            "topology_path": topology_path,
            "overlay": args.topology_overlay,
        }
    topology = load_topology(topology_path, args.topology_overlay)
    if args.action == "show":
        return {
            "topology": topology.id,
            "topology_path": topology_path,
            "schema_version": topology.schema_version,
            "overlay": args.topology_overlay,
            "hosts": [asdict(host) for host in topology.hosts],
            "runtimes": [asdict(runtime) for runtime in topology.runtimes],
            "resources": [asdict(resource) for resource in topology.resources],
            "transports": [
                {
                    "id": transport.id,
                    "kind": transport.kind,
                    "host": transport.host,
                    "runtime": transport.runtime,
                    "endpoint": transport.endpoint,
                    "auth_env": transport.auth_env,
                    "allowed_operations": list(transport.allowed_operations),
                }
                for transport in topology.transports
            ],
        }
    path, node = _command_node(args.command)
    plan = resolve_execution_plan(
        topology,
        _spec(path, node),
        target=args.target,
        transport=args.transport,
        command_host=args.command_host,
        command_runtime=args.command_runtime,
        overlay=args.topology_overlay,
        experimental_model_workload=args.experimental_model_workload,
    )
    result = plan.as_dict()
    result["resolved_command"] = result["command"]
    result["topology_path"] = topology_path
    return result


def main(argv=None) -> int:
    result = run(argv)
    if result.get("schema_version") == ROUTER_VALIDATION_SCHEMA_VERSION:
        print(validation_human(result), end="")
        return 0 if result["valid"] else 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get("valid", True):
        return 2
    if result.get("in_sync") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
