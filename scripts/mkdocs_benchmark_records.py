"""MkDocs hook: publish benchmark records built from retained findings evidence.

At build time this hook
- writes ``benchmarks/results/index.json`` (the record index the results page charts);
- fills the ``<!-- benchmark-records:table -->`` marker on the results page with
  one filterable row per record;
- fills ``<!-- benchmark-records:latest:N -->`` on any page with the N newest
  records as cards;
- puts the standard record header under the title of every dated finding page; and
- keeps raw evidence bundles and internal planning pages out of site search.

Nothing is written to ``docs/``; the generated views always match the committed
evidence, so a new finding appears everywhere in the same shape without hand edits.
"""

from __future__ import annotations

import json
import re
import sys
from html import escape
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anvil_serving.benchmarking import records  # noqa: E402

INDEX_URI = "benchmarks/results/index.json"
RESULTS_PAGE = "benchmarks/results.md"
TABLE_MARKER = "<!-- benchmark-records:table -->"
SUMMARY_MARKER = "<!-- benchmark-records:summary -->"
DOWNLOAD_MARKER = "<!-- benchmark-records:download -->"
SCRIPTS_MARKER = "<!-- benchmark-records:scripts -->"
LATEST_MARKER = re.compile(r"<!-- benchmark-records:latest:(\d+) -->")
# Generated and rendered-URL targets live here, not in the Markdown source, so the
# tracked-link checker only sees files that exist in the repository.
DOWNLOAD = "[Download the full index as JSON](results/index.json)."
SCRIPTS = "\n".join(
    f'<script src="{src}"></script>'
    for src in (
        "https://cdn.jsdelivr.net/npm/vega@5.30.0",
        "https://cdn.jsdelivr.net/npm/vega-lite@5.21.0",
        "https://cdn.jsdelivr.net/npm/vega-embed@6.26.0",
        "../../javascripts/benchmark-results.js",
    )
)
# Raw evidence bundles and internal planning records stay reachable by link but
# do not compete with guides and findings in search results.
SEARCH_EXCLUDED = re.compile(r"^(findings/[^/]+-evidence/|prds/|design/|research/)")
FACTS = (
    ("Model", "subject", "model"),
    ("Engine", "subject", "engine"),
    ("Quantization", "subject", "quantization"),
    ("Hardware", "subject", "hardware"),
    ("Context tokens", "workload", "context_tokens"),
    ("Concurrency", "workload", "concurrency"),
)
_TONES = (
    ("bad", ("reject", "fail", "invalid")),
    ("warn", ("no-promotion", "no promotion", "rollback", "retain", "pending", "incomplete")),
    ("info", ("challenger", "candidate", "scout")),
    ("good", ("current", "qualif", "accept", "promot", "verified")),
)

_index: dict | None = None
_by_finding: dict[str, dict] = {}


def _text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _labels(value) -> list[str]:
    """Decision labels as a clean list; findings write them as lists or prose."""
    items = value if isinstance(value, list) else str(value).split(",")
    return [label for label in (str(item).strip(" `") for item in items) if label]


def _tone(label: str) -> str:
    lowered = label.lower()
    for tone, needles in _TONES:
        if any(needle in lowered for needle in needles):
            return f" av-badge--{tone}"
    return ""


def _badges(record, limit: int | None = None) -> str:
    not_recorded = _index["not_recorded"] if _index else records.NOT_RECORDED
    badges = [f'<span class="av-badge">{escape(name)}</span>' for name in record["categories"] or []]
    for label in _labels(record["decision"]["labels"]):
        if label != not_recorded:
            badges.append(f'<span class="av-badge{_tone(label)}">{escape(label)}</span>')
    return "".join(badges[:limit] if limit else badges)


def render_record(record, href) -> str:
    """The standard header shown under the title of every finding with a record."""
    not_recorded = _index["not_recorded"] if _index else records.NOT_RECORDED
    facts = []
    for name, group, key in FACTS:
        value = _text(record[group][key])
        gap = ' class="is-gap"' if value == not_recorded else ""
        facts.append(f"<div><dt>{name}</dt><dd{gap}>{escape(value)}</dd></div>")
    artifacts = record["artifacts"]
    facts.append(
        f"<div><dt>Retained artifacts</dt><dd>{artifacts['capacity']} capacity · "
        f"{artifacts['quality_suites']} quality suites</dd></div>"
    )
    parts = [
        '<div class="av-record">',
        f'<div class="av-record__head"><span class="av-record__date">{escape(record["date"])}</span>'
        f"{_badges(record)}</div>",
        f'<dl class="av-facts">{"".join(facts)}</dl>',
    ]
    if record["headline"]:
        items = []
        for item in record["headline"]:
            unit = f" {escape(_text(item['unit']))}" if item["unit"] else ""
            condition = f"<span>{escape(_text(item['condition']))}</span>" if item["condition"] else ""
            items.append(f"<li>{escape(_text(item['metric']))}: <b>{escape(_text(item['value']))}{unit}</b>{condition}</li>")
        parts.append(f'<ul class="av-record__metrics">{"".join(items)}</ul>')
    notes = []
    if record["limitations"]:
        notes.append(f"<strong>Main limitation:</strong> {escape(_text(record['limitations'][0]))}")
    if record["gaps"]:
        notes.append(f"<strong>Not recorded:</strong> {escape(_text(record['gaps']).replace('_', ' '))}")
    if record["links"]["evidence_index"]:
        notes.append(f'<a href="{escape(href(record["links"]["evidence_index"]))}">Retained evidence</a>')
    if notes:
        parts.append(f'<p class="av-record__note">{" · ".join(notes)}</p>')
    parts.append("</div>")
    return "\n".join(parts)


