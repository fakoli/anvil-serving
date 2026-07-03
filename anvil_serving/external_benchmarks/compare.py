"""Compare local Anvil benchmark JSON to external benchmark priors."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from . import store
from .normalize import (
    concurrency_bucket,
    context_bucket,
    normalize_engine,
    normalize_gpu_name,
    normalize_model_identity,
    normalize_precision_quant,
)


def _dig(data: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        cur: Any = data
        ok = True
        for part in path.split("."):
            if isinstance(cur, Mapping) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def load_local_benchmark(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    model = _dig(data, "model_id", "model", "served_model_name", "serve.model")
    ident = normalize_model_identity(model)
    precision, quantization = normalize_precision_quant(
        _dig(data, "precision", "serve.precision"),
        _dig(data, "quantization", "serve.quantization"),
    )
    flags = _dig(data, "serve_flags", "serve.flags") or {}
    if not isinstance(flags, Mapping):
        flags = {"raw": flags}
    metrics = _dig(data, "metrics") or data
    return {
        "raw": data,
        "run_id": _dig(data, "run_id", "id"),
        "model_id": ident["model_id_normalized"],
        "model_family": ident["model_family"],
        "model_variant": ident["model_variant"],
        "served_model_name": _dig(data, "served_model_name", "serve.served_model_name"),
        "engine": normalize_engine(_dig(data, "engine", "serve.engine")),
        "engine_version": _dig(data, "engine_version", "serve.engine_version"),
        "precision": precision,
        "quantization": quantization,
        "gpu_model": normalize_gpu_name(_dig(data, "gpu_model", "gpu", "serve.gpu_model")),
        "gpu_count": _dig(data, "gpu_count", "serve.gpu_count"),
        "context_limit": _dig(data, "context_limit", "max_context_tokens", "serve.context_limit"),
        "context_tokens": _dig(data, "context_tokens", "ctx_tokens", "metrics.context_tokens"),
        "concurrency": _dig(data, "concurrency", "metrics.concurrency"),
        "kv_cache_dtype": _dig(data, "kv_cache_dtype", "serve.kv_cache_dtype"),
        "reasoning_parser": _dig(data, "reasoning_parser", "serve.reasoning_parser"),
        "tool_call_parser": _dig(data, "tool_call_parser", "serve.tool_call_parser"),
        "serve_flags": dict(flags),
        "throughput_tok_s": _dig(
            metrics, "throughput_tok_s", "throughput", "output_tok_s", "aggregate_output_tok_s"
        ),
        "ttft_ms": _dig(metrics, "ttft_ms", "ttft_p50_ms"),
    }


def serve_fingerprint(local: Mapping[str, Any]) -> dict[str, Any]:
    flags_json = json.dumps(local.get("serve_flags") or {}, sort_keys=True)
    identity = {
        "model_id": local.get("model_id"),
        "served_model_name": local.get("served_model_name"),
        "engine": local.get("engine"),
        "engine_version": local.get("engine_version"),
        "quantization": local.get("quantization"),
        "precision": local.get("precision"),
        "gpu_model": local.get("gpu_model"),
        "gpu_count": local.get("gpu_count"),
        "context_limit": local.get("context_limit"),
        "kv_cache_dtype": local.get("kv_cache_dtype"),
        "reasoning_parser": local.get("reasoning_parser"),
        "tool_call_parser": local.get("tool_call_parser"),
        "serve_flags_json": flags_json,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    identity["fingerprint_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return identity


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score(local: Mapping[str, Any], row: Mapping[str, Any]) -> tuple[int, list[str]]:
    score = 0
    mismatches: list[str] = []
    if row.get("gpu_model") == local.get("gpu_model"):
        score += 10
    else:
        mismatches.append("GPU differs")
    if row.get("model_id_normalized") == local.get("model_id"):
        score += 7
    elif row.get("model_family") == local.get("model_family"):
        score += 4
        mismatches.append("model variant differs")
    else:
        mismatches.append("model differs")
    if local.get("engine") and row.get("engine"):
        if row.get("engine") == local.get("engine"):
            score += 3
        else:
            mismatches.append("engine differs")
    if local.get("quantization") or row.get("quantization"):
        if row.get("quantization") == local.get("quantization"):
            score += 3
        else:
            mismatches.append("quantization differs")
    elif local.get("precision") or row.get("precision"):
        if row.get("precision") == local.get("precision"):
            score += 2
        else:
            mismatches.append("precision differs")
    if context_bucket(row.get("context_tokens")) == context_bucket(local.get("context_tokens")):
        score += 2
    else:
        mismatches.append("context bucket differs")
    if concurrency_bucket(row.get("concurrency")) == concurrency_bucket(local.get("concurrency")):
        score += 2
    else:
        mismatches.append("concurrency bucket differs")
    return score, mismatches


def _local_uses_speculative(local: Mapping[str, Any]) -> str | None:
    flags = local.get("serve_flags") or {}
    blob = json.dumps(flags, sort_keys=True).lower()
    if "nextn" in blob:
        return "NEXTN"
    if "speculative" in blob or "draft" in blob:
        return "speculative decoding"
    return None


def _local_uses_prompt_cache(local: Mapping[str, Any]) -> bool:
    flags = local.get("serve_flags") or {}
    if isinstance(flags, Mapping):
        for key, value in flags.items():
            lowered = str(key).lower()
            if lowered in {"prompt_cache", "prefix_cache", "enable_prefix_caching"}:
                return bool(value)
            if lowered == "shared_prefix_burst" and value:
                return True
    blob = json.dumps(flags, sort_keys=True).lower()
    return "prompt_cache" in blob or "prefix_cache" in blob


def _external_reports_speculative(row: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(row.get(k) or "")
        for k in ("methodology_notes", "raw_metrics_json", "hardware_notes")
    ).lower()
    if "no speculative" in text or "without speculative" in text:
        return False
    return any(term in text for term in ("speculative", "draft model", "nextn", "mtp"))


def _external_reports_prompt_cache(row: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(row.get(k) or "")
        for k in ("methodology_notes", "raw_metrics_json", "hardware_notes")
    ).lower()
    if "prompt cache disabled" in text or "prefix cache disabled" in text:
        return False
    if "no prompt cache" in text or "no prefix cache" in text:
        return False
    return any(term in text for term in ("prompt cache", "prefix cache", "cached prompt"))


def compare_local_to_external(
    db_path: str | Path,
    local_path: str | Path,
    *,
    gpu: str | None = None,
    top: int = 5,
) -> dict[str, Any]:
    local = load_local_benchmark(local_path)
    if gpu and not local.get("gpu_model"):
        local = dict(local)
        local["gpu_model"] = normalize_gpu_name(gpu)
    rows = store.query_rows(db_path, gpu=gpu or local.get("gpu_model"))
    scored = []
    for row in rows:
        score, mismatches = _score(local, row)
        scored.append((score, mismatches, row))
    scored.sort(key=lambda item: (-item[0], -(item[2].get("throughput_tok_s") or 0), item[2]["id"]))
    nearest = scored[:top]
    exact = [
        (score, mismatches, row)
        for score, mismatches, row in nearest
        if not mismatches
        or mismatches == ["precision differs"]
        and not (local.get("precision") and row.get("precision"))
    ]
    chosen = (exact[0] if exact else nearest[0]) if nearest else None
    warnings: list[str] = []
    if chosen:
        for mismatch in chosen[1]:
            warnings.append(mismatch)
        spec = _local_uses_speculative(local)
        if spec and not _external_reports_speculative(chosen[2]):
            warnings.append(
                "Local run used %s speculative decoding; external baseline did not report "
                "speculative decoding. Throughput delta is not an apples-to-apples "
                "model/engine comparison." % spec
            )
        if _local_uses_prompt_cache(local) and not _external_reports_prompt_cache(chosen[2]):
            warnings.append(
                "Local run used prompt/prefix cache; external baseline did not report prompt "
                "cache. Throughput and TTFT deltas may include cache effects."
            )
        fp = serve_fingerprint(local)
        fp_id = store.upsert_serve_fingerprint(db_path, fp)
        local_tp = _as_float(local.get("throughput_tok_s"))
        external_tp = _as_float(chosen[2].get("throughput_tok_s"))
        store.insert_comparison(
            db_path,
            serve_fingerprint_id=fp_id,
            external_row_id=chosen[2]["id"],
            local_run_id=local.get("run_id"),
            metric="throughput_tok_s",
            local_value=local_tp,
            external_value=external_tp,
            notes="; ".join(warnings) if warnings else None,
        )
    else:
        fp = serve_fingerprint(local)
    return {
        "local": local,
        "fingerprint": fp,
        "chosen": chosen,
        "nearest": nearest,
        "exact": bool(exact),
        "warnings": warnings,
    }


def render_comparison(result: Mapping[str, Any]) -> str:
    local = result["local"]
    fp = result["fingerprint"]
    lines = [
        "# External Benchmark Comparison",
        "",
        f"local serve fingerprint: `{fp['fingerprint_sha256']}`",
        f"local run: `{local.get('run_id') or 'unknown'}`",
        f"local model: `{local.get('model_id') or 'unknown'}` on `{local.get('gpu_model') or 'unknown'}`",
        "",
    ]
    chosen = result.get("chosen")
    if not chosen:
        lines.append("No external rows matched the requested GPU/model prior.")
        return "\n".join(lines)
    _score_value, mismatches, row = chosen
    lines.append("match: " + ("exact external match" if result.get("exact") else "nearest external row"))
    lines.append(
        "external source: `%s`, model `%s`, engine `%s`"
        % (row.get("source_name"), row.get("model_id_normalized"), row.get("engine"))
    )
    lines.append("")
    lines.append("| metric | local | external | delta |")
    lines.append("|---|---:|---:|---:|")
    local_tp = _as_float(local.get("throughput_tok_s"))
    external_tp = _as_float(row.get("throughput_tok_s"))
    if local_tp is not None and external_tp not in (None, 0):
        delta = local_tp - external_tp
        pct = (delta / external_tp) * 100.0
        lines.append(f"| throughput_tok_s | {local_tp:.2f} | {external_tp:.2f} | {pct:+.1f}% |")
    local_ttft = _as_float(local.get("ttft_ms"))
    external_ttft = _as_float(row.get("ttft_ms"))
    if local_ttft is not None and external_ttft is not None:
        delta = local_ttft - external_ttft
        pct = (delta / external_ttft) * 100.0 if external_ttft else 0.0
        lines.append(f"| ttft_ms | {local_ttft:.2f} | {external_ttft:.2f} | {pct:+.1f}% |")
    lines.append("")
    lines.append(
        "context/concurrency: local `%s`/`%s`, external `%s`/`%s`"
        % (
            local.get("context_tokens"),
            local.get("concurrency"),
            row.get("context_tokens"),
            row.get("concurrency"),
        )
    )
    if result.get("warnings"):
        lines.append("")
        lines.append("Warnings:")
        for warning in result["warnings"]:
            lines.append(f"- {warning}")
    if mismatches:
        lines.append("")
        lines.append("Nearest-row mismatches: " + ", ".join(mismatches))
    nearest = result.get("nearest") or []
    if not result.get("exact") and nearest:
        lines.append("")
        lines.append("Nearest External Rows:")
        lines.append("| source | model | gpu | engine | quant | ctx | conc | tok/s | mismatches |")
        lines.append("|---|---|---|---|---|---:|---:|---:|---|")
        for _score_value, near_mismatches, near_row in nearest:
            quant = near_row.get("quantization") or near_row.get("precision") or ""
            tok_s = near_row.get("throughput_tok_s")
            tok_s_text = "" if tok_s is None else f"{float(tok_s):.2f}"
            lines.append(
                "| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                % (
                    near_row.get("source_name") or "",
                    near_row.get("model_id_normalized") or "",
                    near_row.get("gpu_model") or "",
                    near_row.get("engine") or "",
                    quant,
                    near_row.get("context_tokens") or "",
                    near_row.get("concurrency") or "",
                    tok_s_text,
                    ", ".join(near_mismatches) if near_mismatches else "none",
                )
            )
    return "\n".join(lines)
