"""MkDocs hook: publish benchmark records built from retained findings evidence.

At build time this hook
- writes ``benchmarks/results/index.json`` (the record index the results page charts);
- fills the ``<!-- benchmark-records:table -->`` marker on the results page; and
- puts the standard record card under the title of every dated finding page.

Nothing is written to ``docs/``; the generated views always match the committed evidence.
"""

from __future__ import annotations

import json
import sys
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

_index: dict | None = None
_by_finding: dict[str, dict] = {}


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
    if uri == RESULTS_PAGE and _index is not None:
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
            .replace(TABLE_MARKER, records.render_table(_index, link_prefix="../findings/"))
        )
    record = _by_finding.get(uri)
    if record is None:
        return markdown
    card = records.render_card(record)
    lines = markdown.split("\n")
    for position, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join([*lines[: position + 1], "", card, *lines[position + 1:]])
    return card + "\n" + markdown
