"""MkDocs hook: publish every serve-recipe registry under configs/ as a page and a download.

At build time this hook adds, for each recipe file,
- ``recipes/files/<name>.toml`` with the exact committed bytes;
- ``recipes/<name>.md``, a generated page with the recipe summary, the benchmark
  findings that measured the same served model, usage commands, and the file;
plus ``recipes/files/anvil-serving-recipes.zip`` and the catalog table on
``recipes/index.md``. A file with a host-specific GPU UUID or personal path
fails the build.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anvil_serving import recipe_catalog  # noqa: E402
from anvil_serving.benchmarking import records  # noqa: E402

INDEX_PAGE = "recipes/index.md"
TABLE_MARKER = "<!-- recipe-catalog:table -->"
SUMMARY_MARKER = "<!-- recipe-catalog:summary -->"
ZIP_NAME = "anvil-serving-recipes.zip"

_catalog: dict | None = None


def on_files(files, config):
    from mkdocs.structure.files import File

    global _catalog
    docs = Path(config["docs_dir"])
    _catalog = recipe_catalog.build_catalog(
        (REPO_ROOT / "configs").glob("*.toml"),
        records.build_index(docs / "findings"),
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in _catalog["files"]:
            bundle.writestr(item["name"], item["text"])
            files.append(File.generated(config, f"recipes/files/{item['name']}", content=item["text"]))
            files.append(File.generated(config, f"recipes/{item['slug']}.md",
                                        content=recipe_catalog.render_file_page(item, docs_prefix="../")))
    files.append(File.generated(config, f"recipes/files/{ZIP_NAME}", content=archive.getvalue()))
    return files


def on_page_markdown(markdown, page, config, files):
    if page.file.src_uri != INDEX_PAGE or _catalog is None:
        return markdown
    recipes = sum(len(item["recipes"]) for item in _catalog["files"])
    measured = sum(1 for item in _catalog["files"] for recipe in item["recipes"] if recipe["benchmarks"])
    summary = (
        f"**{recipes} recipes** in **{len(_catalog['files'])} files**; {measured} link to a benchmark "
        f"finding that measured the same served model. "
        f"[Download all recipe files (zip)](files/{ZIP_NAME}){{ .md-button }}"
    )
    return markdown.replace(SUMMARY_MARKER, summary).replace(
        TABLE_MARKER, recipe_catalog.render_index_table(_catalog)
    )
