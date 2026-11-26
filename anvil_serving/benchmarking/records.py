"""One published benchmark record per finding, derived from retained evidence.

A record is a read-only projection. It joins a dated finding page with its
evidence bundle (artifact-set manifest, decision summary, and recognized
capacity/quality artifacts) and states every unrecorded core field as an
explicit gap. Native artifacts are never rewritten.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .. import benchmark_evidence as evidence

RECORD_SCHEMA = "anvil-serving.benchmark-record/v1"
INDEX_SCHEMA = "anvil-serving.benchmark-record-index/v1"
DEFAULT_ROOT = "docs/findings"
NOT_RECORDED = "not recorded"
CORE_FIELDS = ("model", "engine", "quantization", "hardware", "context_tokens", "concurrency", "decision")
MAX_HEADLINES = 6

_DATE = re.compile(r"^(\d{4}-\d{2}(?:-\d{2})?)-")
_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
# Older findings state the decision in prose: "**Decision:** no promotion; ...".
_DECISION = re.compile(r"\*\*Decision:?\*\*:?\s*([^*\n]+)")
_HARDWARE = (
    (re.compile(r"pro[\s_-]*6000", re.I), "RTX PRO 6000"),
    (re.compile(r"rtx[\s_-]*5090|(?<![\d])5090(?![\d])", re.I), "RTX 5090"),
    (re.compile(r"rtx[\s_-]*4090", re.I), "RTX 4090"),
    (re.compile(r"m4[\s_-]*max", re.I), "Apple M4 Max"),
    (re.compile(r"\bh100\b", re.I), "H100"),
    (re.compile(r"xeon", re.I), "Intel Xeon"),
)
# Evidence-label vocabulary drifted across campaigns; fold it into stable categories.
_CATEGORIES = (
    ("external", "external-prior"),
    ("multimodal", "multimodal"),
    ("capacity", "capacity"),
    ("performance", "capacity"),
    ("endurance", "capacity"),
    ("quality", "quality"),
    ("functional", "functional"),
    ("compatib", "functional"),
    ("diagnostic", "diagnostic"),
)
_KIND_CATEGORY = {"capacity": "capacity", "quality": "quality", "stability": "capacity",
                  "speculative": "capacity", "campaign": "quality", "external_prior": "external-prior"}
_IDENTITY_ALIASES = {
    "model": ("model", "served_model", "candidate", "model_id"),
    # Names only: revisions and image digests identify a build, not an engine.
    "engine": ("engine", "runtime"),
    "quantization": ("quantization", "precision", "weights", "weight_format"),
    "hardware": ("hardware", "gpu", "measured_hardware"),
}


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[: evidence.MAX_TEXT_CHARS]
    if isinstance(value, Mapping):
        for key in ("name", "id", "model", "value", "summary"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()[: evidence.MAX_TEXT_CHARS]
    return None


def _labels(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [item for item in evidence._list(value) if isinstance(item, str)]


UNIDENTIFIED_HARDWARE = "unidentified"


def hardware_label(value: str | None) -> str | None:
    """Return a canonical accelerator name.

    Free text that names no known accelerator (host labels, topology such as
    "TP2") becomes ``unidentified`` so host names never surface as hardware.
    """
    if not value:
        return None
    for pattern, label in _HARDWARE:
        if pattern.search(value):
            return label
    return UNIDENTIFIED_HARDWARE


def categories(labels: list[str], kinds: Counter) -> list[str]:
    found: list[str] = []
    for label in labels:
        lowered = label.casefold()
        for needle, category in _CATEGORIES:
            if needle in lowered and category not in found:
                found.append(category)
                break
    for kind in kinds:
        category = _KIND_CATEGORY.get(kind)
        if category and category not in found:
            found.append(category)
    return found


def _units(root: Path) -> dict[str, dict[str, Path | None]]:
    units: dict[str, dict[str, Path | None]] = {}
    for path in sorted(root.glob("*.md")):
        if _DATE.match(path.name) and path.is_file() and not path.is_symlink():
            units.setdefault(path.stem, {"finding": None, "evidence": None})["finding"] = path
    candidates = [*root.glob("*-evidence"), *root.glob("artifacts/*")]
    for path in sorted(candidates):
        if not path.is_dir() or path.is_symlink():
            continue
        stem = path.name.removesuffix("-evidence")
        if _DATE.match(stem + "-"):
            units.setdefault(stem, {"finding": None, "evidence": None})["evidence"] = path
    return units


def _scan_evidence(directory: Path) -> dict[str, Any]:
    scan: dict[str, Any] = {"manifest": None, "summary": None, "points": [], "suites": [],
                            "kinds": Counter(), "subject": {key: Counter() for key in _IDENTITY_ALIASES},
                            "context": set(), "concurrency": set(), "failed_requests": 0}
    paths, _, _ = evidence._collect_json(directory)
    root = directory.resolve()
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        try:
            raw = evidence._load_json(path)
        except evidence.EvidenceError:
            continue
        schema = raw.get("schema")
        if schema == "anvil-serving.benchmark-artifact-set/v1" and scan["manifest"] is None:
            scan["manifest"] = (path, raw)
            continue
        if schema == "anvil-serving.benchmark-decision-summary/v1" and scan["summary"] is None:
            scan["summary"] = (path, raw)
            continue
        try:
            summary = evidence.summarize_payload(raw, path)
        except (evidence.EvidenceError, ValueError, TypeError):
            continue
        kind = summary["kind"]
        scan["kinds"][kind] += 1
        scan["subject"]["model"][summary.get("model")] += 1
        provenance = summary.get("provenance") or {}
        scan["subject"]["engine"][provenance.get("engine")] += 1
        scan["subject"]["hardware"][hardware_label(provenance.get("gpu"))] += 1
        capacity = summary.get("capacity") or {}
        if capacity.get("context_tokens") is not None:
            scan["context"].add(capacity["context_tokens"])
        if capacity.get("concurrency") is not None:
            scan["concurrency"].add(capacity["concurrency"])
        if kind == "capacity":
            metrics = evidence._mapping(raw.get("metrics"))
            requests, completed = capacity.get("requests"), capacity.get("completed")
            if isinstance(requests, int) and isinstance(completed, int):
                scan["failed_requests"] += max(requests - completed, 0)
            scan["points"].append({
                "artifact": path,
                "model": summary.get("model"),
                "engine": provenance.get("engine"),
                "hardware": hardware_label(provenance.get("gpu")),
                "concurrency": capacity.get("concurrency"),
                "context_tokens": capacity.get("context_tokens"),
                "requests": requests,
                "completed": completed,
                "aggregate_output_tok_s": capacity.get("aggregate_output_tok_s"),
                "per_user_decode_tok_s_p50": evidence._number(metrics.get("decode_tok_s_p50")),
                "ttft_p50_ms": capacity.get("ttft_p50_ms"),
                "ttft_p95_ms": evidence._number(metrics.get("ttft_p95_ms")),
                "e2e_p50_ms": capacity.get("e2e_p50_ms"),
                "performance_eligible": evidence._bool(raw.get("performance_eligible")),
            })
        for suite in (summary.get("quality") or {}).get("suites") or []:
            scan["suites"].append({
                "artifact": path,
                "model": summary.get("model"),
                "suite": suite["name"],
                "items": suite["items"],
                "fully_correct_items": suite["fully_correct_items"],
                "attempts": suite["attempts"],
                "passed_attempts": suite["passed_attempts"],
            })
    return scan


def _identity_value(identity: Mapping[str, Any], field: str) -> str | None:
    for key in _IDENTITY_ALIASES[field]:
        value = _text(identity.get(key))
        if value and not value.startswith("REPLACE_WITH"):  # unfilled template placeholder
            return value
    return None


def _explicit_hardware_labels(identity: Mapping[str, Any]) -> str | None:
    """Use the structured multi-accelerator list when a summary supplies it.

    Free-text ``hardware`` continues through the historical single-label path;
    this field exists only for a campaign that genuinely spans named hardware.
    """
    labels: list[str] = []
    for item in evidence._list(identity.get("measured_hardware")):
        label = hardware_label(_text(item))
        if label and label != UNIDENTIFIED_HARDWARE and label not in labels:
            labels.append(label)
    return " + ".join(labels) if labels else None


def _observed(counter: Counter) -> str | None:
    values = sorted(str(value) for value in counter if value)
    if not values:
        return None
    return values[0] if len(values) == 1 else f"{len(values)} values: " + ", ".join(values[:4])


def _headlines(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in evidence._list(summary.get("headline_results")):
        item = evidence._mapping(item)
        name = _text(item.get("metric")) or _text(item.get("name"))
        value = item.get("value")
        if not name or isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        rows.append({
            "metric": name,
            "value": value,
            "unit": _text(item.get("unit")),
            "condition": _text(item.get("condition")) or _text(item.get("denominator")),
        })
        if len(rows) == MAX_HEADLINES:
            break
    return rows


def _relative(path: Path | None, root: Path) -> str | None:
    return path.relative_to(root).as_posix() if path else None


def build_record(stem: str, finding: Path | None, directory: Path | None, root: Path) -> dict[str, Any]:
    """Build one record. ``root`` is the findings root; paths are relative to it."""
    scan = _scan_evidence(directory) if directory else _scan_evidence_empty()
    manifest = scan["manifest"][1] if scan["manifest"] else {}
    summary = scan["summary"][1] if scan["summary"] else {}
    campaign = manifest.get("campaign") if isinstance(manifest.get("campaign"), Mapping) else {}
    identity = evidence._mapping(summary.get("identity"))
    decision = summary.get("decision")

    title = None
    if finding:
        head = finding.read_text(encoding="utf-8", errors="replace")[:20_000]
        match = _H1.search(head)
        title = match.group(1).strip() if match else None
        if not decision:
            stated = _DECISION.search(head)
            decision = stated.group(1).strip().rstrip(".;")[:120] if stated else None
    date_match = _DATE.match(stem + "-")

    subject: dict[str, str] = {}
    for field in _IDENTITY_ALIASES:
        explicit_hardware = _explicit_hardware_labels(identity) if field == "hardware" else None
        value = explicit_hardware or _identity_value(identity, field) or _observed(scan["subject"][field])
        if field == "hardware" and not value:
            value = _text(summary.get("measured_hardware"))
        if field == "hardware" and not explicit_hardware:
            value = hardware_label(value)
        subject[field] = value or NOT_RECORDED

    evidence_labels = _labels(campaign.get("evidence_labels")) or _labels(summary.get("evidence_labels")) or _labels(
        evidence._mapping(decision).get("evidence_labels"))
    decision_labels = (
        _labels(campaign.get("decision_labels"))
        or _labels(evidence._mapping(decision).get("decision_labels"))
        or _labels(decision if isinstance(decision, str) else None)
    )
    summary_workload = evidence._mapping(summary.get("workload"))
    workload = {
        "context_tokens": sorted(scan["context"]) or _text(summary_workload.get("context_tokens")) or NOT_RECORDED,
        "concurrency": sorted(scan["concurrency"]) or _text(summary_workload.get("concurrency")) or NOT_RECORDED,
    }
    limitations = [text for text in (_text(item) for item in evidence._list(summary.get("limitations"))) if text]
    failures = evidence._list(summary.get("failures"))
    roles = {
        str(role.get("role")): str(role.get("status"))
        for role in (evidence._mapping(item) for item in evidence._list(manifest.get("artifact_roles")))
        if role.get("role")
    }
    evidence_index = None
    if directory:
        readme = directory / "README.md"
        evidence_index = readme if readme.is_file() else (scan["manifest"][0] if scan["manifest"] else None)

    record = {
        "schema": RECORD_SCHEMA,
        "id": stem,
        "date": date_match.group(1) if date_match else NOT_RECORDED,
        "title": title or stem,
        "categories": categories(evidence_labels, scan["kinds"]),
        "evidence_labels": evidence_labels,
        "decision": {
            "labels": decision_labels or NOT_RECORDED,
            "promotion_authorized": bool(campaign.get("promotion_authorized") is True),
        },
        "subject": subject,
        "workload": workload,
        "headline": _headlines(summary),
        "failures": {"listed": len(failures), "failed_requests": scan["failed_requests"]},
        "limitations": limitations[:MAX_HEADLINES],
        "artifacts": {
            "capacity": len(scan["points"]),
            "quality_suites": len(scan["suites"]),
            "by_kind": dict(sorted(scan["kinds"].items())),
            "roles": roles,
        },
        "links": {
            "finding": _relative(finding, root),
            "evidence_index": _relative(evidence_index, root),
            "manifest": _relative(scan["manifest"][0], root) if scan["manifest"] else None,
            "summary": _relative(scan["summary"][0], root) if scan["summary"] else None,
        },
    }
    core = {**subject, **workload, "decision": record["decision"]["labels"]}
    record["gaps"] = [field for field in CORE_FIELDS if core[field] == NOT_RECORDED]
    if not directory:
        record["gaps"].append("evidence bundle")
    points = [{**point, "record": stem, "artifact": _relative(point["artifact"], root)} for point in scan["points"]]
    suites = [{**suite, "record": stem, "artifact": _relative(suite["artifact"], root)} for suite in scan["suites"]]
    return {"record": record, "points": points, "suites": suites}


def _scan_evidence_empty() -> dict[str, Any]:
    return {"manifest": None, "summary": None, "points": [], "suites": [], "kinds": Counter(),
            "subject": {key: Counter() for key in _IDENTITY_ALIASES}, "context": set(),
            "concurrency": set(), "failed_requests": 0}


def build_index(root: str | Path = DEFAULT_ROOT) -> dict[str, Any]:
    """Return every dated finding as a record, newest first, plus chartable points."""
    root_path = Path(root)
    if not root_path.is_dir():
        raise evidence.EvidenceError(f"findings root is not a directory: {root_path}")
    records, points, suites = [], [], []
    for stem, unit in _units(root_path).items():
        built = build_record(stem, unit["finding"], unit["evidence"], root_path)
        records.append(built["record"])
        points.extend(built["points"])
        suites.extend(built["suites"])
    records.sort(key=lambda item: (item["date"], item["id"]), reverse=True)
    gaps = Counter(gap for record in records for gap in record["gaps"])
    return {
        "schema": INDEX_SCHEMA,
        "record_schema": RECORD_SCHEMA,
        "root": root_path.as_posix(),
        "not_recorded": NOT_RECORDED,
        "counts": {"records": len(records), "capacity_points": len(points), "quality_suites": len(suites),
                   "gaps": dict(sorted(gaps.items()))},
        "records": records,
        "capacity_points": points,
        "quality_suites": suites,
    }


def _cell(value: Any) -> str:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_table(index: Mapping[str, Any], link_prefix: str = "") -> str:
    """Render the record index as one Markdown table. Links are relative to the findings root."""
    lines = [
        "| Date | Result | Evidence | Decision | Model | Engine | Quantization | Hardware | Context | Concurrency | Gaps |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for record in index["records"]:
        links = record["links"]
        target = links["finding"] or links["evidence_index"]
        title = _cell(record["title"])
        result = f"[{title}]({link_prefix}{target})" if target else title
        subject, workload = record["subject"], record["workload"]
        lines.append("| " + " | ".join([
            record["date"], result, _cell(record["categories"] or "—"), _cell(record["decision"]["labels"]),
            _cell(subject["model"]), _cell(subject["engine"]), _cell(subject["quantization"]),
            _cell(subject["hardware"]), _cell(workload["context_tokens"]), _cell(workload["concurrency"]),
            _cell(record["gaps"] or "none"),
        ]) + " |")
    return "\n".join(lines) + "\n"


def render_card(record: Mapping[str, Any], link_prefix: str = "") -> str:
    """Render the standard record card shown at the top of a finding page."""
    subject, workload, links = record["subject"], record["workload"], record["links"]
    rows = [
        ("Evidence", _cell(record["categories"] or "—")),
        ("Decision", _cell(record["decision"]["labels"])),
        ("Model", _cell(subject["model"])),
        ("Engine", _cell(subject["engine"])),
        ("Quantization", _cell(subject["quantization"])),
        ("Hardware", _cell(subject["hardware"])),
        ("Context tokens", _cell(workload["context_tokens"])),
        ("Concurrency", _cell(workload["concurrency"])),
        ("Retained artifacts", f"{record['artifacts']['capacity']} capacity, "
                               f"{record['artifacts']['quality_suites']} quality suites"),
        ("Recorded gaps", _cell(record["gaps"] or "none")),
    ]
    body = ["| Field | Value |", "|---|---|", *(f"| {name} | {value} |" for name, value in rows)]
    if record["headline"]:
        body += ["", "**Headline results (from the decision summary):**", ""]
        for item in record["headline"]:
            unit = f" {item['unit']}" if item["unit"] else ""
            condition = f" ({_cell(item['condition'])})" if item["condition"] else ""
            body.append(f"- {_cell(item['metric'])}: {_cell(item['value'])}{unit}{condition}")
    if record["limitations"]:
        body += ["", f"**Main limitation:** {_cell(record['limitations'][0])}"]
    evidence_link = links["evidence_index"]
    if evidence_link:
        body += ["", f"[Retained evidence]({link_prefix}{evidence_link})"]
    indented = "\n".join(("    " + line) if line else "" for line in body)
    return f'??? abstract "Benchmark record ({RECORD_SCHEMA})"\n\n{indented}\n'


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving eval benchmark records", description=__doc__)
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Findings root to index.")
    parser.add_argument("--record", help="Show one record by finding id, for example 2026-10-17-slug.")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        index = build_index(args.root)
    except evidence.EvidenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.record:
        matches = [item for item in index["records"] if item["id"] == args.record]
        if not matches:
            print(f"error: no record named {args.record!r} under {args.root}", file=sys.stderr)
            return 2
        print(json.dumps(matches[0], indent=2) if args.format == "json" else render_card(matches[0]))
        return 0
    if args.format == "json":
        print(json.dumps(index, indent=2))
    else:
        counts = index["counts"]
        print(f"{counts['records']} records, {counts['capacity_points']} capacity points, "
              f"{counts['quality_suites']} quality suites. Gaps: {counts['gaps']}")
        print(render_table(index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
