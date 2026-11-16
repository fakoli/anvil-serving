"""Publishable catalog of serve-recipe registry files for the documentation site.

Each recipe registry under ``configs/`` becomes one catalog entry with its
recipes summarized, any benchmark records that measured the same served model,
and the exact file bytes for download. Publication fails closed on files that
contain host-specific GPU UUIDs or personal paths.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import serve_recipes

CATALOG_SCHEMA = "anvil-serving.recipe-catalog/v1"
_GPU_UUID = re.compile(r"GPU-[0-9A-Fa-f]{8}-[0-9A-Fa-f-]{27}")
_HOST_PATH = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\Users\\\\)")


class PublicationError(ValueError):
    """A recipe file is not safe to publish."""


def check_publishable(name: str, text: str) -> None:
    """Refuse real GPU UUIDs and personal host paths in a published recipe file."""
    for match in _GPU_UUID.finditer(text):
        if not serve_recipes._is_public_gpu_placeholder(match.group(0)):
            raise PublicationError(f"{name}: contains a host-specific GPU UUID")
    if _HOST_PATH.search(text):
        raise PublicationError(f"{name}: contains a personal host path")


def _measured_by(records_index: Mapping[str, Any] | None) -> dict[str, list[dict[str, str]]]:
    """Map served model name to the benchmark records that measured it."""
    if not records_index:
        return {}
    titles = {record["id"]: record for record in records_index.get("records", [])}
    found: dict[str, dict[str, dict[str, str]]] = {}
    rows = [*records_index.get("capacity_points", []), *records_index.get("quality_suites", [])]
    for row in rows:
        record = titles.get(row.get("record"))
        if not record or not row.get("model") or not record["links"].get("finding"):
            continue
        found.setdefault(row["model"], {})[record["id"]] = {
            "id": record["id"], "date": record["date"], "title": record["title"],
            "finding": record["links"]["finding"],
        }
    return {model: sorted(items.values(), key=lambda item: item["date"], reverse=True)
            for model, items in found.items()}


def _summary(recipe: Mapping[str, Any]) -> dict[str, Any]:
    serve = recipe.get("serve") or {}
    hardware = recipe.get("hardware") or {}
    download = recipe.get("download") or {}
    fit = recipe.get("fit") or {}
    return {
        "model": recipe.get("model"),
        "status": recipe.get("status"),
        "family": recipe.get("family"),
        "source": recipe.get("source"),
        "served_model_name": serve.get("served_model_name"),
        "engine": serve.get("engine"),
        "image": serve.get("image"),
        "quantization": serve.get("quantization"),
        "context_tokens": serve.get("context_tokens"),
        "gpu": hardware.get("gpu"),
        "arch": hardware.get("arch"),
        "vram_total_gb": hardware.get("vram_total_gb"),
        "repo": download.get("repo"),
        "revision": download.get("revision"),
        "suited": fit.get("suited"),
        "not_suited": fit.get("not_suited"),
        "measured": recipe.get("measured") or {},
        "sources": recipe.get("sources") or {},
    }


def build_catalog(paths: Iterable[Path], records_index: Mapping[str, Any] | None = None,
                  repo_root: Path | None = None) -> dict[str, Any]:
    """Summarize recipe registry files; files without ``[[recipe]]`` tables are skipped."""
    measured_by = _measured_by(records_index)
    paths = sorted(paths)
    repo_root = repo_root or (paths[0].parent.parent if paths else Path("."))
    loaded = []
    for path in paths:
        raw = path.read_bytes()
        recipes = serve_recipes.load_registry(path).get("recipe") or []
        if recipes:  # router and host configs share configs/ but hold no recipes
            loaded.append((path, raw, recipes))
    slugs = {path.stem for path, _, _ in loaded}
    files = []
    for path, raw, recipes in loaded:
        text = raw.decode("utf-8")
        check_publishable(path.name, text)
        summaries = [_summary(recipe) for recipe in recipes]
        for summary in summaries:
            summary["benchmarks"] = measured_by.get(summary["served_model_name"] or "", [])
            summary["sources"] = _resolve_sources(summary["sources"], repo_root, slugs)
        files.append({
            "name": path.name,
            "slug": path.stem,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "text": text,
            "recipes": summaries,
            "placeholder_gpu": bool(_GPU_UUID.search(text)),
        })
    return {"schema": CATALOG_SCHEMA, "files": files}


def _cell(value: Any) -> str:
    if value in (None, "", []):
        return "—"
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def _short(value: Any, limit: int = 70) -> str:
    text = _cell(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def hardware_group(gpu: Any) -> str:
    """Canonical heading such as "2× RTX PRO 6000" from free-text recipe hardware."""
    from .benchmarking.records import hardware_label

    if not isinstance(gpu, str) or not gpu.strip():
        return "Hardware not stated"
    count = re.match(r"\s*(\d+)\s*x\s", gpu, re.I)
    label = hardware_label(gpu)
    return f"{count.group(1)}× {label}" if count else label


def render_index_table(catalog: Mapping[str, Any], page_prefix: str = "") -> str:
    """One row per recipe, grouped by measured hardware, linking to its file page."""
    groups: dict[str, list[str]] = {}
    for item in catalog["files"]:
        for recipe in item["recipes"]:
            row = "| " + " | ".join([
                f"[{_cell(recipe['model'])}]({page_prefix}{item['slug']}.md)",
                _cell(recipe["engine"]),
                _short(recipe["quantization"], 60),
                _cell(recipe["context_tokens"]),
                _cell(recipe["status"]),
                str(len(recipe["benchmarks"])) if recipe["benchmarks"] else "—",
            ]) + " |"
            groups.setdefault(hardware_group(recipe["gpu"]), []).append(row)
    out = []
    for gpu in sorted(groups):
        out += [f"### {gpu}", "",
                "| Recipe | Engine | Quantization | Context | Status | Findings |",
                "|---|---|---|---|---|---|", *groups[gpu], ""]
    return "\n".join(out)


def _resolve_sources(sources: Mapping[str, Any], repo_root: Path, slugs: set[str]) -> list[dict[str, str | None]]:
    """Resolve source references to site-relative targets; anything unpublishable stays plain text."""
    resolved = []
    for kind in ("official", "community", "local"):
        for target in sources.get(kind) or []:
            if not isinstance(target, str):
                continue
            href = None
            if target.startswith("https://"):
                href = target
            elif target.startswith("docs/") and target.endswith(".md") and (repo_root / target).is_file():
                href = "docs:" + target.removeprefix("docs/")
            elif target.startswith("configs/") and Path(target).stem in slugs:
                href = Path(target).stem + ".md"
            resolved.append({"kind": kind, "label": target, "href": href})
    return resolved


def _source_lines(sources: list[dict[str, str | None]], docs_prefix: str) -> list[str]:
    lines = []
    for source in sources:
        href, label = source["href"], source["label"]
        if href is None:
            lines.append(f"- {source['kind']}: `{label}`")
        elif href.startswith("https://"):
            lines.append(f"- {source['kind']}: <{href}>")
        else:
            target = docs_prefix + href.removeprefix("docs:") if href.startswith("docs:") else href
            lines.append(f"- {source['kind']}: [{label}]({target})")
    return lines


def render_file_page(item: Mapping[str, Any], docs_prefix: str = "../") -> str:
    """Render one recipe file as a page. Links are relative to ``recipes/<slug>.md``."""
    first = item["recipes"][0]
    title = first["model"] if len(item["recipes"]) == 1 else item["name"]
    out = [
        f"# {title}", "",
        f"[Download {item['name']}](files/{item['name']}){{ .md-button .md-button--primary }}", "",
        f"`{item['name']}` · {item['bytes']:,} bytes · SHA-256 `{item['sha256']}`", "",
        '!!! note "A recipe is a reproducible starting point, not a promotion"',
        "    Status text and notes describe when the recipe was written. Loading a recipe",
        "    starts a loopback-only container and changes no route; qualify it with",
        "    `eval preflight` before use.",
    ]
    if item["placeholder_gpu"]:
        out += ["    GPU UUIDs in this file are public placeholders: replace them with the",
                "    UUIDs `host gpus` reports on your machine."]
    for recipe in item["recipes"]:
        if len(item["recipes"]) > 1:
            out += ["", f"## {_cell(recipe['model'])}"]
        out += ["", "| Field | Value |", "|---|---|"]
        rows = [
            ("Status", recipe["status"]), ("Family", recipe["family"]),
            ("Weights", f"{recipe['repo']} @ {recipe['revision']}" if recipe["repo"] else None),
            ("Engine", recipe["engine"]), ("Image", recipe["image"]),
            ("Quantization", recipe["quantization"]), ("Context tokens", recipe["context_tokens"]),
            ("Hardware", " · ".join(str(v) for v in (recipe["gpu"], recipe["arch"],
                                                     f"{recipe['vram_total_gb']} GB" if recipe["vram_total_gb"] else None) if v)),
            ("Served model name", recipe["served_model_name"]),
            ("Suited for", recipe["suited"]), ("Not suited for", recipe["not_suited"]),
            ("Origin", recipe["source"]),
        ]
        out += [f"| {name} | {_cell(value)} |" for name, value in rows if value not in (None, "", [])]
        measured = {key: value for key, value in recipe["measured"].items()
                    if isinstance(value, (int, float, str)) and not isinstance(value, bool)}
        if measured:
            out += ["", "**Recorded in the recipe:**", ""]
            out += [f"- `{key}`: {_cell(value)}" for key, value in measured.items()]
        if recipe["benchmarks"]:
            out += ["", "**Benchmark findings that measured this served model:**", ""]
            out += [f"- {b['date']} — [{_cell(b['title'])}]({docs_prefix}findings/{b['finding']})"
                    for b in recipe["benchmarks"]]
        sources = _source_lines(recipe["sources"], docs_prefix)
        if sources:
            out += ["", "**Sources:**", "", *sources]
    model = first["model"]
    out += [
        "", "## Use this recipe", "",
        "```bash",
        f"anvil-serving models recipes show '{model}' --registry ./{item['name']}",
        f"anvil-serving models recipes load '{model}' --container my-serve --registry ./{item['name']} --dry-run",
        "```", "",
        "Review the dry run, then repeat the `load` command with `--confirm`. See",
        f"[model lifecycle]({docs_prefix}MODEL-LIFECYCLE.md) for importing a recipe into your own registry.",
        "", "## File", "", "```toml", item["text"].rstrip("\n"), "```", "",
    ]
    return "\n".join(out)
