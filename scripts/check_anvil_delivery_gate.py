"""Fail closed unless Anvil tasks and PR-bound delivery evidence are complete."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable, Mapping, Sequence


SCHEMA_VERSION = "anvil-delivery/v1"
FINAL_REVIEW_KINDS = ("documentation", "adversarial")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ANVIL_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTIC_CHARS = 4096


class DeliveryGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class GateResult:
    task_ids: tuple[str, ...]
    prd_ids: tuple[str, ...]
    commands_run: tuple[tuple[str, ...], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "task_ids": list(self.task_ids),
            "prd_ids": list(self.prd_ids),
            "commands_run": [list(command) for command in self.commands_run],
        }


def _run(
    argv: Sequence[str], *, cwd: Path, environment: Mapping[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            stdout=stdout,
            stderr=stderr,
            check=False,
            timeout=timeout,
            shell=False,
        )
        stdout.seek(0)
        stderr.seek(0)
        stdout_bytes = stdout.read(MAX_ANVIL_OUTPUT_BYTES + 1)
        stderr_bytes = stderr.read(MAX_ANVIL_OUTPUT_BYTES + 1)
    if len(stdout_bytes) > MAX_ANVIL_OUTPUT_BYTES or len(stderr_bytes) > MAX_ANVIL_OUTPUT_BYTES:
        raise DeliveryGateError("Anvil command output exceeded the delivery gate size limit")
    try:
        stdout_text = stdout_bytes.decode("utf-8")
        stderr_text = stderr_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeliveryGateError("Anvil command output was not valid UTF-8") from exc
    return subprocess.CompletedProcess(completed.args, completed.returncode, stdout_text, stderr_text)


def _invoke(
    prefix: Sequence[str],
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: int,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    commands_run: list[tuple[str, ...]],
) -> dict[str, object]:
    command = (*prefix, *arguments)
    commands_run.append(command)
    completed = runner(command, cwd=cwd, environment=environment, timeout=timeout)
    if not isinstance(completed.stdout, str) or not isinstance(completed.stderr, str):
        raise DeliveryGateError("Anvil command runner must return text output")
    if (
        len(completed.stdout.encode("utf-8")) > MAX_ANVIL_OUTPUT_BYTES
        or len(completed.stderr.encode("utf-8")) > MAX_ANVIL_OUTPUT_BYTES
    ):
        raise DeliveryGateError("Anvil command output exceeded the delivery gate size limit")
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()[:MAX_DIAGNOSTIC_CHARS]
        raise DeliveryGateError(f"Anvil command failed ({completed.returncode}): {message}")
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DeliveryGateError("Anvil command returned malformed JSON") from exc
    if not isinstance(envelope, dict) or envelope.get("ok") is not True:
        raise DeliveryGateError("Anvil command returned an unsuccessful JSON envelope")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise DeliveryGateError("Anvil command JSON is missing an object data field")
    return data


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeliveryGateError(f"{label} is required")
    return value.strip()


def _timestamp(value: object, label: str) -> str:
    timestamp = _required_string(value, label)
    normalized = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DeliveryGateError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DeliveryGateError(f"{label} must include a timezone")
    return timestamp


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_MANIFEST_BYTES + 1)
        if len(payload) > MAX_MANIFEST_BYTES:
            raise DeliveryGateError("delivery manifest exceeds the size limit")
        value = json.loads(payload.decode("utf-8"))
    except DeliveryGateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeliveryGateError(f"delivery manifest could not be read: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise DeliveryGateError(f"delivery manifest must use schema_version {SCHEMA_VERSION}")
    return value


def _task_entries(manifest: Mapping[str, object]) -> dict[str, dict[str, object]]:
    raw = manifest.get("tasks")
    if not isinstance(raw, list) or not raw:
        raise DeliveryGateError("delivery manifest requires a non-empty tasks array")
    entries: dict[str, dict[str, object]] = {}
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise DeliveryGateError(f"tasks[{index}] must be an object")
        task_id = _required_string(value.get("id"), f"tasks[{index}].id")
        if task_id in entries:
            raise DeliveryGateError(f"duplicate delivery task: {task_id}")
        entries[task_id] = value
    return entries


def _validate_final_reviews(manifest: Mapping[str, object], author: str) -> None:
    reviews = manifest.get("final_reviews")
    if not isinstance(reviews, dict):
        raise DeliveryGateError("final_reviews must be an object")
    for kind in FINAL_REVIEW_KINDS:
        review = reviews.get(kind)
        if not isinstance(review, dict):
            raise DeliveryGateError(f"final_reviews.{kind} is required")
        if review.get("disposition") != "passed":
            raise DeliveryGateError(f"final_reviews.{kind}.disposition must be passed")
        reviewer = _required_string(review.get("reviewer"), f"final_reviews.{kind}.reviewer")
        if reviewer.casefold() == author.casefold():
            raise DeliveryGateError(f"final {kind} reviewer must differ from the author")
        _timestamp(review.get("observed_at"), f"final_reviews.{kind}.observed_at")
        _required_string(review.get("summary"), f"final_reviews.{kind}.summary")


def _observed_proof_key(value: Mapping[str, object]) -> tuple[str, str]:
    kind = _required_string(value.get("kind"), "proof.kind")
    if kind == "command":
        return kind, _required_string(value.get("command"), "proof.command")
    if kind == "link":
        return kind, _required_string(value.get("url"), "proof.url")
    raise DeliveryGateError(f"unsupported proof kind: {kind}")


def _required_proof_match(
    required: Mapping[str, object], observed: Mapping[tuple[str, str], Mapping[str, object]]
) -> tuple[tuple[str, str], Mapping[str, object]]:
    kind = _required_string(required.get("kind"), "proof.kind")
    if kind == "command":
        command = _required_string(required.get("command"), "proof.command")
        key = (kind, command)
        proof = observed.get(key)
        if proof is None:
            raise DeliveryGateError(f"missing observed proof: {command}")
        return key, proof
    if kind != "link":
        raise DeliveryGateError(f"unsupported proof kind: {kind}")

    exact = required.get("url")
    contains = required.get("link_contains")
    if exact is not None:
        expected = _required_string(exact, "proof.url")
        candidates = [(key, proof) for key, proof in observed.items() if key == (kind, expected)]
    else:
        fragment = _required_string(contains, "proof.link_contains")
        candidates = [
            (key, proof)
            for key, proof in observed.items()
            if key[0] == kind and fragment in key[1]
        ]
        expected = fragment
    if not candidates:
        raise DeliveryGateError(f"missing observed proof: {expected}")
    if len(candidates) != 1:
        raise DeliveryGateError(f"ambiguous observed link proof: {expected}")
    return candidates[0]


def _validate_proofs(task: Mapping[str, object], entry: Mapping[str, object]) -> None:
    verification = task.get("verification")
    if not isinstance(verification, dict):
        raise DeliveryGateError(f"task {task.get('id')} has no verification contract")
    required = verification.get("required_proofs")
    if not isinstance(required, list):
        raise DeliveryGateError(f"task {task.get('id')} required_proofs is malformed")
    observed = entry.get("proofs")
    if not isinstance(observed, list):
        raise DeliveryGateError(f"task {task.get('id')} proofs must be an array")
    observed_by_key: dict[tuple[str, str], Mapping[str, object]] = {}
    for value in observed:
        if not isinstance(value, dict):
            raise DeliveryGateError(f"task {task.get('id')} contains a malformed proof")
        key = _observed_proof_key(value)
        if key in observed_by_key:
            raise DeliveryGateError(f"task {task.get('id')} contains duplicate proof {key[1]}")
        observed_by_key[key] = value

    for required_proof in required:
        if not isinstance(required_proof, dict):
            raise DeliveryGateError(f"task {task.get('id')} has a malformed required proof")
        try:
            key, proof = _required_proof_match(required_proof, observed_by_key)
        except DeliveryGateError as exc:
            raise DeliveryGateError(f"task {task.get('id')} is {exc}") from exc
        _timestamp(proof.get("observed_at"), f"task {task.get('id')} proof observed_at")
        commit = _required_string(proof.get("commit_sha"), f"task {task.get('id')} proof commit_sha")
        if not COMMIT_RE.fullmatch(commit):
            raise DeliveryGateError(f"task {task.get('id')} proof commit_sha is invalid")
        if key[0] == "command":
            exit_code = proof.get("exit_code")
            passing = required_proof.get("passing_exit_codes", [0])
            if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                raise DeliveryGateError(f"task {task.get('id')} proof exit_code must be an integer")
            if not isinstance(passing, list) or exit_code not in passing:
                raise DeliveryGateError(
                    f"task {task.get('id')} proof failed ({exit_code}): {key[1]}"
                )


def _validate_disposition(
    task_id: str, entry: Mapping[str, object], *, author: str, default_reviewer: str
) -> None:
    if entry.get("evidence_status") != "complete":
        raise DeliveryGateError(f"task {task_id} evidence_status must be complete")
    disposition = entry.get("human_disposition")
    if not isinstance(disposition, dict) or disposition.get("decision") != "approved":
        raise DeliveryGateError(f"task {task_id} requires an approved human disposition")
    reviewer = _required_string(disposition.get("reviewer"), f"task {task_id} disposition reviewer")
    if reviewer.casefold() != default_reviewer.casefold():
        raise DeliveryGateError(f"task {task_id} disposition reviewer must match manifest reviewer")
    if reviewer.casefold() == author.casefold():
        raise DeliveryGateError(f"task {task_id} reviewer must differ from the author")
    _required_string(disposition.get("reason"), f"task {task_id} disposition reason")
    _timestamp(disposition.get("observed_at"), f"task {task_id} disposition observed_at")


def run_gate(
    manifest_path: Path,
    *,
    include_prds: Sequence[str] = (),
    anvil_prefix: Sequence[str] = ("anvil",),
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: int = 30,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run,
) -> GateResult:
    if not anvil_prefix or any(not token for token in anvil_prefix):
        raise DeliveryGateError("anvil command prefix must not be empty")
    if timeout < 1:
        raise DeliveryGateError("timeout must be positive")
    manifest = _load_manifest(manifest_path)
    author = _required_string(manifest.get("author"), "author")
    reviewer = _required_string(manifest.get("reviewer"), "reviewer")
    if author.casefold() == reviewer.casefold():
        raise DeliveryGateError("reviewer must differ from author")
    _validate_final_reviews(manifest, author)
    entries = _task_entries(manifest)

    working_directory = (cwd or Path.cwd()).resolve()
    command_environment = dict(os.environ if environment is None else environment)
    commands_run: list[tuple[str, ...]] = []
    prd_ids = tuple(dict.fromkeys((*include_prds,)))
    required_ids = set(entries)
    for prd_id in prd_ids:
        data = _invoke(
            anvil_prefix,
            ("list", "--prd", prd_id, "--json"),
            cwd=working_directory,
            environment=command_environment,
            timeout=timeout,
            runner=runner,
            commands_run=commands_run,
        )
        tasks = data.get("tasks")
        if not isinstance(tasks, list):
            raise DeliveryGateError(f"Anvil list for {prd_id} returned malformed tasks")
        required_ids.update(
            _required_string(task.get("id"), f"Anvil list {prd_id} task id")
            for task in tasks
            if isinstance(task, dict)
        )

    for task_id in sorted(required_ids):
        entry = entries.get(task_id)
        if entry is None:
            raise DeliveryGateError(f"delivery manifest is missing task {task_id}")
        data = _invoke(
            anvil_prefix,
            ("show", task_id, "--json"),
            cwd=working_directory,
            environment=command_environment,
            timeout=timeout,
            runner=runner,
            commands_run=commands_run,
        )
        task = data.get("task")
        if not isinstance(task, dict) or task.get("id") != task_id:
            raise DeliveryGateError(f"Anvil show returned the wrong task for {task_id}")
        if task.get("status") != "done":
            raise DeliveryGateError(f"task {task_id} is not done (status={task.get('status')})")
        _validate_disposition(task_id, entry, author=author, default_reviewer=reviewer)
        _validate_proofs(task, entry)

    return GateResult(tuple(sorted(required_ids)), prd_ids, tuple(commands_run))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="PR-bound delivery manifest JSON.")
    parser.add_argument("--include-prd", action="append", default=[], help="Require every task in this PRD.")
    parser.add_argument(
        "--anvil-prefix",
        action="append",
        default=[],
        metavar="TOKEN",
        help="Command prefix token; repeat for test wrappers (default: anvil).",
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_gate(
            args.manifest.resolve(),
            include_prds=args.include_prd,
            anvil_prefix=tuple(args.anvil_prefix) or ("anvil",),
            timeout=args.timeout,
        )
    except (DeliveryGateError, OSError, subprocess.SubprocessError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print(f"Anvil delivery gate failed: {exc}", file=sys.stderr)
        return 1
    payload = {"ok": True, "result": result.as_dict()}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"Anvil delivery gate passed: {len(result.task_ids)} tasks, "
            f"{len(result.commands_run)} read-only commands"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
