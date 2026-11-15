#!/usr/bin/env python3
"""Assemble retained, partial serving-comparison evidence from accepted projections.

This private helper performs no collection.  It only accepts a projection when the
frozen-plan cell, its native receipt, and its collector acceptance record agree.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from anvil_serving.benchmarking.artifacts import (
    SERVING_METRIC_KEYS,
    atomic_write_json,
    build_measured_evidence,
    evidence_file_reference,
    validate_cross_suite_evidence,
)

PROFILE = "serving-comparison/v1"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def reference(path: Path, root: Path) -> dict[str, Any]:
    return evidence_file_reference(str(path.resolve()), root=str(root))


def accepted_projection(root: Path, plan: dict[str, Any], cell_id: str) -> tuple[dict[str, Any], Path] | None:
    projection_path = root / "analysis" / "projections" / f"{cell_id}.json"
    if not projection_path.is_file():
        return None
    projection = load(projection_path)
    cell = plan["required_cells"][cell_id]
    if (projection.get("schema") != "serving-cell-projection/v2"
            or projection.get("profile") != PROFILE
            or projection.get("cell_id") != cell_id
            or projection.get("arm") != cell["arm"]
            or projection.get("unavailable") != []):
        return None
    accepted = root / "telemetry" / cell["arm"] / cell_id / "cell-summary.json"
    if not accepted.is_file() or load(accepted).get("coverage_passed") is not True:
        return None
    metrics = projection.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(SERVING_METRIC_KEYS):
        return None
    if any(not isinstance(record, dict) or record.get("status") != "measured" for record in metrics.values()):
        return None
    if not isinstance(projection.get("measurement"), dict):
        return None
    supplementals = projection.get("supplementals")
    if not isinstance(supplementals, dict) or set(supplementals) != {"runtime_model_load_memory", "engine_phase_histograms", "queue_waiting_by_reason", "request_identity"}:
        return None
    return {"measurement": projection["measurement"], "metrics": metrics, "supplementals": supplementals}, projection_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("analysis/partial-serving-comparison-evidence.json"))
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    if args.validate:
        evidence = load(output)
        checked = validate_cross_suite_evidence(evidence, artifact_root=str(root), verify_files=True)
        coverage = checked["metrics_coverage"]
        print(json.dumps({"artifact": str(output), "status": coverage["status"], "accepted_cells": sorted(coverage["cells"]), "missing_required_metrics": len(coverage["missing_required_metrics"])}, sort_keys=True))
        return 0

    plan_path = root / "plans" / "comparison-plan.json"
    plan = load(plan_path)
    if plan.get("profile") != PROFILE or plan.get("frozen_before_requests") is not True:
        raise ValueError("comparison plan is not frozen serving-comparison/v1")
    required = list(plan.get("required_cells", {}))
    if not required:
        raise ValueError("comparison plan has no required cells")
    cells: dict[str, dict[str, Any]] = {}
    projections: list[Path] = []
    finished: list[str] = []
    for cell_id in required:
        candidate = accepted_projection(root, plan, cell_id)
        if candidate is None:
            continue
        cell, projection_path = candidate
        cells[cell_id] = {"measurement": cell["measurement"], "metrics": cell["metrics"]}
        projections.append(projection_path)
        native_path = root / cell["metrics"]["ttft_ms"]["evidence"][0]["path"]
        native = load(native_path)
        if isinstance(native.get("finished_at"), str):
            finished.append(native["finished_at"])
    gaps = [f"{cell_id}.{metric}" for cell_id in required if cell_id not in cells for metric in sorted(SERVING_METRIC_KEYS)]
    coverage = {
        "profile": PROFILE,
        "status": "partial",
        "frozen_plan": reference(plan_path, root),
        "required_cells": required,
        "cells": cells,
        "missing_required_metrics": gaps,
    }
    supplemental_cells = {
        cell_id: (candidate[0]["supplementals"] if (candidate := accepted_projection(root, plan, cell_id)) else {
            name: {"status": "not_collected", "reason": "no accepted fresh complete frozen-plan projection"}
            for name in ("runtime_model_load_memory", "engine_phase_histograms", "queue_waiting_by_reason", "request_identity")
        })
        for cell_id in required
    }
    comparisons = {}
    for cell_id, cell in plan["required_cells"].items():
        if cell["arm"] != "baseline": continue
        mate_id = next((other_id for other_id, other in plan["required_cells"].items() if other["arm"] == "candidate" and all(other[key] == cell[key] for key in ("workload", "context_tokens", "concurrency", "requests"))), None)
        if not mate_id or cell_id not in cells or mate_id not in cells:
            comparisons[cell_id] = {"status": "not_collected", "reason": "both accepted arm cells are required before latency comparability"}; continue
        left, right = supplemental_cells[cell_id]["request_identity"], supplemental_cells[mate_id]["request_identity"]
        if left.get("status") != "measured" or right.get("status") != "measured":
            comparisons[cell_id] = {"status": "blocked", "reason": "request identity unavailable"}; continue
        keys = ("namespace", "context_seed", "request_indices", "prompt_tokens_ordered", "request_canaries")
        comparisons[cell_id] = {"status": "measured" if all(left["value"][key] == right["value"][key] for key in keys) else "blocked", "reason": "matching native identity vectors required; no normalization", "wire_prompt_proof": "not retained; reconstruction is not transmitted-byte proof"}
    supplemental_coverage = {
        "schema": "private-serving-supplementals/v1",
        "status": "partial",
        "completion_rule": "comparison cannot be complete until every frozen cell has all supplemental records and each baseline/candidate pair has measured matching native request identity vectors",
        "cells": supplemental_cells,
        "latency_comparability": comparisons,
        "validator_scope": "product validator verifies metrics_coverage only; this private supplemental block is retained for independent review",
    }
    # This is an in-progress aggregate, deliberately not a completed comparison.
    created_at = max(finished) if finished else plan.get("frozen_at")
    if not isinstance(created_at, str):
        raise ValueError("no deterministic timestamp available")
    stage_evidence = [reference(plan_path, root), *(reference(path, root) for path in projections)]
    identities = {
        "model": {"baseline": plan["arms"]["baseline"]["model"], "candidate": plan["arms"]["candidate"]["model"]},
        "served_model": {"arms": {name: arm["revision"] for name, arm in plan["arms"].items()}},
        "runtime": {"arms": {name: arm["runtime"] for name, arm in plan["arms"].items()}},
        "image": {"arms": {name: arm["image"] for name, arm in plan["arms"].items()}},
        "hardware": {"arms": {name: arm["hardware"] for name, arm in plan["arms"].items()}},
        "topology": {"kind": "two-arm controlled serving comparison"},
        "context": {"frozen_matrix_cells": len(required)},
        "concurrency": {"frozen_matrix_cells": len(required)},
        "harnesses": {"projection": "project_cell.py", "coverage": "assemble_partial_coverage.py"},
        "dataset": {"workloads": sorted({cell["workload"] for cell in plan["required_cells"].values()})},
    }
    evidence = build_measured_evidence(
        run={"run_id": "glm53-serving-performance-phase2-partial", "ownership_id": "controlled-ab", "suite": "capacity", "profile": PROFILE, "spec_sha256": reference(plan_path, root)["sha256"]},
        identities=identities,
        stages=[{"sequence": 0, "name": "phase2-serving-matrix", "status": "incomplete", "evidence": stage_evidence}],
        completeness="incomplete",
        summary={"completed": False, "status": "in_progress", "accepted_cells": sorted(cells), "pending_cells": [item for item in required if item not in cells]},
        created_at=created_at,
        artifact_root=str(root),
        metrics_coverage=coverage,
    )
    evidence["private_campaign_supplementals"] = supplemental_coverage
    adjudication = load(root / "plans" / "controlled-diagnostic-adjudication.json")
    dispositions = {}
    for cell_id, cell in plan["required_cells"].items():
        if cell_id in cells:
            dispositions[cell_id] = {"status": "accepted", "reason": "all required serving metrics measured and native-bound"}
        elif cell_id == "baseline-controlled-ctx10240-c1":
            dispositions[cell_id] = {"status": "failed", "reason": "retained original controlled deadline/failure"}
        elif cell["workload"] == "synthetic-repeated-code":
            dispositions[cell_id] = {"status": "blocked", "reason": adjudication["original_controlled_remaining"]}
        else:
            dispositions[cell_id] = {"status": "pending", "reason": "no accepted fresh complete frozen-plan projection"}
    evidence["private_current_dispositions"] = {
        "terminal": False,
        "original_matrix_status": "partial",
        "accepted_expected_terminal": 24,
        "accepted": len(cells),
        "cells": dispositions,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(output), evidence)
    print(json.dumps({"artifact": str(output), "status": evidence["metrics_coverage"]["status"], "accepted_cells": sorted(cells), "pending_cells": len(required) - len(cells), "missing_required_metrics": len(evidence["metrics_coverage"]["missing_required_metrics"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
