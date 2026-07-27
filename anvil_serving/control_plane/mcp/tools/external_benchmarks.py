"""Explicit external benchmarks MCP tool family."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from ....benchmarking.artifacts import (
    metric_delta as _metric_delta,
)
from ..arguments import (
    bounded_int_arg as _bounded_int_arg,
    bounded_integer_schema as _bounded_integer_schema,
    schema as _schema,
    str_arg as _str_arg,
)
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from ..evidence import (
    resolve_benchmark_artifact_path as _resolve_benchmark_artifact_path,
)


def _external_bench_db_path(db_path: str) -> str:
    return _resolve_benchmark_artifact_path(db_path)[0]


def _external_bench_existing_db_path(db_path: str, *, required: bool = True) -> tuple[str, bool]:
    db = _external_bench_db_path(db_path)
    if os.path.isfile(db):
        return db, True
    if required:
        raise ToolError(
            "external_bench_db_not_found",
            "external benchmark DB not found; run benchmark external init/import first",
            {"db": db},
        )
    return db, False


def _external_bench_known_sources() -> list[dict[str, Any]]:
    from ....external_benchmarks import store

    rows = []
    for name, info in sorted(store.KNOWN_SOURCES.items()):
        rows.append(
            {
                "name": name,
                "kind": info.get("kind"),
                "homepage_url": info.get("homepage_url"),
                "notes": info.get("notes"),
                "snapshot_id": None,
                "imported_at": None,
                "fetched_at": None,
                "parse_status": None,
                "raw_sha256": None,
            }
        )
    return rows


def _external_bench_read_error(exc: sqlite3.Error, db: str) -> ToolError:
    return ToolError(
        "bad_external_bench_db",
        "could not read external benchmark DB",
        {"db": db, "error": str(exc)},
    )


def _external_bench_filters(args: dict, *, default_top: int = 20) -> tuple[str, str, str, int]:
    gpu = _str_arg(args, "gpu", "")
    model = _str_arg(args, "model", "")
    source = _str_arg(args, "source", "")
    top = _bounded_int_arg(args, "top", default_top, min_value=1, max_value=1000)
    return gpu, model, source, top


def _external_bench_envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "advisory_only": True,
        "promotion_quality_evidence": False,
        **data,
    }


def _external_bench_match(item: Any, local: dict[str, Any] | None = None) -> dict[str, Any]:
    score, mismatches, row = item
    match = {
        "score": score,
        "mismatches": list(mismatches),
        "row": dict(row),
    }
    if local is not None:
        match["deltas"] = {
            "throughput_tok_s": _metric_delta(
                local.get("throughput_tok_s"), row.get("throughput_tok_s")
            ),
            "ttft_ms": _metric_delta(local.get("ttft_ms"), row.get("ttft_ms")),
        }
    return match


def tool_external_bench_sources(args: dict) -> dict:
    from ....external_benchmarks import store

    db, exists = _external_bench_existing_db_path(
        _str_arg(args, "db", store.DEFAULT_DB), required=False
    )
    if exists:
        try:
            rows = store.list_sources(db, initialize=False)
        except sqlite3.Error as exc:
            raise _external_bench_read_error(exc, db)
    else:
        rows = _external_bench_known_sources()
    return _ok(_external_bench_envelope({"db": db, "db_exists": exists, "sources": rows}))


def tool_external_bench_list(args: dict) -> dict:
    from ....external_benchmarks import store

    db, _ = _external_bench_existing_db_path(_str_arg(args, "db", store.DEFAULT_DB))
    gpu, model, source, top = _external_bench_filters(args, default_top=20)
    try:
        rows = store.query_rows(
            db,
            gpu=gpu or None,
            model=model or None,
            source=source or None,
            top=top,
            initialize=False,
        )
    except sqlite3.Error as exc:
        raise _external_bench_read_error(exc, db)
    return _ok(
        _external_bench_envelope(
            {
                "db": db,
                "filters": {
                    "gpu": gpu or None,
                    "model": model or None,
                    "source": source or None,
                    "top": top,
                },
                "rows": rows,
                "count": len(rows),
            }
        )
    )


def tool_external_bench_report(args: dict) -> dict:
    from ....external_benchmarks import store

    db, _ = _external_bench_existing_db_path(_str_arg(args, "db", store.DEFAULT_DB))
    gpu, model, source, top = _external_bench_filters(args, default_top=100)
    try:
        rows = store.query_rows(
            db,
            gpu=gpu or None,
            model=model or None,
            source=source or None,
            top=top,
            initialize=False,
        )
    except sqlite3.Error as exc:
        raise _external_bench_read_error(exc, db)
    return _ok(
        _external_bench_envelope(
            {
                "db": db,
                "filters": {
                    "gpu": gpu or None,
                    "model": model or None,
                    "source": source or None,
                    "top": top,
                },
                "columns": [
                    "source_name",
                    "model_id_normalized",
                    "gpu_model",
                    "engine",
                    "quantization",
                    "precision",
                    "context_tokens",
                    "concurrency",
                    "throughput_tok_s",
                    "ttft_ms",
                ],
                "rows": rows,
                "count": len(rows),
            }
        )
    )


def tool_external_bench_compare(args: dict) -> dict:
    from ....external_benchmarks import compare, store

    db, _ = _external_bench_existing_db_path(_str_arg(args, "db", store.DEFAULT_DB))
    local_path = _resolve_benchmark_artifact_path(_str_arg(args, "local", required=True))[0]
    if not os.path.isfile(local_path):
        raise ToolError(
            "local_benchmark_not_found", "local benchmark artifact not found", {"local": local_path}
        )
    gpu = _str_arg(args, "gpu", "")
    top = _bounded_int_arg(args, "top", 5, min_value=1, max_value=100)
    try:
        result = compare.compare_local_to_external(
            db, local_path, gpu=gpu or None, top=top, record=False, initialize=False
        )
    except sqlite3.Error as exc:
        raise _external_bench_read_error(exc, db)
    local = dict(result["local"])
    chosen = result.get("chosen")
    nearest = [_external_bench_match(item, local) for item in (result.get("nearest") or [])]
    data = {
        "db": db,
        "local_path": local_path,
        "gpu": gpu or None,
        "local": local,
        "fingerprint": result["fingerprint"],
        "exact": bool(result.get("exact")),
        "warnings": list(result.get("warnings") or []),
        "chosen": _external_bench_match(chosen, local) if chosen else None,
        "nearest": nearest,
        "comparison": {
            "match_type": "exact" if result.get("exact") else ("nearest" if chosen else "none"),
            "has_external_prior": bool(chosen),
        },
    }
    return _ok(_external_bench_envelope(data))


FAMILY = ToolFamily(
    name="external_benchmarks",
    tools={
        "external_bench_sources": {
            "description": "List known external benchmark sources and latest snapshots as advisory-only priors.",
            "inputSchema": _schema(
                {
                    "db": {"type": "string"},
                }
            ),
            "handler": tool_external_bench_sources,
        },
        "external_bench_list": {
            "description": "List normalized external benchmark rows as advisory-only priors.",
            "inputSchema": _schema(
                {
                    "db": {"type": "string"},
                    "gpu": {"type": "string"},
                    "model": {"type": "string"},
                    "source": {"type": "string"},
                    "top": _bounded_integer_schema(1, 1000, 20),
                }
            ),
            "handler": tool_external_bench_list,
        },
        "external_bench_report": {
            "description": "Return a structured external benchmark report as advisory-only priors.",
            "inputSchema": _schema(
                {
                    "db": {"type": "string"},
                    "gpu": {"type": "string"},
                    "model": {"type": "string"},
                    "source": {"type": "string"},
                    "top": _bounded_integer_schema(1, 1000, 100),
                }
            ),
            "handler": tool_external_bench_report,
        },
        "external_bench_compare": {
            "description": "Compare a local benchmark artifact against external advisory priors.",
            "inputSchema": _schema(
                {
                    "db": {"type": "string"},
                    "local": {"type": "string"},
                    "gpu": {"type": "string"},
                    "top": _bounded_integer_schema(1, 100, 5),
                },
                required=["local"],
            ),
            "handler": tool_external_bench_compare,
        },
    },
)
