"""Adapter for llmrequirements.com's machine-readable advisory database.

The site publishes model metadata and coarse build-level speed buckets in
``/data/db.json``.  Those numbers are useful recipe priors, but they are not
hardware-matched benchmark runs for each model.  This adapter reproduces the
site's Q4 bucket selection and labels every emitted row accordingly.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from ..normalize import normalize_gpu_name, normalize_model_identity
from .base import ParseResult, SourceAdapter


_ORDER = ("8b", "14b", "30b", "70b", "120b_moe", "235b_moe", "671b_moe", "1t_moe")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _params_bucket(model: Mapping[str, Any]) -> str:
    params = float(model["params"])
    if model.get("type") in {"moe", "diffusion-moe"}:
        active = float(model["activeParams"])
        if active <= 6:
            return "8b"
        if active <= 15:
            return "14b"
        if active <= 30:
            return "30b"
        if active <= 45:
            return "70b"
        if params >= 800:
            return "1t_moe"
        if params >= 500:
            return "671b_moe"
        if params >= 180:
            return "235b_moe"
        return "120b_moe"
    if params <= 9:
        return "8b"
    if params <= 17:
        return "14b"
    if params <= 40:
        return "30b"
    return "70b"


def _nearest(bucket_map: Mapping[str, Any], wanted: str) -> float | None:
    direct = _number(bucket_map.get(wanted))
    if direct is not None:
        return direct
    index = _ORDER.index(wanted)
    for distance in range(1, len(_ORDER)):
        above = index + distance
        below = index - distance
        if above < len(_ORDER):
            value = _number(bucket_map.get(_ORDER[above]))
            if value is not None:
                return value
        if below >= 0:
            value = _number(bucket_map.get(_ORDER[below]))
            if value is not None:
                return value
    return None


class LlmRequirementsAdapter(SourceAdapter):
    name = "llmrequirements"
    parser_name = "llmrequirements-db-json"
    parser_version = "1"

    def parse(
        self,
        raw_bytes: bytes,
        *,
        content_type: str | None = None,
        source_url: str | None = None,
        original_name: str | None = None,
    ) -> ParseResult:
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("llmrequirements source must be its machine-readable data/db.json") from exc
        if not isinstance(payload, dict):
            raise ValueError("llmrequirements data/db.json root must be an object")
        models = payload.get("models")
        builds = payload.get("builds")
        if not isinstance(models, list) or not isinstance(builds, list):
            raise ValueError("llmrequirements data/db.json needs models and builds arrays")

        valid_models: list[dict[str, Any]] = []
        skipped_models = 0
        for model in models:
            if not isinstance(model, dict) or model.get("modality", "text") != "text":
                continue
            model_id = model.get("id")
            model_name = model.get("name")
            params = _number(model.get("params"))
            minimum = _number(model.get("vramMinQ4"))
            active = _number(model.get("activeParams"))
            is_moe = model.get("type") in {"moe", "diffusion-moe"}
            if (
                not isinstance(model_id, str)
                or not model_id.strip()
                or not isinstance(model_name, str)
                or not model_name.strip()
                or params is None
                or params <= 0
                or minimum is None
                or minimum <= 0
                or (is_moe and (active is None or active <= 0))
            ):
                skipped_models += 1
                continue
            valid_models.append(model)

        rows: list[dict[str, Any]] = []
        skipped_builds = 0
        for build in builds:
            if not isinstance(build, dict) or not build.get("id"):
                skipped_builds += 1
                continue
            usable = _number(build.get("usableMemoryGB"))
            if usable is None or usable <= 0:
                skipped_builds += 1
                continue
            gpu_name = normalize_gpu_name(build.get("name") or build.get("id"))
            for model in valid_models:
                minimum = _number(model.get("vramMinQ4"))
                if minimum is None or minimum > usable:
                    continue
                bucket = _params_bucket(model)
                tps = build.get("tps")
                pp = build.get("pp")
                ttft = build.get("ttft100k")
                decode = _nearest(tps if isinstance(tps, Mapping) else {}, bucket)
                prefill = _number(pp.get(bucket)) if isinstance(pp, Mapping) else None
                ttft_seconds = _nearest(ttft if isinstance(ttft, Mapping) else {}, bucket)
                ident = normalize_model_identity(model.get("name") or model.get("id"))
                notes = (
                    "Advisory Q4 estimate reproduced from llmrequirements.com build buckets; "
                    "not a per-model measured run. Decode is a single-stream estimate; "
                    "TTFT, when present, is the site's 100K-input estimate. "
                    "Quality ratings and benchmark claims are preserved only in raw_metrics_json."
                )
                raw_metrics = {
                    "database_last_updated": payload.get("_lastUpdated"),
                    "database_last_material_release": payload.get("_lastMaterialRelease"),
                    "build": build,
                    "model": model,
                    "estimate": {
                        "quantization": "Q4",
                        "params_bucket": bucket,
                        "decode_tok_s": decode,
                        "prefill_tok_s": prefill,
                        "ttft_100k_seconds": ttft_seconds,
                    },
                }
                context_k = _number(model.get("contextK"))
                rows.append({
                    "source_row_id": f"{build['id']}:{model.get('id')}:q4",
                    "report_url": model.get("huggingface") or source_url,
                    "model_name_raw": model.get("name"),
                    "model_id_normalized": ident["model_id_normalized"],
                    "model_family": ident["model_family"],
                    "model_variant": ident["model_variant"],
                    "modality": "text",
                    "engine": None,
                    "engine_version": None,
                    "precision": "4-bit",
                    "quantization": "Q4",
                    "gpu_model": gpu_name,
                    "gpu_count": 1,
                    "vram_gb": _number(build.get("memoryGB")),
                    "hardware_notes": (
                        f"{build.get('name')}; {usable:g} GB usable; "
                        f"site minimum for model at Q4: {minimum:g} GB"
                    ),
                    "context_tokens": None,
                    "max_context_tokens": int(context_k * 1024) if context_k else None,
                    "concurrency": 1,
                    "prompt_tokens": None,
                    "output_tokens": None,
                    "ttft_ms": ttft_seconds * 1000.0 if ttft_seconds is not None else None,
                    "decode_tok_s": decode,
                    "throughput_tok_s": decode,
                    "peak_throughput_tok_s": None,
                    "success_rate": None,
                    "capacity_users_32k": None,
                    "methodology_notes": notes,
                    "raw_metrics_json": json.dumps(raw_metrics, sort_keys=True),
                })
        if not rows:
            raise ValueError("llmrequirements data/db.json contained no fitting text model/build rows")
        warnings = ["Imported advisory estimates only; promotion_quality_evidence remains false."]
        if skipped_models or skipped_builds:
            warnings.append(
                f"Skipped malformed records: models={skipped_models}, builds={skipped_builds}."
            )
        return ParseResult(
            rows=rows,
            warnings=warnings,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )
