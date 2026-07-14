"""Validate the versioned, exhaustive CLI UX audit.

The checked-in audit is a migration ratchet.  It must contain exactly one
record per visible command leaf, and its per-dimension failure budgets may not
be exceeded.  Once every budget reaches zero, ``enforcement`` becomes
``strict`` and any failing dimension is rejected.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Iterable


SCHEMA_VERSION = 1
MAX_JSON_BYTES = 4 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
MANIFEST_REL = Path("docs/CLI-COMMAND-MANIFEST.json")
AUDIT_REL = Path("docs/CLI-UX-AUDIT.json")

ALWAYS_DIMENSIONS = (
    "grammar",
    "help",
    "configuration",
    "output",
    "errors",
    "boundedness",
    "docs",
    "parser_tests",
)
CONDITIONAL_DIMENSIONS = (
    "preview",
    "confirmation",
    "drift_protection",
    "timeout",
    "rollback",
    "journaling",
    "remote_parity",
    "linux",
    "macos",
    "windows",
)
DIMENSIONS = ALWAYS_DIMENSIONS + CONDITIONAL_DIMENSIONS
STATUSES = frozenset({"pass", "fail", "not-applicable"})
ENFORCEMENT_MODES = frozenset({"ratchet", "strict"})

class AuditError(ValueError):
    """The audit or command manifest is malformed or stale."""


def _read_json(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        payload = handle.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        raise AuditError(f"JSON exceeds {MAX_JSON_BYTES} byte limit: {path}")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuditError(f"JSON document must be an object: {path}")
    return value


def _atomic_write_json(
    path: Path, value: dict[str, object], *, exclusive: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode if path.exists() else None
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temporary_name, existing_mode)
        else:
            current_umask = os.umask(0)
            os.umask(current_umask)
            os.chmod(temporary_name, 0o666 & ~current_umask)
        if exclusive:
            os.link(temporary_name, path)
            Path(temporary_name).unlink()
        else:
            os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def visible_leaves(manifest: dict[str, object]) -> tuple[dict[str, object], ...]:
    records = manifest.get("commands")
    if not isinstance(records, list):
        raise AuditError("command manifest must contain a commands array")
    visible: list[dict[str, object]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise AuditError(f"command manifest commands[{index}] must be an object")
        path = record.get("path")
        if not isinstance(path, str) or not path.strip():
            raise AuditError(f"command manifest commands[{index}].path is required")
        if record.get("visible") is True:
            visible.append(record)
        elif record.get("visible") is not False:
            raise AuditError(f"command manifest commands[{index}].visible must be boolean")

    paths = {str(record["path"]) for record in visible}
    leaves: list[dict[str, object]] = []
    for record in visible:
        path = str(record["path"])
        handler = record.get("handler")
        has_visible_child = any(candidate.startswith(f"{path} ") for candidate in paths)
        if isinstance(handler, str) and handler.strip():
            leaves.append(record)
        elif handler is not None:
            raise AuditError(f"visible command {path}.handler must be a string or null")
        elif not has_visible_child:
            raise AuditError(f"visible command {path} has neither a handler nor visible children")
    return tuple(sorted(leaves, key=lambda record: str(record["path"])))


def _manifest_fingerprint(command: dict[str, object]) -> str:
    payload = json.dumps(
        command, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _decision(
    value: object, *, path: str, dimension: str
) -> tuple[str, str | None]:
    if isinstance(value, str):
        status = value
        reason = None
    elif isinstance(value, dict):
        status = value.get("status")
        reason = value.get("reason")
        unknown = set(value) - {"status", "reason"}
        if unknown:
            raise AuditError(
                f"{path}.{dimension} has unknown fields: {', '.join(sorted(unknown))}"
            )
    else:
        raise AuditError(f"{path}.{dimension} must be a status or status object")
    if status not in STATUSES:
        raise AuditError(f"{path}.{dimension} has invalid status {status!r}")
    if dimension in ALWAYS_DIMENSIONS and status == "not-applicable":
        raise AuditError(f"{path}.{dimension} may not be not-applicable")
    if status == "not-applicable" and (
        not isinstance(reason, str) or not reason.strip()
    ):
        raise AuditError(f"{path}.{dimension} requires a non-empty reason")
    if status != "not-applicable" and reason is not None:
        raise AuditError(f"{path}.{dimension} may only include a reason when not-applicable")
    return status, reason.strip() if isinstance(reason, str) else None


def _status(value: object, *, path: str, dimension: str) -> str:
    return _decision(value, path=path, dimension=dimension)[0]


def dimension_attestation(
    manifest_fingerprint: str,
    dimension: str,
    decision: object,
    evidence: list[str],
) -> str:
    """Bind a reviewed decision and its evidence to one command interface."""

    status, reason = _decision(decision, path="attestation", dimension=dimension)
    payload = {
        "manifest_fingerprint": manifest_fingerprint,
        "dimension": dimension,
        "status": status,
        "reason": reason,
        "evidence": sorted(evidence),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _record_paths(records: Iterable[object]) -> list[str]:
    paths: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise AuditError(f"audit records[{index}] must be an object")
        path = record.get("path")
        if not isinstance(path, str) or not path.strip():
            raise AuditError(f"audit records[{index}].path is required")
        paths.append(path)
    return paths


def _validate_test_node(root: Path, node_id: str, *, path: str, dimension: str) -> None:
    relative_value, separator, selector = node_id.partition("::")
    relative = Path(relative_value)
    if (
        not separator
        or not selector
        or "::" in selector
        or "[" in selector
        or "]" in selector
        or "\\" in relative_value
        or relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or relative.parts[0] != "tests"
        or relative.suffix != ".py"
        or relative.as_posix() == "tests/test_cli_ux_audit.py"
    ):
        raise AuditError(f"{path}.{dimension} has invalid pytest node ID {node_id!r}")
    test_path = root / relative
    if not test_path.is_file():
        raise AuditError(f"{path}.{dimension} evidence test does not exist: {node_id}")
    test_name = selector.split("::")[-1].split("[", 1)[0]
    if not test_name.startswith("test_") or not all(
        character in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
        for character in test_name
    ):
        raise AuditError(f"{path}.{dimension} has invalid test selector: {node_id}")
    try:
        module = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
    except SyntaxError as exc:
        raise AuditError(f"{path}.{dimension} evidence test file has invalid syntax: {exc}") from exc
    collected_names = {
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if test_name not in collected_names:
        raise AuditError(f"{path}.{dimension} evidence test is not defined: {node_id}")


def validate_audit(
    manifest: dict[str, object], audit: dict[str, object], *, root: Path = ROOT
) -> dict[str, int]:
    expected_top_level = {
        "schema_version",
        "manifest_schema_version",
        "enforcement",
        "maximum_failures",
        "records",
    }
    if set(audit) != expected_top_level:
        raise AuditError(
            f"audit top-level fields mismatch; missing={sorted(expected_top_level - set(audit))}, "
            f"unknown={sorted(set(audit) - expected_top_level)}"
        )
    if (
        not isinstance(audit.get("schema_version"), int)
        or isinstance(audit.get("schema_version"), bool)
        or audit.get("schema_version") != SCHEMA_VERSION
    ):
        raise AuditError(f"audit schema_version must be {SCHEMA_VERSION}")
    if audit.get("manifest_schema_version") != manifest.get("schema_version"):
        raise AuditError("audit manifest_schema_version does not match the command manifest")
    enforcement = audit.get("enforcement")
    if enforcement not in ENFORCEMENT_MODES:
        raise AuditError(f"audit enforcement must be one of {sorted(ENFORCEMENT_MODES)}")

    records = audit.get("records")
    if not isinstance(records, list):
        raise AuditError("audit must contain a records array")
    actual_paths = _record_paths(records)
    duplicates = sorted(path for path, count in Counter(actual_paths).items() if count > 1)
    if duplicates:
        raise AuditError(f"duplicate audit records: {', '.join(duplicates)}")
    expected_paths = [str(record["path"]) for record in visible_leaves(manifest)]
    missing = sorted(set(expected_paths) - set(actual_paths))
    stale = sorted(set(actual_paths) - set(expected_paths))
    if missing or stale:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if stale:
            details.append(f"stale: {', '.join(stale)}")
        raise AuditError("audit leaf inventory mismatch (" + "; ".join(details) + ")")
    if actual_paths != sorted(actual_paths):
        raise AuditError("audit records must be sorted by path")

    manifest_by_path = {str(record["path"]): record for record in visible_leaves(manifest)}
    failures = Counter({dimension: 0 for dimension in DIMENSIONS})
    for record in records:
        path = str(record["path"])
        expected = manifest_by_path[path]
        expected_record_fields = {
            "path",
            "manifest_fingerprint",
            "docs_anchor",
            "mutation_class",
            "output_policy",
            "dimensions",
            "evidence",
            "attestations",
        }
        if set(record) != expected_record_fields:
            raise AuditError(
                f"{path} fields mismatch; missing={sorted(expected_record_fields - set(record))}, "
                f"unknown={sorted(set(record) - expected_record_fields)}"
            )
        if record.get("manifest_fingerprint") != _manifest_fingerprint(expected):
            raise AuditError(f"{path}.manifest_fingerprint is stale")
        if record.get("docs_anchor") != expected.get("docs_anchor"):
            raise AuditError(f"{path}.docs_anchor is stale")
        if record.get("mutation_class") != expected.get("mutation_class"):
            raise AuditError(f"{path}.mutation_class is stale")
        if record.get("output_policy") != expected.get("output_policy"):
            raise AuditError(f"{path}.output_policy is stale")
        evidence = record.get("evidence")
        if not isinstance(evidence, dict):
            raise AuditError(f"{path}.evidence must be an object")
        unknown_evidence = set(evidence) - set(DIMENSIONS)
        if unknown_evidence:
            raise AuditError(f"{path}.evidence has unknown dimensions: {sorted(unknown_evidence)}")
        attestations = record.get("attestations")
        if not isinstance(attestations, dict):
            raise AuditError(f"{path}.attestations must be an object")
        unknown_attestations = set(attestations) - set(DIMENSIONS)
        if unknown_attestations:
            raise AuditError(
                f"{path}.attestations has unknown dimensions: {sorted(unknown_attestations)}"
            )
        dimensions = record.get("dimensions")
        if not isinstance(dimensions, dict):
            raise AuditError(f"{path}.dimensions must be an object")
        unknown = set(dimensions) - set(DIMENSIONS)
        missing_dimensions = set(DIMENSIONS) - set(dimensions)
        if unknown or missing_dimensions:
            raise AuditError(
                f"{path}.dimensions mismatch; missing={sorted(missing_dimensions)}, "
                f"unknown={sorted(unknown)}"
            )
        for dimension in DIMENSIONS:
            status = _status(dimensions[dimension], path=path, dimension=dimension)
            dimension_evidence = evidence.get(dimension, [])
            if (
                not isinstance(dimension_evidence, list)
                or any(
                    not isinstance(test, str) or not test.strip()
                    for test in dimension_evidence
                )
            ):
                raise AuditError(f"{path}.evidence.{dimension} must be a string array")
            if len(dimension_evidence) != len(set(dimension_evidence)):
                raise AuditError(f"{path}.evidence.{dimension} contains duplicates")
            if status == "pass" and not dimension_evidence:
                raise AuditError(f"{path}.{dimension} pass requires test evidence")
            attestation = attestations.get(dimension)
            if status in {"pass", "not-applicable"}:
                expected_attestation = dimension_attestation(
                    str(record["manifest_fingerprint"]),
                    dimension,
                    dimensions[dimension],
                    dimension_evidence,
                )
                if attestation != expected_attestation:
                    raise AuditError(
                        f"{path}.{dimension} requires attestation to the current interface, "
                        "decision, and evidence"
                    )
            elif attestation is not None:
                raise AuditError(f"{path}.{dimension} fail may not have an attestation")
            for test in dimension_evidence:
                _validate_test_node(root, test, path=path, dimension=dimension)
            if status == "fail":
                failures[dimension] += 1

    budgets = audit.get("maximum_failures")
    if not isinstance(budgets, dict) or set(budgets) != set(DIMENSIONS):
        raise AuditError("maximum_failures must contain exactly every audit dimension")
    for dimension in DIMENSIONS:
        budget = budgets[dimension]
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
            raise AuditError(f"maximum_failures.{dimension} must be a non-negative integer")
        if failures[dimension] > budget:
            raise AuditError(
                f"{dimension} failures increased: {failures[dimension]} > budget {budget}"
            )
        if enforcement == "ratchet" and failures[dimension] != budget:
            raise AuditError(
                f"{dimension} budget must equal current failures: "
                f"{budget} != {failures[dimension]}"
            )
        if enforcement == "strict" and (failures[dimension] or budget):
            raise AuditError(
                f"strict enforcement requires zero {dimension} failures and budget"
            )
    return {dimension: failures[dimension] for dimension in DIMENSIONS}


def validate_monotonic(previous: dict[str, object], current: dict[str, object]) -> None:
    """Reject a downgrade relative to the trusted audit from the PR base."""

    previous_enforcement = previous.get("enforcement")
    current_enforcement = current.get("enforcement")
    if previous_enforcement == "strict" and current_enforcement != "strict":
        raise AuditError("strict enforcement may not be downgraded")
    previous_budgets = previous.get("maximum_failures")
    current_budgets = current.get("maximum_failures")
    if not isinstance(previous_budgets, dict) or not isinstance(current_budgets, dict):
        raise AuditError("historical comparison requires failure budgets")
    for dimension in DIMENSIONS:
        before = previous_budgets.get(dimension)
        after = current_budgets.get(dimension)
        if not isinstance(before, int) or isinstance(before, bool):
            raise AuditError(f"previous maximum_failures.{dimension} is invalid")
        if not isinstance(after, int) or isinstance(after, bool):
            raise AuditError(f"current maximum_failures.{dimension} is invalid")
        if after > before:
            raise AuditError(
                f"{dimension} failure budget regressed: {after} > previous {before}"
            )

    previous_records = previous.get("records")
    current_records = current.get("records")
    if not isinstance(previous_records, list) or not isinstance(current_records, list):
        raise AuditError("historical comparison requires records arrays")
    previous_by_path = {
        str(record.get("path")): record
        for record in previous_records
        if isinstance(record, dict)
    }
    current_by_path = {
        str(record.get("path")): record
        for record in current_records
        if isinstance(record, dict)
    }
    for path in sorted(set(previous_by_path) & set(current_by_path)):
        before_dimensions = previous_by_path[path].get("dimensions")
        after_dimensions = current_by_path[path].get("dimensions")
        if not isinstance(before_dimensions, dict) or not isinstance(after_dimensions, dict):
            raise AuditError(f"{path} historical comparison requires dimensions")
        for dimension in DIMENSIONS:
            before = _status(before_dimensions.get(dimension), path=path, dimension=dimension)
            after = _status(after_dimensions.get(dimension), path=path, dimension=dimension)
            if before == "pass" and after != "pass":
                raise AuditError(f"{path}.{dimension} regressed from pass to {after}")
            if before == "not-applicable" and after == "fail":
                raise AuditError(f"{path}.{dimension} regressed from not-applicable to fail")


def _baseline_record(command: dict[str, object]) -> dict[str, object]:
    dimensions = {dimension: "fail" for dimension in DIMENSIONS}
    return {
        "path": command["path"],
        "manifest_fingerprint": _manifest_fingerprint(command),
        "docs_anchor": command["docs_anchor"],
        "mutation_class": command["mutation_class"],
        "output_policy": command["output_policy"],
        "dimensions": dimensions,
        "evidence": {},
        "attestations": {},
    }


def bootstrap_audit(manifest: dict[str, object]) -> dict[str, object]:
    records = [_baseline_record(command) for command in visible_leaves(manifest)]
    failures = Counter({dimension: 0 for dimension in DIMENSIONS})
    for record in records:
        for dimension, value in record["dimensions"].items():
            if value == "fail":
                failures[dimension] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_schema_version": manifest["schema_version"],
        "enforcement": "ratchet",
        "maximum_failures": {
            dimension: failures[dimension] for dimension in DIMENSIONS
        },
        "records": records,
    }


def refresh_audit(
    manifest: dict[str, object], audit: dict[str, object]
) -> dict[str, object]:
    """Refresh derived fields; reviewed attestations remain bound to the old interface."""

    records = audit.get("records")
    if not isinstance(records, list):
        raise AuditError("audit must contain a records array before refresh")
    paths = _record_paths(records)
    duplicates = sorted(path for path, count in Counter(paths).items() if count > 1)
    if duplicates:
        raise AuditError(f"duplicate audit records: {', '.join(duplicates)}")
    existing = {str(record["path"]): record for record in records}
    refreshed_records: list[dict[str, object]] = []
    for command in visible_leaves(manifest):
        path = str(command["path"])
        if path not in existing:
            refreshed_records.append(_baseline_record(command))
            continue
        record = dict(existing[path])
        record.update(
            {
                "path": command["path"],
                "manifest_fingerprint": _manifest_fingerprint(command),
                "docs_anchor": command["docs_anchor"],
                "mutation_class": command["mutation_class"],
                "output_policy": command["output_policy"],
            }
        )
        refreshed_records.append(record)
    refreshed = dict(audit)
    refreshed.update(
        {
            "schema_version": SCHEMA_VERSION,
            "manifest_schema_version": manifest["schema_version"],
            "records": refreshed_records,
        }
    )
    return refreshed


def _summary(failures: dict[str, int], *, records: int, enforcement: str) -> str:
    total = sum(failures.values())
    return (
        f"CLI UX audit: {records} leaves, {total} failing dimensions, "
        f"enforcement={enforcement}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Validate the checked-in audit.")
    action.add_argument(
        "--bootstrap",
        action="store_true",
        help="Create the initial conservative ratchet; refuses to replace an existing audit.",
    )
    action.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh manifest-derived fields while preserving reviewed statuses and budgets.",
    )
    parser.add_argument(
        "--baseline-audit",
        type=Path,
        help="Trusted audit from the PR base for monotonic-history validation.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the validation summary as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        manifest = _read_json(root / MANIFEST_REL)
        audit_path = root / AUDIT_REL
        if args.bootstrap:
            _atomic_write_json(audit_path, bootstrap_audit(manifest), exclusive=True)
        elif args.refresh:
            audit = _read_json(audit_path)
            _atomic_write_json(audit_path, refresh_audit(manifest, audit))
        audit = _read_json(audit_path)
        failures = validate_audit(manifest, audit, root=root)
        if args.baseline_audit is not None:
            if not args.check:
                raise AuditError("--baseline-audit is only valid with --check")
            previous = _read_json(args.baseline_audit.resolve())
            validate_monotonic(previous, audit)
    except (AuditError, FileExistsError, FileNotFoundError, OSError, UnicodeError) as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"CLI UX audit failed: {exc}", file=sys.stderr)
        return 1
    summary = _summary(
        failures,
        records=len(audit["records"]),
        enforcement=str(audit["enforcement"]),
    )
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "enforcement": audit["enforcement"],
                    "records": len(audit["records"]),
                    "failures": failures,
                    "total_failures": sum(failures.values()),
                },
                sort_keys=True,
            )
        )
    else:
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