def render_table(href) -> str:
    """Every record as one table row, with the same badges as the cards."""
    not_recorded = _index["not_recorded"]
    head = "".join(f"<th>{name}</th>" for name in ("Date", "Result", "Evidence and decision", *(fact[0] for fact in FACTS)))
    rows = []
    for record in _index["records"]:
        target = record["links"]["finding"] or record["links"]["evidence_index"]
        title = escape(record["title"])
        result = f'<a href="{escape(href(target))}">{title}</a>' if target else title
        cells = [f"<td>{escape(record['date'])}</td>", f"<td>{result}</td>", f"<td>{_badges(record)}</td>"]
        for _name, group, key in FACTS:
            value = _text(record[group][key])
            gap = ' class="is-gap"' if value == not_recorded else ""
            cells.append(f"<td{gap}>{escape(value)}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        '<input class="av-filter" type="search" placeholder="Filter records: model, hardware, decision…" '
        'aria-label="Filter records" data-av-filter="benchmark-records">\n'
        '<div class="md-typeset__scrollwrap"><div class="md-typeset__table">'
        f'<table class="av-table" id="benchmark-records"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div></div>"
    )


def render_latest(count: int, href) -> str:
    """The newest records as cards, for landing pages."""
    not_recorded = _index["not_recorded"]
    cards = []
    for record in [item for item in _index["records"] if item["links"]["finding"]][:count]:
        hardware = _text(record["subject"]["hardware"])
        kicker = record["date"] + (f" · {hardware}" if hardware != not_recorded else "")
        cards.append(
            f'<a class="av-card" href="{escape(href(record["links"]["finding"]))}">'
            f'<span class="av-card__kicker">{escape(kicker)}</span>'
            f'<span class="av-card__title">{escape(record["title"])}</span>'
            f'<span class="av-card__foot">{_badges(record, limit=4)}</span>'
            "</a>"
        )
    return '<div class="av-grid av-grid--wide">' + "".join(cards) + "</div>"


def on_files(files, config):
    from mkdocs.structure.files import File

    global _index
    _index = records.build_index(Path(config["docs_dir"]) / "findings")
    _by_finding.clear()
    for record in _index["records"]:
        if record["links"]["finding"]:
            _by_finding["findings/" + record["links"]["finding"]] = record
    files.append(File.generated(config, INDEX_URI, content=json.dumps(_index, separators=(",", ":"))))
    return files


def on_page_markdown(markdown, page, config, files):
    uri = page.file.src_uri
    if SEARCH_EXCLUDED.match(uri):
        page.meta.setdefault("search", {})["exclude"] = True
    if _index is None:
        return markdown

    def href(finding_relative: str) -> str:
        target = files.get_file_from_path("findings/" + finding_relative)
        if target is None:
            return finding_relative
        return target.url_relative_to(page.file)

    markdown = LATEST_MARKER.sub(lambda match: render_latest(int(match.group(1)), href), markdown)
    if uri == RESULTS_PAGE:
        counts = _index["counts"]
        summary = (
            f"**{counts['records']} records**, **{counts['capacity_points']} capacity measurements**, "
            f"and **{counts['quality_suites']} quality suite results**, rebuilt from retained evidence "
            "every time this site is built."
        )
        return (
            markdown.replace(SUMMARY_MARKER, summary)
            .replace(DOWNLOAD_MARKER, DOWNLOAD)
            .replace(SCRIPTS_MARKER, SCRIPTS)
            .replace(TABLE_MARKER, render_table(href))
        )
    record = _by_finding.get(uri)
    if record is None:
        return markdown
    header = render_record(record, href)
    lines = markdown.split("\n")
    for position, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join([*lines[: position + 1], "", header, "", *lines[position + 1:]])
    return header + "\n\n" + markdown
