"""CLI for durable context, agentic, and SWE benchmark jobs."""

from __future__ import annotations

import argparse
import json
import os
import sys

from ..guard import confirmation_authorized
from ..control_plane.controller.store import BenchmarkJobStore
from .harnesses import cleanup_harness_work, harness_asset_status, prepare_harness_assets
from .evidence_reader import read_referenced_job_evidence
from .jobs import BenchmarkJobError, validate_job_spec
from .preflight import run_benchmark_preflight
from .profiles import load_profile
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


def run(argv: list[str]) -> dict:
    args = _parser().parse_args(argv)
    store = _store()
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
    if record is not None and record["spec"]["suite"] != args.suite:
        raise BenchmarkJobError("suite_mismatch", "run belongs to a different suite")
    if args.action == "status":
        if record is None:
            raise BenchmarkJobError("job_not_found", "benchmark job does not exist")
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
