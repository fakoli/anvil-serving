"""CLI for external benchmark ingestion, reporting, export, and comparison."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
from pathlib import Path

from . import compare, notebook, store
from .sources import ADAPTERS

MAX_IMPORT_BYTES = 16 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 30


def _read_bounded(path: str | Path, *, limit: int | None = None) -> bytes:
    limit = MAX_IMPORT_BYTES if limit is None else limit
    source = Path(path)
    size = source.stat().st_size
    if size > limit:
        raise ValueError("input exceeds %d byte limit: %s" % (limit, source))
    with source.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("input exceeds %d byte limit: %s" % (limit, source))
    return payload


def _fetch_bounded(url: str) -> tuple[bytes, str | None]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("fetch URL must be an absolute http:// or https:// URL")
    req = urllib.request.Request(url, headers={"User-Agent": "anvil-serving/0.13"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
        declared = resp.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > MAX_IMPORT_BYTES:
                    raise ValueError("response exceeds %d byte limit" % MAX_IMPORT_BYTES)
            except ValueError as exc:
                if "exceeds" in str(exc):
                    raise
        raw = resp.read(MAX_IMPORT_BYTES + 1)
        if len(raw) > MAX_IMPORT_BYTES:
            raise ValueError("response exceeds %d byte limit" % MAX_IMPORT_BYTES)
        return raw, resp.headers.get("Content-Type")


def _validate_export_target(path: str) -> Path:
    target = Path(path)
    parent = target.parent if str(target.parent) else Path(".")
    if not parent.exists() or not parent.is_dir():
        raise ValueError("export parent directory does not exist: %s" % parent)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("export target must be a regular file: %s" % target)
    if not os.access(parent, os.W_OK):
        raise ValueError("export parent directory is not writable: %s" % parent)
    return target


def _load_evidence(path: str) -> dict:
    try:
        value = json.loads(_read_bounded(path).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cannot read bakeoff evidence %s: %s" % (path, exc)) from exc
    if not isinstance(value, dict):
        raise ValueError("bakeoff evidence must contain a JSON object")
    store.validate_bakeoff_evidence(value)
    return value


def _adapter(name: str):
    if name not in ADAPTERS:
        raise SystemExit(f"unknown source {name!r}; known: {', '.join(sorted(ADAPTERS))}")
    return ADAPTERS[name]


def _print_row_table(rows: list[dict], *, markdown: bool = False) -> None:
    headers = [
        "source",
        "model",
        "gpu",
        "engine",
        "quant",
        "ctx",
        "conc",
        "tok/s",
        "ttft_ms",
    ]
    data = [
        [
            r.get("source_name") or "",
            r.get("model_id_normalized") or "",
            r.get("gpu_model") or "",
            r.get("engine") or "",
            r.get("quantization") or r.get("precision") or "",
            r.get("context_tokens") or "",
            r.get("concurrency") or "",
            r.get("throughput_tok_s") or "",
            r.get("ttft_ms") or "",
        ]
        for r in rows
    ]
    if markdown:
        print("| " + " | ".join(headers) + " |")
        print("|" + "|".join("---" for _ in headers) + "|")
        for row in data:
            print("| " + " | ".join(str(v) for v in row) + " |")
        return
    widths = [len(h) for h in headers]
    for row in data:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(str(value)))
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in data:
        print("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))


def _import_bytes(
    *,
    db: str,
    source: str,
    raw_bytes: bytes,
    original_name: str | None,
    source_url: str | None,
    content_type: str | None,
    fetched: bool,
) -> int:
    adapter = _adapter(source)
    stamp = store.utc_now()
    snap = store.store_snapshot(
        db,
        source_name=source,
        raw_bytes=raw_bytes,
        original_name=original_name,
        source_url=source_url,
        content_type=content_type,
        parser_name=adapter.parser_name,
        parser_version=adapter.parser_version,
        fetched_at=stamp if fetched else None,
        imported_at=None if fetched else stamp,
    )
    try:
        result = adapter.parse(
            raw_bytes,
            content_type=content_type,
            source_url=source_url,
            original_name=original_name,
        )
    except Exception as exc:
        store.update_snapshot_status(db, snap["snapshot_id"], "failed", str(exc))
        print(
            "stored raw snapshot but parsing failed: %s\nraw: %s"
            % (exc, snap["raw_path"]),
            file=sys.stderr,
        )
        return 1
    try:
        count = store.insert_rows(db, snap["snapshot_id"], result.rows)
    except Exception as exc:
        store.update_snapshot_status(db, snap["snapshot_id"], "failed", str(exc))
        print(
            "stored raw snapshot but importing rows failed: %s\nraw: %s"
            % (exc, snap["raw_path"]),
            file=sys.stderr,
        )
        return 1
    status = "parsed" if count else "empty"
    store.update_snapshot_status(db, snap["snapshot_id"], status, None)
    print(
        "imported %d row(s) from %s snapshot %s" % (count, source, snap["snapshot_id"])
    )
    print("raw: " + snap["raw_path"])
    if result.warnings:
        for warning in result.warnings:
            print("warning: " + warning)
    return 0


def main(argv=None, *, prog: str = "anvil-serving eval benchmark external") -> int:
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Ingest, store, report, and compare external LLM inference benchmarks.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create the SQLite benchmark store")
    p_init.add_argument("--db", default=store.DEFAULT_DB)
    p_init.add_argument("--dry-run", action="store_true")

    p_sources = sub.add_parser("sources", help="list known sources and latest snapshots")
    p_sources.add_argument("--db", default=store.DEFAULT_DB)

    p_fetch = sub.add_parser("fetch", help="fetch a source snapshot by URL and import it")
    p_fetch.add_argument("--source", required=True, choices=sorted(ADAPTERS))
    p_fetch.add_argument("--url", required=True)
    p_fetch.add_argument("--db", default=store.DEFAULT_DB)
    p_fetch.add_argument("--dry-run", action="store_true")

    p_import = sub.add_parser("import", help="import a saved JSON, CSV, Markdown, or HTML snapshot")
    p_import.add_argument("--source", required=True, choices=sorted(ADAPTERS))
    p_import.add_argument("--file", required=True)
    p_import.add_argument("--db", default=store.DEFAULT_DB)
    p_import.add_argument("--dry-run", action="store_true")

    p_list = sub.add_parser("list", help="list normalized benchmark rows")
    p_list.add_argument("--gpu")
    p_list.add_argument("--model")
    p_list.add_argument("--source")
    p_list.add_argument("--top", type=int, default=20)
    p_list.add_argument("--db", default=store.DEFAULT_DB)

    p_report = sub.add_parser("report", help="emit a benchmark report")
    p_report.add_argument("--gpu")
    p_report.add_argument("--model")
    p_report.add_argument("--source")
    p_report.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_report.add_argument("--db", default=store.DEFAULT_DB)

    p_export = sub.add_parser("export", help="export normalized rows")
    p_export.add_argument("--format", choices=["json"], default="json")
    p_export.add_argument("--out", required=True)
    p_export.add_argument("--db", default=store.DEFAULT_DB)
    p_export.add_argument("--dry-run", action="store_true")

    p_compare = sub.add_parser("compare", help="compare a local Anvil benchmark JSON")
    p_compare.add_argument("--local", required=True)
    p_compare.add_argument("--gpu")
    p_compare.add_argument("--db", default=store.DEFAULT_DB)

    p_nb = sub.add_parser(
        "notebook",
        help="record/list/render bakeoff candidate runs (the model-bakeoff notebook)",
    )
    nb_sub = p_nb.add_subparsers(dest="nb_cmd", required=True)
    nb_add = nb_sub.add_parser("add", help="record a fast-tier-bakeoff/v1 evidence JSON")
    nb_add.add_argument("--evidence", required=True, help="path to a bakeoff evidence JSON")
    nb_add.add_argument("--task", required=True)
    nb_add.add_argument("--hardware", required=True)
    nb_add.add_argument("--db", default=store.DEFAULT_DB)
    nb_add.add_argument("--dry-run", action="store_true")
    nb_list = nb_sub.add_parser("list", help="list recorded runs (latest per candidate)")
    nb_list.add_argument("--task")
    nb_list.add_argument("--hardware")
    nb_list.add_argument("--all", action="store_true", help="show full append history")
    nb_list.add_argument("--format", choices=["table", "json"], default="table")
    nb_list.add_argument("--db", default=store.DEFAULT_DB)
    nb_render = nb_sub.add_parser("render", help="render the comparison matrix + verdicts")
    nb_render.add_argument("--task")
    nb_render.add_argument("--hardware")
    nb_render.add_argument("--baseline", help="candidate_id to compare others against")
    nb_render.add_argument("--format", choices=["markdown", "json"], default="markdown")
    nb_render.add_argument("--db", default=store.DEFAULT_DB)

    args = ap.parse_args(argv)
    if args.cmd == "init":
        if args.dry_run:
            print("would initialize external benchmark DB: " + str(store.fs_path(args.db)))
            return 0
        result = store.init_db(args.db)
        print("initialized external benchmark DB: " + result["db"])
        return 0
    if args.cmd == "sources":
        rows = store.list_sources(args.db)
        for row in rows:
            latest = row.get("imported_at") or row.get("fetched_at") or "never"
            status = row.get("parse_status") or "-"
            print(f"{row['name']}\t{row.get('kind') or ''}\tlatest={latest}\tstatus={status}")
        return 0
    if args.cmd == "fetch":
        if args.dry_run:
            _fetch_url = urllib.parse.urlsplit(args.url)
            if _fetch_url.scheme not in {"http", "https"} or not _fetch_url.netloc:
                print("fetch URL must be an absolute http:// or https:// URL", file=sys.stderr)
                return 2
            print("would fetch and import %s from %s into %s" % (
                args.source, args.url, store.fs_path(args.db),
            ))
            return 0
        try:
            raw, content_type = _fetch_bounded(args.url)
        except (OSError, ValueError) as exc:
            print("fetch failed: %s" % exc, file=sys.stderr)
            return 1
        return _import_bytes(
            db=args.db,
            source=args.source,
            raw_bytes=raw,
            original_name=args.url,
            source_url=args.url,
            content_type=content_type,
            fetched=True,
        )
    if args.cmd == "import":
        path = Path(args.file)
        try:
            raw = _read_bounded(path)
        except (OSError, ValueError) as exc:
            print("import failed: %s" % exc, file=sys.stderr)
            return 1
        if args.dry_run:
            print("would import %s from %s into %s (%d bytes)" % (
                args.source, path, store.fs_path(args.db), len(raw),
            ))
            return 0
        return _import_bytes(
            db=args.db,
            source=args.source,
            raw_bytes=raw,
            original_name=str(path),
            source_url=str(path),
            content_type=None,
            fetched=False,
        )
    if args.cmd == "list":
        rows = store.query_rows(
            args.db, gpu=args.gpu, model=args.model, source=args.source, top=args.top
        )
        _print_row_table(rows)
        return 0
    if args.cmd == "report":
        rows = store.query_rows(args.db, gpu=args.gpu, model=args.model, source=args.source)
        if args.format == "json":
            print(json.dumps(rows, indent=2, sort_keys=True))
        else:
            _print_row_table(rows, markdown=True)
        return 0
    if args.cmd == "export":
        try:
            target = _validate_export_target(args.out)
        except ValueError as exc:
            print("export refused: %s" % exc, file=sys.stderr)
            return 2
        if args.dry_run:
            print("would export normalized rows from %s to %s" % (
                store.fs_path(args.db), target,
            ))
            return 0
        rows = store.export_rows(args.db, target)
        print("exported %d row(s) to %s" % (len(rows), args.out))
        return 0
    if args.cmd == "compare":
        result = compare.compare_local_to_external(args.db, args.local, gpu=args.gpu)
        print(compare.render_comparison(result))
        return 0
    if args.cmd == "notebook":
        if args.nb_cmd == "add":
            try:
                evidence = _load_evidence(args.evidence)
            except (OSError, ValueError) as exc:
                print("notebook add refused: %s" % exc, file=sys.stderr)
                return 2
            if args.dry_run:
                print("would record bakeoff run %s for task=%s hardware=%s in %s" % (
                    evidence.get("run_id"), args.task, args.hardware,
                    store.fs_path(args.db),
                ))
                return 0
            row_id = store.record_bakeoff_run(
                args.db, evidence, task=args.task, hardware=args.hardware,
                evidence_path=args.evidence,
            )
            print("recorded bakeoff run %s (row %d)" % (evidence.get("run_id"), row_id))
            return 0
        if args.nb_cmd == "list":
            rows = store.list_bakeoff_runs(
                args.db, task=args.task, hardware=args.hardware,
                latest_per_candidate=not args.all,
            )
            if args.format == "json":
                print(json.dumps(rows, indent=2, sort_keys=True))
            else:
                for r in rows:
                    print("%s	%s	task=%s	hw=%s	ttft=%s	e2e=%s" % (
                        r.get("candidate_id"), r.get("config_id"), r.get("task"),
                        r.get("hardware"), r.get("ttft_p50_ms"), r.get("e2e_p50_ms")))
            return 0
        if args.nb_cmd == "render":
            rows = store.list_bakeoff_runs(
                args.db, task=args.task, hardware=args.hardware,
                latest_per_candidate=True,
            )
            if args.format == "json":
                out = [{"run": r, "rubric": notebook.score_run(r),
                        "verdict": notebook.verdict(r)} for r in rows]
                print(json.dumps(out, indent=2, sort_keys=True, default=str))
            else:
                print(notebook.render_markdown(
                    rows, task=args.task, hardware=args.hardware,
                    baseline_candidate=args.baseline), end="")
            return 0
        return 2
    return 2
