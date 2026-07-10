"""CLI for external benchmark ingestion, reporting, export, and comparison."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from . import compare, notebook, store
from .sources import ADAPTERS


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


def main(argv=None, *, prog: str = "anvil-serving benchmark external") -> int:
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Ingest, store, report, and compare external LLM inference benchmarks.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create the SQLite benchmark store")
    p_init.add_argument("--db", default=store.DEFAULT_DB)

    p_sources = sub.add_parser("sources", help="list known sources and latest snapshots")
    p_sources.add_argument("--db", default=store.DEFAULT_DB)

    p_fetch = sub.add_parser("fetch", help="fetch a source snapshot by URL and import it")
    p_fetch.add_argument("--source", required=True, choices=sorted(ADAPTERS))
    p_fetch.add_argument("--url", required=True)
    p_fetch.add_argument("--db", default=store.DEFAULT_DB)

    p_import = sub.add_parser("import", help="import a saved JSON, CSV, Markdown, or HTML snapshot")
    p_import.add_argument("--source", required=True, choices=sorted(ADAPTERS))
    p_import.add_argument("--file", required=True)
    p_import.add_argument("--db", default=store.DEFAULT_DB)

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
        req = urllib.request.Request(args.url, headers={"User-Agent": "anvil-serving/0.7"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            content_type = resp.headers.get("Content-Type")
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
        return _import_bytes(
            db=args.db,
            source=args.source,
            raw_bytes=path.read_bytes(),
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
        rows = store.export_rows(args.db, args.out)
        print("exported %d row(s) to %s" % (len(rows), args.out))
        return 0
    if args.cmd == "compare":
        result = compare.compare_local_to_external(args.db, args.local, gpu=args.gpu)
        print(compare.render_comparison(result))
        return 0
    if args.cmd == "notebook":
        if args.nb_cmd == "add":
            with open(args.evidence, encoding="utf-8") as f:
                evidence = json.load(f)
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
