"""Bounded operator CLI for managed media workflows and jobs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .artifacts import ArtifactStore
from .comfyui import ComfyUIClient
from .errors import MediaError
from .jobs import MediaJobStore
from .operations import MediaOperations, parameters_from_json, stable_request_key
from .workflows import WorkflowRegistry


DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / "_media_workflows" / "registry.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving media")
    parser.add_argument("--registry", default=os.environ.get("ANVIL_MEDIA_WORKFLOW_REGISTRY", str(DEFAULT_REGISTRY)))
    parser.add_argument("--state-db", default=os.environ.get("ANVIL_MEDIA_STATE_DB", ""))
    parser.add_argument("--artifact-root", default=os.environ.get("ANVIL_MEDIA_ARTIFACT_ROOT", ""))
    sub = parser.add_subparsers(dest="family", required=True)
    capabilities = sub.add_parser("capabilities")
    _storage_options(capabilities)

    workflow = sub.add_parser("workflow")
    workflow_sub = workflow.add_subparsers(dest="action", required=True)
    workflow_list = workflow_sub.add_parser("list")
    _storage_options(workflow_list)
    show = workflow_sub.add_parser("show")
    _storage_options(show)
    _workflow_identity(show)
    validate = workflow_sub.add_parser("validate")
    _storage_options(validate)
    _workflow_identity(validate)
    validate.add_argument("--backend-url", required=True)
    run = workflow_sub.add_parser("run")
    _storage_options(run)
    _workflow_identity(run)
    run.add_argument("--parameters", required=True)
    run.add_argument("--principal", required=True)
    run.add_argument("--idempotency-key", default="")
    run.add_argument("--backend-url", required=True)

    job = sub.add_parser("job")
    job_sub = job.add_subparsers(dest="action", required=True)
    status = job_sub.add_parser("status")
    _storage_options(status)
    _job_identity(status)
    cancel = job_sub.add_parser("cancel")
    _storage_options(cancel)
    _job_identity(cancel)
    cancel.add_argument("--backend-url", required=True)

    artifact = sub.add_parser("artifact")
    artifact_sub = artifact.add_subparsers(dest="action", required=True)
    inspect = artifact_sub.add_parser("inspect")
    _storage_options(inspect)
    inspect.add_argument("artifact_id")
    inspect.add_argument("--principal", required=True)
    return parser


def _storage_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--registry", default=argparse.SUPPRESS)
    parser.add_argument("--state-db", default=argparse.SUPPRESS)
    parser.add_argument("--artifact-root", default=argparse.SUPPRESS)


def _workflow_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("workflow_id")
    parser.add_argument("--version", required=True)


def _job_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("job_id")
    parser.add_argument("--principal", required=True)


def _operations(args: argparse.Namespace) -> MediaOperations:
    state_path = Path(args.state_db or (Path.home() / ".anvil-serving" / "media-jobs.sqlite3"))
    artifact_root = Path(args.artifact_root or (Path.home() / ".anvil-serving" / "media-artifacts"))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    return MediaOperations(
        WorkflowRegistry(args.registry),
        MediaJobStore(state_path),
        ArtifactStore(artifact_root),
    )


def run(args: argparse.Namespace) -> dict:
    operations = _operations(args)
    if args.family == "capabilities":
        return operations.capabilities()
    if args.family == "workflow":
        if args.action == "list":
            return operations.workflow_list()
        if args.action == "show":
            return operations.workflow_show(args.workflow_id, args.version)
        backend = ComfyUIClient(args.backend_url)
        if args.action == "validate":
            return operations.workflow_validate(args.workflow_id, args.version, backend=backend)
        parameters = parameters_from_json(args.parameters)
        key = args.idempotency_key or stable_request_key(args.workflow_id, args.version, parameters)
        return operations.workflow_run(
            args.workflow_id,
            args.version,
            parameters,
            principal=args.principal,
            idempotency_key=key,
            backend=backend,
        )
    if args.family == "job":
        if args.action == "status":
            return operations.job_status(args.job_id, principal=args.principal)
        return operations.job_cancel(
            args.job_id,
            principal=args.principal,
            backend=ComfyUIClient(args.backend_url),
        )
    return operations.artifact_inspect(args.artifact_id, principal=args.principal)


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(_parser().parse_args(argv))
    except MediaError as exc:
        print(json.dumps(exc.as_dict(), sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
