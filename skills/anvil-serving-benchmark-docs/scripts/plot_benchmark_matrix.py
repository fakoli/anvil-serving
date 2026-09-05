#!/usr/bin/env python3
"""Render deterministic benchmark charts from retained native evidence.

The input manifest selects exact artifacts and metric paths.  This keeps chart
generation separate from benchmark execution and prevents prose or manually
copied numbers from becoming the source of truth.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


SCHEMA = "anvil-serving.benchmark-graph-manifest/v1"
DATA_SCHEMA = "anvil-serving.benchmark-graph-data/v1"
EVIDENCE_SCHEMAS = frozenset(
    {
        "anvil-serving.benchmark/v1",
        "anvil-serving.benchmark-evidence/v1",
        "anvil-serving.capacity-aggregate/v1",
        "anvil-serving.media-qualification/v1",
        "kernel-tune-manifest/v1",
        "multimodal-benchmark-evidence/v1",
        "stt-benchmark-evidence/v1",
        "voice-benchmark-evidence/v1",
    }
)
SVG_SCHEMA_MARKER = f'data-anvil-schema="{DATA_SCHEMA}"'
LEGACY_SVG_MARKER = (
    "Values are derived from the retained JSON artifacts named in the graph data file."
)
COLORS = ("#4cc9f0", "#f72585", "#f9c74f", "#90be6d", "#b8c0ff")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _relative_child(base: Path, relative: str, *, label: str) -> tuple[Path, str]:
    normalized = relative.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(relative)
    if (
        not relative
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError(f"{label} must be a canonical relative path: {relative}")
    candidate = (base / Path(*posix_path.parts)).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes manifest directory: {relative}") from exc
    return candidate, posix_path.as_posix()


def _output_value(manifest: dict[str, Any], key: str, default: str) -> str:
    value = manifest.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"manifest {key} must be a non-empty relative path")
    return value


def _require_suffix(path: Path, suffix: str, *, label: str) -> None:
    if path.suffix.lower() != suffix:
        raise ValueError(f"{label} must use the {suffix} suffix: {path.name}")


def _assert_generated_target(path: Path, *, kind: str) -> None:
    if not path.exists():
        return
    if not path.is_file():
        raise ValueError(f"{kind} output target is not a file: {path}")
    if kind == "SVG":
        text = path.read_text(encoding="utf-8")
        is_current = SVG_SCHEMA_MARKER in text
        is_legacy = (
            text.lstrip().startswith("<svg ")
            and 'role="img"' in text
            and LEGACY_SVG_MARKER in text
        )
        if not (is_current or is_legacy):
            raise ValueError(f"refusing to overwrite unmarked SVG output: {path}")
        return
    document = _read_json(path)
    if document.get("schema") != DATA_SCHEMA:
        raise ValueError(f"refusing to overwrite unmarked graph data output: {path}")


def _metric(document: dict[str, Any], dotted_path: str) -> float:
    value: Any = document
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"metric path not found: {dotted_path}")
        value = value[part]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"metric is not numeric: {dotted_path}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"metric is not finite: {dotted_path}")
    return number


def _fmt(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def _load(
    manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], frozenset[Path]]:
    manifest = _read_json(manifest_path)
    if manifest.get("schema") != SCHEMA:
        raise ValueError(f"manifest schema must be {SCHEMA}")
    charts = manifest.get("charts")
    if not isinstance(charts, list) or not charts:
        raise ValueError("manifest charts must be a non-empty array")

    base = manifest_path.parent
    rendered: list[dict[str, Any]] = []
    artifact_paths: set[Path] = set()
    for chart in charts:
        if not isinstance(chart, dict):
            raise ValueError("each chart must be an object")
        metric_path = chart.get("metric")
        series_list = chart.get("series")
        if not isinstance(metric_path, str) or not metric_path:
            raise ValueError("each chart needs a metric path")
        if not isinstance(series_list, list) or not series_list:
            raise ValueError("each chart needs at least one series")
        output_series: list[dict[str, Any]] = []
        for series in series_list:
            label = series.get("label") if isinstance(series, dict) else None
            points = series.get("points") if isinstance(series, dict) else None
            if not isinstance(label, str) or not isinstance(points, list) or not points:
                raise ValueError("each series needs a label and non-empty points")
            output_points: list[dict[str, Any]] = []
            for point in points:
                if not isinstance(point, dict):
                    raise ValueError("each point must be an object")
                artifact_rel = point.get("artifact")
                x_label = point.get("x")
                if not isinstance(artifact_rel, str) or not isinstance(x_label, str):
                    raise ValueError("each point needs string artifact and x values")
                artifact_path, artifact_relative = _relative_child(
                    base, artifact_rel, label="point artifact"
                )
                _require_suffix(artifact_path, ".json", label="point artifact")
                artifact = _read_json(artifact_path)
                artifact_schema = artifact.get("schema")
                if artifact_schema not in EVIDENCE_SCHEMAS:
                    raise ValueError(
                        f"unsupported point artifact schema: {artifact_schema!r}"
                    )
                artifact_paths.add(artifact_path)
                output_points.append(
                    {
                        "x": x_label,
                        "value": _metric(artifact, metric_path),
                        "artifact": artifact_relative,
                        "artifact_sha256": hashlib.sha256(
                            artifact_path.read_bytes()
                        ).hexdigest(),
                    }
                )
            output_series.append({"label": label, "points": output_points})
        rendered.append(
            {
                "title": chart.get("title", metric_path),
                "metric": metric_path,
                "x_label": chart.get("x_label", ""),
                "y_label": chart.get("y_label", metric_path),
                "series": output_series,
            }
        )

    data = {
        "schema": DATA_SCHEMA,
        "title": manifest.get("title", "Benchmark matrix"),
        "subtitle": manifest.get("subtitle", ""),
        "metric_semantics": manifest.get("metric_semantics", {}),
        "charts": rendered,
    }
    return manifest, data, frozenset(artifact_paths)


def _svg(data: dict[str, Any]) -> str:
    charts = data["charts"]
    columns = 2
    rows = math.ceil(len(charts) / columns)
    width = 1280
    header = 118
    panel_w = 600
    panel_h = 330
    gap_x = 28
    gap_y = 30
    left = 26
    height = header + rows * panel_h + max(0, rows - 1) * gap_y + 32
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" {SVG_SCHEMA_MARKER} width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{html.escape(str(data["title"]))}</title>',
        f'<desc id="desc">{html.escape(str(data.get("subtitle", "")))}</desc>',
        "<style>text{font-family:Inter,Segoe UI,Arial,sans-serif}.title{font-size:27px;font-weight:700}.sub{font-size:14px;fill:#9fb0c8}.panel-title{font-size:17px;font-weight:650}.axis{font-size:11px;fill:#aab7c8}.value{font-size:10px;font-weight:600}.legend{font-size:12px}.grid{stroke:#31425d;stroke-width:1}.line{fill:none;stroke-width:3}.dot{stroke:#0b1220;stroke-width:2}</style>",
        f'<rect width="{width}" height="{height}" fill="#0b1220"/>',
        f'<text x="{left}" y="42" class="title" fill="#f4f7fb">{html.escape(str(data["title"]))}</text>',
        f'<text x="{left}" y="70" class="sub">{html.escape(str(data.get("subtitle", "")))}</text>',
        '<text x="26" y="96" class="sub">Values are derived from the retained JSON artifacts named in the graph data file.</text>',
    ]

    for index, chart in enumerate(charts):
        col = index % columns
        row = index // columns
        px = left + col * (panel_w + gap_x)
        py = header + row * (panel_h + gap_y)
        plot_x = px + 62
        plot_y = py + 58
        plot_w = panel_w - 88
        plot_h = panel_h - 116
        all_points = [p for s in chart["series"] for p in s["points"]]
        categories: list[str] = []
        for point in all_points:
            if point["x"] not in categories:
                categories.append(point["x"])
        max_value = max(float(p["value"]) for p in all_points)
        axis_max = max_value * 1.12 if max_value > 0 else 1.0

        parts.extend(
            [
                f'<rect x="{px}" y="{py}" width="{panel_w}" height="{panel_h}" rx="14" fill="#111c2e" stroke="#263853"/>',
                f'<text x="{px + 18}" y="{py + 28}" class="panel-title" fill="#f4f7fb">{html.escape(str(chart["title"]))}</text>',
                f'<text x="{px + 18}" y="{py + 48}" class="axis">{html.escape(str(chart["y_label"]))}</text>',
            ]
        )
        for tick in range(5):
            frac = tick / 4
            y = plot_y + plot_h - frac * plot_h
            value = axis_max * frac
            parts.append(f'<line x1="{plot_x}" y1="{y:.1f}" x2="{plot_x + plot_w}" y2="{y:.1f}" class="grid"/>')
            parts.append(f'<text x="{plot_x - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{html.escape(_fmt(value))}</text>')

        denominator = max(1, len(categories) - 1)
        x_positions = {
            category: plot_x + (position / denominator) * plot_w
            for position, category in enumerate(categories)
        }
        for category, x in x_positions.items():
            parts.append(f'<text x="{x:.1f}" y="{plot_y + plot_h + 20}" text-anchor="middle" class="axis">{html.escape(category)}</text>')
        parts.append(f'<text x="{plot_x + plot_w / 2:.1f}" y="{plot_y + plot_h + 43}" text-anchor="middle" class="axis">{html.escape(str(chart["x_label"]))}</text>')

        for series_index, series in enumerate(chart["series"]):
            color = COLORS[series_index % len(COLORS)]
            coords = []
            for point in series["points"]:
                x = x_positions[point["x"]]
                y = plot_y + plot_h - (float(point["value"]) / axis_max) * plot_h
                coords.append((x, y, float(point["value"])))
            if len(coords) > 1:
                points_attr = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in coords)
                parts.append(f'<polyline points="{points_attr}" class="line" stroke="{color}"/>')
            for x, y, value in coords:
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" class="dot" fill="{color}"/>')
                parts.append(f'<text x="{x:.1f}" y="{y - 9:.1f}" text-anchor="middle" class="value" fill="{color}">{html.escape(_fmt(value))}</text>')
            legend_x = px + 20 + series_index * 215
            legend_y = py + panel_h - 17
            parts.append(f'<circle cx="{legend_x}" cy="{legend_y - 4}" r="5" fill="{color}"/>')
            parts.append(f'<text x="{legend_x + 11}" y="{legend_y}" class="legend" fill="#dce6f3">{html.escape(str(series["label"]))}</text>')

    parts.append("</svg>\n")
    return "".join(parts)


def render(manifest_path: Path) -> tuple[Path, Path]:
    manifest_path = manifest_path.resolve()
    manifest, data, artifact_paths = _load(manifest_path)
    base = manifest_path.parent
    output, _ = _relative_child(
        base,
        _output_value(manifest, "output", "benchmark-matrix.svg"),
        label="SVG output",
    )
    data_output, _ = _relative_child(
        base,
        _output_value(manifest, "data_output", "benchmark-graph-data.json"),
        label="graph data output",
    )
    if output == data_output:
        raise ValueError("SVG output and graph data output paths must differ")
    protected_paths = artifact_paths | {manifest_path}
    if output in protected_paths:
        raise ValueError("SVG output must not overwrite the manifest or a source artifact")
    if data_output in protected_paths:
        raise ValueError(
            "graph data output must not overwrite the manifest or a source artifact"
        )
    _require_suffix(output, ".svg", label="SVG output")
    _require_suffix(data_output, ".json", label="graph data output")
    _assert_generated_target(output, kind="SVG")
    _assert_generated_target(data_output, kind="graph data")
    output.parent.mkdir(parents=True, exist_ok=True)
    data_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_svg(data), encoding="utf-8", newline="\n")
    data_output.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return output, data_output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render an accessible SVG benchmark matrix from retained JSON evidence."
    )
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    svg_path, data_path = render(args.manifest)
    print(json.dumps({"svg": str(svg_path), "data": str(data_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
