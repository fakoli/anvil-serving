"""Offline validation and deterministic planning for prepared SPEED-Bench JSONL.

This validates provenance declarations, not upstream downloads or license rights.
It neither prepares the upstream Parquet distribution nor executes model requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
import unicodedata
from urllib.parse import urlsplit

from .jobs import BenchmarkJobError
from ..operator_output import CommandResult, OperatorError, UsageError

CORPUS_SCHEMA = "anvil-serving.speed-corpus/v1"
PLAN_SCHEMA = "anvil-serving.speed-plan/v1"
SELECTION_VERSION = "anvil-speed-selection/v1"
UNRESOLVED_TEXT = "FULL BENCHMARK DATA SHOULD BE FETCHED FROM THE SOURCE USING SPECDEC_BENCH"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_CORPUS_BYTES = 128 * 1024 * 1024
MAX_ROW_BYTES = 1024 * 1024
MAX_ROWS = 10_000
QUALITATIVE_CATEGORIES = frozenset({
    "coding", "stem", "humanities", "math", "multilingual", "qa", "rag",
    "roleplay", "reasoning", "summarization", "writing",
})
THROUGHPUT_CATEGORIES = frozenset({"high_entropy", "mixed", "low_entropy"})
CONFIG_BUCKETS = {f"throughput_{n}k": n * 1024 for n in (1, 2, 8, 16, 32)}
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _fail(message: str) -> None:
    raise BenchmarkJobError("bad_speed_corpus", message)


def _canonical(value: Any) -> bytes:
    # The corpus contract retains literal UTF-8, unlike legacy job JSON hashes.
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: Any, *, maximum: int = 2048) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for c in value)):
        _fail("Metadata must be bounded nonempty text without controls or surrogates.")
    return value


def _hex(value: Any, pattern: re.Pattern) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        _fail("Revisions and SHA-256 digests must be complete lowercase hexadecimal values.")


def _url(value: Any) -> None:
    _text(value)
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.fragment and not parsed.query
                 and not any(c.isspace() for c in value))
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        _fail("Source and license references must be credential-free HTTPS URLs.")


def _object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("Duplicate JSON keys are forbidden.")
        result[key] = value
    return result


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                           parse_constant=lambda _: _fail("Non-finite JSON is forbidden."))
    except (UnicodeError, ValueError, RecursionError):
        _fail("Input must be a bounded UTF-8 JSON object.")
    if not isinstance(value, dict):
        _fail("Input must be a JSON object.")
    return value


def _read(path: Path, limit: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                _fail("Input must be a bounded regular file.")
            raw = handle.read(limit + 1)
    except OSError:
        _fail("Input file is unavailable.")
    if len(raw) > limit:
        _fail("Input exceeds its byte limit.")
    return raw


def _manifest(value: dict) -> None:
    if set(value) != {"schema", "dataset", "dataset_revision", "preparer_revision",
                       "config", "data_file", "data_sha256", "sources", "sources_sha256"}:
        _fail("Corpus manifest fields do not match v1.")
    if value["schema"] != CORPUS_SCHEMA or value["dataset"] != "nvidia/SPEED-Bench":
        _fail("Unsupported corpus schema or dataset.")
    for field in ("dataset_revision", "preparer_revision"):
        _hex(value[field], _REVISION)
    for field in ("data_sha256", "sources_sha256"):
        _hex(value[field], _SHA256)
    if not isinstance(value["config"], str) or value["config"] not in {"qualitative", *CONFIG_BUCKETS}:
        _fail("Unsupported SPEED-Bench config.")
    name = _text(value["data_file"], maximum=255)
    if name in {".", ".."} or any(c in name for c in "/\\:"):
        _fail("Corpus data_file must be a local basename.")
    sources = value["sources"]
    if not isinstance(sources, dict) or not 1 <= len(sources) <= 128:
        _fail("A bounded per-source provenance registry is required.")
    for source, details in sources.items():
        _url(source)
        if not isinstance(details, dict) or set(details) != {"revision", "license_url", "license_decision"}:
            _fail("Source provenance fields do not match v1.")
        _hex(details["revision"], _REVISION)
        _url(details["license_url"])
        if details["license_decision"] != "approved":
            _fail("Each source needs an explicit approved license decision.")
    if _digest(sources) != value["sources_sha256"]:
        _fail("Source provenance digest mismatch.")


def _row(value: dict, categories: frozenset[str], sources: dict) -> None:
    required = {"question_id", "category", "turns", "source", "src_id"}
    if not required <= value.keys() or value.keys() - required - {"sub_category", "difficulty", "multiturn"}:
        _fail("Corpus row fields do not match v1.")
    _text(value["question_id"], maximum=128)
    # Some upstream RoleBench identifiers contain more than 10,000 characters.
    _text(value["src_id"], maximum=64 * 1024)
    _text(value["category"], maximum=32)
    if value["category"] not in categories:
        _fail("Row category does not belong to the selected config.")
    _text(value["source"])
    if value["source"] not in sources:
        _fail("Row source is absent from the provenance registry.")
    for field in ("sub_category", "difficulty"):
        if field in value and value[field] is not None:
            _text(value[field], maximum=512)
    turns = value["turns"]
    if not isinstance(turns, list) or not 1 <= len(turns) <= 5:
        _fail("Rows need one through five ordered turns.")
    for turn in turns:
        if (not isinstance(turn, str) or not turn.strip()
                or any(0xD800 <= ord(c) <= 0xDFFF for c in turn)):
            _fail("Turns must contain nonempty Unicode text.")
        if UNRESOLVED_TEXT in turn:
            _fail("Corpus contains unresolved upstream text; preparation is required.")
    if "multiturn" in value and (type(value["multiturn"]) is not bool
                                 or value["multiturn"] != (len(turns) > 1)):
        _fail("multiturn must agree with the ordered turns.")


def plan_speed_bench(manifest_path: str | Path, *, categories: list[str] | None = None,
                     samples_per_category: int = 8, seed: int = 42) -> dict:
    """Validate all input rows and return a bounded, prompt-free offline plan."""
    if type(seed) is not int or not 0 <= seed < 2**64:
        _fail("Seed must be an unsigned 64-bit integer.")
    if type(samples_per_category) is not int or not 1 <= samples_per_category <= MAX_ROWS:
        _fail("Sample count must be between one and the maximum row count.")
    try:
        path = Path(manifest_path).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        _fail("Manifest path is unavailable.")
    manifest_raw = _read(path, MAX_MANIFEST_BYTES)
    manifest = _json(manifest_raw)
    _manifest(manifest)
    config = manifest["config"]
    allowed = QUALITATIVE_CATEGORIES if config == "qualitative" else THROUGHPUT_CATEGORIES
    if categories is None:
        categories = ["coding", "reasoning", "rag", "writing"] if config == "qualitative" else sorted(allowed)
    if (not isinstance(categories, list) or not categories
            or any(not isinstance(c, str) or c not in allowed for c in categories)
            or len(set(categories)) != len(categories)):
        _fail("Requested categories must be a nonempty unique subset of the config.")
    try:
        data_path = (path.parent / manifest["data_file"]).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        _fail("Corpus data path is unavailable.")
    if not data_path.is_relative_to(path.parent):
        _fail("Corpus data must remain inside the manifest directory.")
    raw = _read(data_path, MAX_CORPUS_BYTES)
    if hashlib.sha256(raw).hexdigest() != manifest["data_sha256"]:
        _fail("Corpus content digest mismatch.")
    counts = dict.fromkeys(sorted(allowed), 0)
    candidates: dict[str, list[dict]] = {c: [] for c in categories}
    seen: set[str] = set()
    # ponytail: bounded in-memory input (128 MiB); stream if larger corpora become necessary.
    for line in raw.splitlines():
        if len(seen) >= MAX_ROWS or len(line) > MAX_ROW_BYTES:
            _fail("Corpus exceeds row-count or row-byte limits.")
        row = _json(line)
        _row(row, allowed, manifest["sources"])
        question_id = row["question_id"]
        if question_id in seen:
            _fail("Duplicate question IDs are forbidden.")
        seen.add(question_id)
        category = row["category"]
        counts[category] += 1
        if category in candidates:
            candidates[category].append({"question_id": question_id,
                                         "row_sha256": _digest(row),
                                         "turn_count": len(row["turns"])})
    selected = []
    for category in sorted(categories):
        if counts[category] < samples_per_category:
            _fail("A requested category has insufficient rows.")
        ordered = sorted(candidates[category], key=lambda r: (
            _digest([SELECTION_VERSION, seed, r["question_id"]]), r["question_id"]))
        selected.extend({"category": category, **r} for r in ordered[:samples_per_category])
    plan = {"schema": PLAN_SCHEMA, "execution_ready": False,
            "provenance_status": "declarations_only", "dataset": manifest["dataset"],
            "dataset_revision": manifest["dataset_revision"],
            "preparer_revision": manifest["preparer_revision"], "config": config,
            "nominal_input_tokens": CONFIG_BUCKETS.get(config),
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "corpus_sha256": manifest["data_sha256"], "sources_sha256": manifest["sources_sha256"],
            "selection_version": SELECTION_VERSION, "categories": sorted(categories),
            "seed": seed, "samples_per_category": samples_per_category,
            "category_counts": counts, "row_count": len(seen), "selected": selected,
            "selected_turn_count": sum(r["turn_count"] for r in selected)}
    return {**plan, "plan_sha256": _digest(plan)}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError("Invalid speed plan arguments; use focused help.")


def main(argv: list[str] | None = None) -> CommandResult:
    parser = _Parser(prog="anvil-serving eval benchmark speed")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Validate prepared JSONL and print an offline plan.")
    plan.add_argument("--manifest", required=True, type=Path)
    plan.add_argument("--categories", help="Comma-separated config categories; defaults depend on config.")
    plan.add_argument("--samples-per-category", type=int, default=8)
    plan.add_argument("--seed", type=int, default=42)
    try:
        args = parser.parse_args(argv)
        result = plan_speed_bench(args.manifest,
                                  categories=args.categories.split(",") if args.categories is not None else None,
                                  samples_per_category=args.samples_per_category, seed=args.seed)
    except BenchmarkJobError as error:
        failure = OperatorError(error.message, code=error.code)
    except UsageError as error:
        failure = error
    else:
        return CommandResult(data=result, human_stdout=json.dumps(
            result, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n")
    return CommandResult(error=failure, human_stdout=json.dumps(
        {"ok": False, "error": {"code": failure.code, "message": failure.message}}) + "\n")
