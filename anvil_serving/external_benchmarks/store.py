"""SQLite store for external benchmark snapshots and normalized rows."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import schema

DEFAULT_DB = ".anvil/benchmarks.sqlite"

KNOWN_SOURCES = {
    "millstone": {
        "kind": "llm-inference-benchmark",
        "homepage_url": "https://millstone.ai/",
        "notes": "Millstone AI LLM inference benchmark snapshots.",
    },
    "manual": {
        "kind": "manual-import",
        "homepage_url": None,
        "notes": "Operator-curated external benchmark rows.",
    },
}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fs_path(path: str | os.PathLike[str]) -> Path:
    """Return a local filesystem path, accepting Unix-style /tmp on Windows."""
    raw = os.fspath(path)
    if os.name == "nt":
        normalized = raw.replace("\\", "/")
        if normalized == "/tmp":
            return Path(tempfile.gettempdir())
        if normalized.startswith("/tmp/"):
            return Path(tempfile.gettempdir()) / normalized[len("/tmp/") :]
    return Path(raw)


def connect(db_path: str | os.PathLike[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(str(fs_path(db_path)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | os.PathLike[str] = DEFAULT_DB) -> dict[str, Any]:
    path = fs_path(db_path)
    if path.parent and str(path.parent) != "":
        path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(schema.DDL)
        for name, info in KNOWN_SOURCES.items():
            ensure_source(conn, name, **info)
    return {"db": str(path), "tables": list(schema.EXPECTED_TABLES)}


def ensure_source(
    conn: sqlite3.Connection,
    name: str,
    kind: str | None = None,
    homepage_url: str | None = None,
    notes: str | None = None,
) -> int:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO external_sources(name, kind, homepage_url, notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            kind=COALESCE(excluded.kind, external_sources.kind),
            homepage_url=COALESCE(excluded.homepage_url, external_sources.homepage_url),
            notes=COALESCE(excluded.notes, external_sources.notes)
        """,
        (name, kind, homepage_url, notes, now),
    )
    row = conn.execute("SELECT id FROM external_sources WHERE name = ?", (name,)).fetchone()
    return int(row["id"])


def raw_root_for_db(db_path: str | os.PathLike[str]) -> Path:
    parent = fs_path(db_path).parent
    if str(parent) in ("", "."):
        parent = Path(".anvil")
    return parent / "external-benchmarks" / "raw"


def _suffix(original_name: str | None, content_type: str | None) -> str:
    if original_name:
        suf = Path(original_name).suffix
        if suf:
            return suf
    ct = (content_type or "").lower()
    if "json" in ct:
        return ".json"
    if "csv" in ct:
        return ".csv"
    if "html" in ct:
        return ".html"
    if "markdown" in ct:
        return ".md"
    return ".txt"


