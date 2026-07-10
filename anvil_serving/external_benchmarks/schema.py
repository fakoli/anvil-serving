"""SQLite schema for external benchmark snapshots and normalized rows."""
from __future__ import annotations

EXTERNAL_ROW_COLUMNS = (
    "snapshot_id",
    "source_row_id",
    "report_url",
    "model_name_raw",
    "model_id_normalized",
    "model_family",
    "model_variant",
    "modality",
    "engine",
    "engine_version",
    "precision",
    "quantization",
    "gpu_model",
    "gpu_count",
    "vram_gb",
    "hardware_notes",
    "context_tokens",
    "max_context_tokens",
    "concurrency",
    "prompt_tokens",
    "output_tokens",
    "ttft_ms",
    "decode_tok_s",
    "throughput_tok_s",
    "peak_throughput_tok_s",
    "success_rate",
    "capacity_users_32k",
    "methodology_notes",
    "raw_metrics_json",
    "created_at",
)

EXPECTED_TABLES = (
    "external_sources",
    "external_snapshots",
    "external_benchmark_rows",
    "serve_fingerprints",
    "benchmark_comparisons",
    "bakeoff_runs",
    "bakeoff_verdicts",
)

DDL = """
CREATE TABLE IF NOT EXISTS external_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT,
    homepage_url TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    source_url TEXT,
    fetched_at TEXT,
    imported_at TEXT,
    raw_path TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    content_type TEXT,
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    parse_error TEXT,
    FOREIGN KEY(source_id) REFERENCES external_sources(id)
);

CREATE TABLE IF NOT EXISTS external_benchmark_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    source_row_id TEXT,
    report_url TEXT,
    model_name_raw TEXT,
    model_id_normalized TEXT,
    model_family TEXT,
    model_variant TEXT,
    modality TEXT,
    engine TEXT,
    engine_version TEXT,
    precision TEXT,
    quantization TEXT,
    gpu_model TEXT,
    gpu_count INTEGER,
    vram_gb REAL,
    hardware_notes TEXT,
    context_tokens INTEGER,
    max_context_tokens INTEGER,
    concurrency INTEGER,
    prompt_tokens INTEGER,
    output_tokens INTEGER,
    ttft_ms REAL,
    decode_tok_s REAL,
    throughput_tok_s REAL,
    peak_throughput_tok_s REAL,
    success_rate REAL,
    capacity_users_32k REAL,
    methodology_notes TEXT,
    raw_metrics_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES external_snapshots(id)
);

CREATE TABLE IF NOT EXISTS serve_fingerprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT,
    served_model_name TEXT,
    engine TEXT,
    engine_version TEXT,
    quantization TEXT,
    precision TEXT,
    gpu_model TEXT,
    gpu_count INTEGER,
    context_limit INTEGER,
    kv_cache_dtype TEXT,
    reasoning_parser TEXT,
    tool_call_parser TEXT,
    serve_flags_json TEXT,
    fingerprint_sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serve_fingerprint_id INTEGER NOT NULL,
    external_row_id INTEGER NOT NULL,
    local_run_id TEXT,
    metric TEXT NOT NULL,
    local_value REAL,
    external_value REAL,
    delta_abs REAL,
    delta_pct REAL,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(serve_fingerprint_id) REFERENCES serve_fingerprints(id),
    FOREIGN KEY(external_row_id) REFERENCES external_benchmark_rows(id)
);

CREATE TABLE IF NOT EXISTS bakeoff_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    task TEXT NOT NULL,
    hardware TEXT NOT NULL,
    model TEXT,
    serve_fingerprint_id INTEGER,
    started_at TEXT,
    ttft_p50_ms REAL,
    e2e_p50_ms REAL,
    voice_latency_ms REAL,
    usable_context_tokens INTEGER,
    tool_call_passed INTEGER,
    session_recall_passed INTEGER,
    intelligence_pass_rate REAL,
    thinking_mode TEXT,
    failures_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT,
    evidence_path TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(serve_fingerprint_id) REFERENCES serve_fingerprints(id)
);

CREATE TABLE IF NOT EXISTS bakeoff_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    baseline_run_id TEXT,
    rubric_json TEXT NOT NULL,
    total_score REAL,
    verdict TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bakeoff_runs_key
    ON bakeoff_runs(candidate_id, config_id, task, hardware, started_at);

CREATE INDEX IF NOT EXISTS idx_external_rows_gpu
    ON external_benchmark_rows(gpu_model);
CREATE INDEX IF NOT EXISTS idx_external_rows_model
    ON external_benchmark_rows(model_id_normalized, model_family);
CREATE INDEX IF NOT EXISTS idx_external_rows_engine
    ON external_benchmark_rows(engine);
CREATE INDEX IF NOT EXISTS idx_external_snapshots_source
    ON external_snapshots(source_id, imported_at, fetched_at);
"""

