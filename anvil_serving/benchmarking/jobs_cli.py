"""CLI for durable context, agentic, and SWE benchmark jobs."""

from __future__ import annotations

import argparse
import json
import os
import sys

from ..guard import confirmation_authorized
from ..control_plane.controller.store import BenchmarkJobStore
from .jobs import BenchmarkJobError, validate_job_spec


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
        submit = actions.add_parser("submit")
        submit.add_argument("--spec-json", required=True)
        mode = submit.add_mutually_exclusive_group()
        mode.add_argument("--follow", action="store_true")
        mode.add_argument("--detach", action="store_true")
        submit.add_argument("--confirm", action="store_true", help=argparse.SUPPRESS)
        for action in ("status", "cancel", "artifact"):
            command = actions.add_parser(action)
            command.add_argument("--run-id", required=True)
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


def run(argv: list[str]) -> dict:
    args = _parser().parse_args(argv)
    store = _store()
    if args.action == "submit":
        if not (args.confirm or confirmation_authorized()):
            raise BenchmarkJobError("confirmation_required", "submit requires --confirm")
        disposition, job = store.submit(_spec_json(args.spec_json, args.suite))
        return {
            "disposition": disposition,
            "job": job,
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
        artifact = store.artifact(args.run_id)
        if artifact is None:
            raise BenchmarkJobError("artifact_pending", "benchmark artifact is not available")
        return artifact
    if not (args.confirm or confirmation_authorized()):
        raise BenchmarkJobError("confirmation_required", "cancel requires --confirm")
    return store.cancel(args.run_id)


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