def store_snapshot(
    db_path: str | os.PathLike[str],
    *,
    source_name: str,
    raw_bytes: bytes,
    original_name: str | None = None,
    source_url: str | None = None,
    content_type: str | None = None,
    parser_name: str,
    parser_version: str,
    fetched_at: str | None = None,
    imported_at: str | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    raw_root = raw_root_for_db(db_path)
    raw_root.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(raw_bytes).hexdigest()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    raw_path = raw_root / f"{source_name}-{stamp}-{sha[:12]}{_suffix(original_name, content_type)}"
    raw_path.write_bytes(raw_bytes)
    with connect(db_path) as conn:
        source_info = KNOWN_SOURCES.get(source_name, {})
        source_id = ensure_source(conn, source_name, **source_info)
        cur = conn.execute(
            """
            INSERT INTO external_snapshots(
                source_id, source_url, fetched_at, imported_at, raw_path, raw_sha256,
                content_type, parser_name, parser_version, parse_status, parse_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL)
            """,
            (
                source_id,
                source_url,
                fetched_at,
                imported_at,
                str(raw_path),
                sha,
                content_type,
                parser_name,
                parser_version,
            ),
        )
        snapshot_id = int(cur.lastrowid)
    return {
        "snapshot_id": snapshot_id,
        "raw_path": str(raw_path),
        "raw_sha256": sha,
    }


def update_snapshot_status(
    db_path: str | os.PathLike[str],
    snapshot_id: int,
    status: str,
    error: str | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE external_snapshots SET parse_status = ?, parse_error = ? WHERE id = ?",
            (status, error, snapshot_id),
        )


def insert_rows(
    db_path: str | os.PathLike[str],
    snapshot_id: int,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    now = utc_now()
    cols = schema.EXTERNAL_ROW_COLUMNS
    placeholders = ",".join("?" for _ in cols)
    sql = "INSERT INTO external_benchmark_rows(%s) VALUES (%s)" % (
        ",".join(cols),
        placeholders,
    )
    count = 0
    with connect(db_path) as conn:
        for row in rows:
            data = dict(row)
            data["snapshot_id"] = snapshot_id
            data.setdefault("created_at", now)
            conn.execute(sql, [data.get(c) for c in cols])
            count += 1
    return count


def list_sources(db_path: str | os.PathLike[str] = DEFAULT_DB) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.name, s.kind, s.homepage_url, s.notes,
                   x.id AS snapshot_id, x.imported_at, x.fetched_at,
                   x.parse_status, x.raw_sha256
            FROM external_sources s
            LEFT JOIN external_snapshots x ON x.id = (
                SELECT id FROM external_snapshots
                WHERE source_id = s.id
                ORDER BY COALESCE(imported_at, fetched_at, '') DESC, id DESC
                LIMIT 1
            )
            ORDER BY s.name
            """
        ).fetchall()
    return [dict(r) for r in rows]


def query_rows(
    db_path: str | os.PathLike[str] = DEFAULT_DB,
    *,
    gpu: str | None = None,
    model: str | None = None,
    source: str | None = None,
    top: int | None = None,
) -> list[dict[str, Any]]:
    from .normalize import normalize_gpu_name, normalize_model_identity

    init_db(db_path)
    clauses = []
    params: list[Any] = []
    if gpu:
        clauses.append("r.gpu_model = ?")
        params.append(normalize_gpu_name(gpu))
    if model:
        ident = normalize_model_identity(model)
        clauses.append("(r.model_id_normalized = ? OR r.model_family = ?)")
        params.extend([ident["model_id_normalized"], ident["model_family"]])
    if source:
        clauses.append("s.name = ?")
        params.append(source)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    limit = "LIMIT ?" if top else ""
    if top:
        params.append(int(top))
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT r.*, s.name AS source_name, x.source_url, x.imported_at, x.fetched_at
            FROM external_benchmark_rows r
            JOIN external_snapshots x ON x.id = r.snapshot_id
            JOIN external_sources s ON s.id = x.source_id
            {where}
            ORDER BY (r.throughput_tok_s IS NULL), r.throughput_tok_s DESC,
                     (r.decode_tok_s IS NULL), r.decode_tok_s DESC, r.id
            {limit}
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def export_rows(
    db_path: str | os.PathLike[str],
    out_path: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    rows = query_rows(db_path)
    if out_path:
        out = fs_path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    return rows


def upsert_serve_fingerprint(
    db_path: str | os.PathLike[str], fingerprint: Mapping[str, Any]
) -> int:
    init_db(db_path)
    cols = (
        "model_id",
        "served_model_name",
        "engine",
        "engine_version",
        "quantization",
        "precision",
        "gpu_model",
        "gpu_count",
        "context_limit",
        "kv_cache_dtype",
        "reasoning_parser",
        "tool_call_parser",
        "serve_flags_json",
        "fingerprint_sha256",
        "created_at",
    )
    data = dict(fingerprint)
    data.setdefault("created_at", utc_now())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO serve_fingerprints(%s)
            VALUES (%s)
            ON CONFLICT(fingerprint_sha256) DO NOTHING
            """
            % (",".join(cols), ",".join("?" for _ in cols)),
            [data.get(c) for c in cols],
        )
        row = conn.execute(
            "SELECT id FROM serve_fingerprints WHERE fingerprint_sha256 = ?",
            (data["fingerprint_sha256"],),
        ).fetchone()
        return int(row["id"])


def insert_comparison(
    db_path: str | os.PathLike[str],
    *,
    serve_fingerprint_id: int,
    external_row_id: int,
    local_run_id: str | None,
    metric: str,
    local_value: float | None,
    external_value: float | None,
    notes: str | None = None,
) -> int:
    if local_value is None or external_value in (None, 0):
        delta_abs = None
        delta_pct = None
    else:
        delta_abs = float(local_value) - float(external_value)
        delta_pct = (delta_abs / float(external_value)) * 100.0
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO benchmark_comparisons(
                serve_fingerprint_id, external_row_id, local_run_id, metric,
                local_value, external_value, delta_abs, delta_pct, notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                serve_fingerprint_id,
                external_row_id,
                local_run_id,
                metric,
                local_value,
                external_value,
                delta_abs,
                delta_pct,
                notes,
                utc_now(),
            ),
        )
        return int(cur.lastrowid)
