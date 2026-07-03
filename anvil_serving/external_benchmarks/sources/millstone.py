"""Millstone AI external benchmark parser.

Millstone is treated as a snapshot source, not a stable API. The adapter accepts
JSON, CSV, Markdown tables, HTML tables, and a small regex fallback.
"""
from __future__ import annotations

import csv
import html
import json
import re
from html.parser import HTMLParser
from io import StringIO
from typing import Any, Mapping

from ..normalize import normalize_external_row
from .base import ParseResult, SourceAdapter


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_cell = False
        self._row: list[str] = []
        self._table: list[list[str]] = []
        self._cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
            self._table = []
        elif self._in_table and tag == "tr":
            self._row = []
        elif self._in_table and tag in {"td", "th"}:
            self._in_cell = True
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._in_table and tag in {"td", "th"} and self._in_cell:
            self._row.append(html.unescape(" ".join(self._cell)).strip())
            self._in_cell = False
        elif self._in_table and tag == "tr":
            if any(c.strip() for c in self._row):
                self._table.append(self._row)
        elif tag == "table" and self._in_table:
            self.tables.append(self._table)
            self._in_table = False


class MillstoneAdapter(SourceAdapter):
    name = "millstone"
    parser_name = "millstone"
    parser_version = "1"

    def parse(
        self,
        raw_bytes: bytes,
        *,
        content_type: str | None = None,
        source_url: str | None = None,
        original_name: str | None = None,
    ) -> ParseResult:
        text = raw_bytes.decode("utf-8-sig", "replace")
        defaults = {"report_url": source_url}
        warnings: list[str] = []
        fmt = self._detect_format(text, content_type, original_name)
        if fmt == "json":
            source_rows, meta = _rows_from_json(text)
            defaults.update(meta)
        elif fmt == "csv":
            source_rows = list(csv.DictReader(StringIO(text)))
        elif fmt == "html":
            source_rows = _rows_from_html(text)
        elif fmt == "markdown":
            source_rows = _rows_from_markdown(text)
        else:
            source_rows = _rows_from_regex(text)
            warnings.append("used fallback regex extraction")
        if not source_rows:
            raise ValueError("no benchmark rows found in snapshot")
        rows = []
        for i, row in enumerate(source_rows, start=1):
            row_defaults = dict(defaults)
            row_defaults.setdefault("row_id", str(i))
            norm = normalize_external_row(row, row_defaults)
            if norm.get("model_name_raw") and norm.get("gpu_model"):
                rows.append(norm)
        if not rows:
            raise ValueError("benchmark rows were found but none had model and GPU fields")
        return ParseResult(
            rows=rows,
            warnings=warnings,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )

    def _detect_format(
        self, text: str, content_type: str | None, original_name: str | None
    ) -> str:
        name = (original_name or "").lower()
        ct = (content_type or "").lower()
        stripped = text.lstrip()
        if "json" in ct or name.endswith(".json") or stripped.startswith(("{", "[")):
            return "json"
        if "csv" in ct or name.endswith(".csv"):
            return "csv"
        if "html" in ct or name.endswith((".html", ".htm")) or "<table" in text.lower():
            return "html"
        if name.endswith(".md") or _looks_like_markdown_table(text):
            return "markdown"
        if "," in text and "\n" in text:
            return "csv"
        return "text"


def _rows_from_json(text: str) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    data = json.loads(text)
    meta: dict[str, Any] = {}
    if isinstance(data, dict):
        for key in ("report_url", "url", "methodology", "methodology_notes"):
            if key in data:
                out_key = "methodology_notes" if key == "methodology" else key
                meta[out_key] = data[key]
        for key in ("benchmarks", "results", "rows", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, Mapping)], meta
        for value in data.values():
            if isinstance(value, dict):
                for key in ("benchmarks", "results", "rows", "data"):
                    nested = value.get(key)
                    if isinstance(nested, list):
                        return [r for r in nested if isinstance(r, Mapping)], meta
    if isinstance(data, list):
        return [r for r in data if isinstance(r, Mapping)], meta
    return [], meta


def _rows_from_html(text: str) -> list[dict[str, str]]:
    parser = _TableParser()
    parser.feed(text)
    rows: list[dict[str, str]] = []
    for table in parser.tables:
        if len(table) < 2:
            continue
        headers = table[0]
        for cells in table[1:]:
            if len(cells) < 2:
                continue
            row = {headers[i]: cells[i] for i in range(min(len(headers), len(cells)))}
            if row:
                rows.append(row)
    return rows


def _looks_like_markdown_table(text: str) -> bool:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines[:-1]):
        if "|" in line and _is_markdown_separator(lines[i + 1]):
            return True
    return False


def _is_markdown_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    cells = [cell for cell in cells if cell]
    if not cells:
        return False
    return all("-" in cell and set(cell) <= {"-", ":"} for cell in cells)


def _rows_from_markdown(text: str) -> list[dict[str, str]]:
    lines = [ln.strip() for ln in text.splitlines() if "|" in ln]
    rows: list[dict[str, str]] = []
    for i, line in enumerate(lines[:-1]):
        if _is_markdown_separator(lines[i + 1]):
            headers = [c.strip() for c in line.strip("|").split("|")]
            for body in lines[i + 2 :]:
                if _is_markdown_separator(body):
                    continue
                cells = [c.strip() for c in body.strip("|").split("|")]
                if len(cells) < 2:
                    break
                rows.append({headers[j]: cells[j] for j in range(min(len(headers), len(cells)))})
            break
    return rows


def _rows_from_regex(text: str) -> list[dict[str, str]]:
    rows = []
    pattern = re.compile(
        r"(?P<model>[A-Za-z0-9_.:/-]+).*?"
        r"(?P<gpu>RTX\s+(?:PRO\s+)?(?:6000|5090)[A-Za-z0-9\s]*)"
        r".*?(?P<throughput>\d+(?:\.\d+)?)\s*(?:tok/s|tokens/s)",
        re.IGNORECASE,
    )
    for i, match in enumerate(pattern.finditer(text), start=1):
        rows.append(
            {
                "id": str(i),
                "model": match.group("model"),
                "gpu": match.group("gpu"),
                "throughput_tok_s": match.group("throughput"),
            }
        )
    return rows
