"""CLI for durable context, agentic, and SWE benchmark jobs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import uuid

from ..guard import confirmation_authorized
from ..control_plane.controller.store import BenchmarkJobStore, DEFAULT_BENCHMARK_RUN_ROOT
from .harnesses import (
    cleanup_harness_work,
    harness_asset_status,
    prepare_harness_assets,
    resolve_container_binary,
)
from .evidence_reader import read_referenced_job_evidence
from .jobs import (
    BenchmarkJobError,
    job_spec_sha256,
    resolve_owned_run_path,
    validate_job_spec,
)
from .preflight import run_benchmark_preflight
from .profiles import load_profile
from .swe import validate_swe_selection
from .swe_images import (
    prepare_paired_images,
    validate_paired_image_ids,
    validate_paired_image_repo_digests,
)
from .worker import cancel_benchmark_job, launch_benchmark_job


def _store() -> BenchmarkJobStore:
    path = os.environ.get("ANVIL_BENCHMARK_JOB_DB")
    run_root = os.environ.get("ANVIL_BENCHMARK_RUN_ROOT")
    kwargs = {"run_root": run_root} if run_root else {}
    return BenchmarkJobStore(path, **kwargs) if path else BenchmarkJobStore(**kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving eval benchmark")
    suites = parser.add_subparsers(dest="suite", required=True)
    for suite in ("context", "agentic", "swe"):
        suite_parser = suites.add_parser(suite)
        actions = suite_parser.add_subparsers(dest="action", required=True)
        plan = actions.add_parser("plan")
        plan.add_argument("--profile", choices=("smoke", "scout", "deep"), required=True)
        plan.add_argument("--observed-context", type=int)
        plan.add_argument("--dry-run", action="store_true")
        preflight = actions.add_parser("preflight")
        preflight.add_argument("--spec-json", required=True)
        preflight.add_argument("--requirements-json", default="{}")
        prepare = actions.add_parser("prepare")
        prepare.add_argument("--profile", choices=("smoke", "scout", "deep"), required=True)
        prepare.add_argument("--run-id", required=True)
        prepare.add_argument("--ownership-id", required=True)
        prepare.add_argument("--offline", action="store_true")
        prepare.add_argument("--max-download-bytes", type=int, default=20 * 1024**3)
        prepare.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
        assets = actions.add_parser("assets")
        assets.add_argument("--run-id", required=True)
        assets.add_argument("--ownership-id", required=True)
        if suite == "swe":
            images = actions.add_parser("images")
            images.add_argument("--spec", required=True)
            images.add_argument("--worker", required=True)
            images.add_argument("--min-free-bytes", type=int, default=100 * 1024**3)
            images.add_argument("--dry-run", action="store_true")
            images.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
        cleanup = actions.add_parser("cleanup")
        cleanup.add_argument("--run-id", required=True)
        cleanup.add_argument("--ownership-id", required=True)
        cleanup.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
        submit = actions.add_parser("submit")
        submit.add_argument("--spec-json", required=True)
        mode = submit.add_mutually_exclusive_group()
        mode.add_argument("--follow", action="store_true")
        mode.add_argument("--detach", action="store_true")
        submit.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
        for action in ("status", "cancel", "artifact"):
            command = actions.add_parser(action)
            command.add_argument("--run-id", required=True)
            if action == "artifact":
                command.add_argument("--path")
            if action == "cancel":
                command.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
        logs = actions.add_parser("logs")
        logs.add_argument("--run-id", required=True)
        logs.add_argument("--cursor", type=int, default=0)
        logs.add_argument("--limit", type=int, default=100)
        logs.add_argument("--follow", action="store_true")
    return parser


def _require_suite(value: dict, suite: str) -> dict:
    normalized = validate_job_spec(value)
    if normalized["suite"] != suite:
        raise BenchmarkJobError(
            "suite_mismatch",
            f"job specification suite must be {suite!r} for this command",
        )
    return normalized


def _spec_json(raw: str, suite: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BenchmarkJobError("bad_spec_json", "spec-json must be valid JSON") from exc
    return _require_suite(value, suite)


def _json_object(raw: str, *, field: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BenchmarkJobError("bad_json", f"{field} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise BenchmarkJobError("bad_json", f"{field} must be a JSON object")
    return value


def _swe_image_spec(path: str) -> dict:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("oversized")
        return _require_suite(json.loads(raw), "swe")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BenchmarkJobError(
            "bad_swe_image_spec",
            "SWE image specification is missing or invalid",
        ) from exc


def _bounded_command_output(handle, max_bytes: int = 64 * 1024) -> tuple[str, bool]:
    marker = b"\n...[bounded output omitted]...\n"
    size = handle.seek(0, os.SEEK_END)
    if size <= max_bytes:
        handle.seek(0)
        return handle.read().decode("utf-8", "replace"), False
    remaining = max_bytes - len(marker)
    head_bytes = remaining // 2
    tail_bytes = remaining - head_bytes
    handle.seek(0)
    head = handle.read(head_bytes)
    handle.seek(-tail_bytes, os.SEEK_END)
    tail = handle.read(tail_bytes)
    return (head + marker + tail).decode("utf-8", "replace"), True


def _image_runner(argv, cwd, timeout, env):
    """Run one Docker command without retaining unbounded output in memory."""
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        timed_out = False
        try:
            result = subprocess.run(
                list(argv),
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout,
                check=False,
            )
            returncode = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = None
        stdout, stdout_truncated = _bounded_command_output(stdout_file)
        stderr, stderr_truncated = _bounded_command_output(stderr_file)
    return SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        timed_out=timed_out,
    )


def _local_worker_identity(spec: dict, requested_worker: str) -> str:
    declared_worker = spec["worker"]["id"]
    if requested_worker != declared_worker:
        raise BenchmarkJobError(
            "swe_image_worker_mismatch",
            "--worker must match the frozen SWE job specification",
            {"requested": requested_worker, "declared": declared_worker},
        )
    hostname = socket.gethostname().strip()
    identities = {hostname.casefold(), hostname.split(".", 1)[0].casefold()}
    if requested_worker.casefold() not in identities:
        raise BenchmarkJobError(
            "swe_image_worker_mismatch",
            "SWE image preparation must run locally on the worker named by the frozen specification",
            {"requested": requested_worker, "observed_hostname": hostname},
        )
    return hostname


def run(argv: list[str]) -> dict:
    args = _parser().parse_args(argv)
    if args.action == "plan":
        profile = load_profile(args.profile, observed_context=args.observed_context)
        return {
            "schema": "anvil-serving.benchmark-plan/v1",
            "suite": args.suite,
            "profile": profile["name"],
            "profile_sha256": profile["content_sha256"],
            "configuration": profile["suites"][args.suite],
            "adapters": {
                name: profile["adapters"][name]
                for name in profile["suites"][args.suite]["adapters"]
            },
            "dry_run": bool(args.dry_run),
            "deferred": ["benchmark preflight", "model requests", "artifact write"],
        }
    if args.action == "images":
        spec = _swe_image_spec(args.spec)
        parameters = spec.get("parameters")
        if not isinstance(parameters, dict):
            raise BenchmarkJobError(
                "bad_swe_image_policy",
                "SWE image specification parameters must be an object",
            )
        selected = validate_swe_selection(
            load_profile(spec["profile"]), parameters.get("instance_ids")
        )
        expected = validate_paired_image_ids(parameters.get("paired_image_ids"), selected)
        if expected is None:
            raise BenchmarkJobError(
                "missing_paired_image_policy",
                "SWE image specification has no paired image IDs",
            )
        repo_digests = validate_paired_image_repo_digests(
            parameters.get("paired_image_repo_digests"), selected
        )
        observed_hostname = _local_worker_identity(spec, args.worker)
        confirm = bool(
            (args.confirm or confirmation_authorized()) and not args.dry_run
        )
        executable = resolve_container_binary()
        if confirm and not executable:
            raise BenchmarkJobError(
                "container_capability_absent",
                "Docker is required to prepare SWE task images",
            )
        run_root = os.environ.get("ANVIL_BENCHMARK_RUN_ROOT", DEFAULT_BENCHMARK_RUN_ROOT)
        spec_sha256 = job_spec_sha256(spec)
        root = Path(
            resolve_owned_run_path(
                run_root,
                ownership_id=spec["ownership_id"],
                run_id=spec["run_id"],
                relative=f"swe-images/{spec_sha256}",
            )
        )
        receipt_name = (
            f"receipt-{uuid.uuid4().hex}.json" if confirm else "receipt-preview.json"
        )
        return prepare_paired_images(
            expected,
            repo_digests,
            executable=executable or "docker",
            runner=_image_runner,
            cwd=str(root),
            env=dict(os.environ),
            receipt_path=str(root / receipt_name),
            lock_root=os.path.realpath(os.path.abspath(os.path.expanduser(run_root))),
            ownership_id=spec["ownership_id"],
            run_id=spec["run_id"],
            spec_sha256=spec_sha256,
            worker_id=spec["worker"]["id"],
            observed_hostname=observed_hostname,
            confirm=confirm,
            min_free_bytes=args.min_free_bytes,
        )
    store = _store()
    if args.action in {"prepare", "assets", "cleanup"}:
        if args.action == "assets":
            return harness_asset_status(
                run_root=store.run_root,
                ownership_id=args.ownership_id,
                run_id=args.run_id,
            )
        if not (args.confirm or confirmation_authorized()):
            raise BenchmarkJobError(
                "confirmation_required", f"harness {args.action} requires --confirm"
            )
        if args.action == "cleanup":
            return cleanup_harness_work(
                run_root=store.run_root,
                ownership_id=args.ownership_id,
                run_id=args.run_id,
            )
        cache_root = os.environ.get(
            "ANVIL_BENCHMARK_CACHE_ROOT",
            os.path.expanduser("~/.anvil-serving/benchmark-harness-cache"),
        )
        return prepare_harness_assets(
            load_profile(args.profile),
            suite=args.suite,
            run_root=store.run_root,
            ownership_id=args.ownership_id,
            run_id=args.run_id,
            cache_root=cache_root,
            offline=args.offline,
            max_download_bytes=args.max_download_bytes,
        )
    if args.action == "preflight":
        return run_benchmark_preflight(
            _spec_json(args.spec_json, args.suite),
            run_root=store.run_root,
            requirements=_json_object(args.requirements_json, field="requirements-json"),
            assets_root=os.environ.get("ANVIL_BENCHMARK_ASSETS_ROOT"),
        )
    if args.action == "submit":
        if not (args.confirm or confirmation_authorized()):
            raise BenchmarkJobError("confirmation_required", "submit requires --confirm")
        disposition, job = store.submit(_spec_json(args.spec_json, args.suite))
        launch = (
            launch_benchmark_job(
                path=store.path,
                run_root=store.run_root,
                run_id=job["spec"]["run_id"],
            )
            if job["state"] == "queued"
            else {"launched": False, "reason": f"job is {job['state']}"}
        )
        return {
            "disposition": disposition,
            "job": job,
            "worker": launch,
            "follow": bool(args.follow),
            "detached": bool(args.detach or not args.follow),
        }
    record = store.status(args.run_id)
    if record is None:
        raise BenchmarkJobError("job_not_found", "benchmark job does not exist")
    if record["spec"]["suite"] != args.suite:
        raise BenchmarkJobError("suite_mismatch", "run belongs to a different suite")
    if args.action == "status":
        return record
    if args.action == "logs":
        return store.logs(args.run_id, cursor=args.cursor, limit=args.limit)
    if args.action == "artifact":
        if args.path:
            return read_referenced_job_evidence(store, record, args.path)
        artifact = store.artifact(args.run_id)
        if artifact is None:
            raise BenchmarkJobError("artifact_pending", "benchmark artifact is not available")
        return artifact
    if not (args.confirm or confirmation_authorized()):
        raise BenchmarkJobError("confirmation_required", "cancel requires --confirm")
    return cancel_benchmark_job(store, args.run_id)


def main(argv=None) -> int:
    try:
        result = run(list(sys.argv[1:] if argv is None else argv))
    except BenchmarkJobError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": exc.message}}))
        return 2
    print(json.dumps({"ok": True, "data": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
