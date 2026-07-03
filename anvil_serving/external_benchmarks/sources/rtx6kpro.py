"""local-inference-lab/rtx6kpro JSON source adapter."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from ..normalize import (
    normalize_engine,
    normalize_gpu_name,
    normalize_model_identity,
    normalize_precision_quant,
)
from .base import ParseResult, SourceAdapter


JSON_ONLY_MESSAGE = (
    "rtx6kpro v1 imports only machine-readable JSON artifacts; import individual "
    "benchmarks/inference-throughput/*.json or "
    "models/glm5.1/benchmarks/**/decode-matrix.json snapshots."
)


class Rtx6kproAdapter(SourceAdapter):
    name = "rtx6kpro"
    parser_name = "rtx6kpro"
    parser_version = "1"

    def parse(
        self,
        raw_bytes: bytes,
        *,
        content_type: str | None = None,
        source_url: str | None = None,
        original_name: str | None = None,
    ) -> ParseResult:
        text = raw_bytes.decode("utf-8-sig", errors="replace")
        if not _looks_like_json(text, content_type, original_name, source_url):
            raise ValueError(JSON_ONLY_MESSAGE)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{JSON_ONLY_MESSAGE} JSON parse error: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("rtx6kpro JSON snapshot must be an object with metadata/results")

        metadata = _mapping(payload.get("metadata"))
        results = payload.get("results")
        if not isinstance(results, list):
            raise ValueError("rtx6kpro JSON snapshot must contain a results[] array")

        artifact = _artifact_name(original_name, source_url)
        artifact_context = _artifact_context(original_name, source_url)
        rows = []
        warnings: list[str] = []
        for index, result in enumerate(results):
            if not isinstance(result, Mapping):
                warnings.append(f"skipped non-object result at index {index}")
                continue
            normalized = _row_from_result(
                payload=payload,
                metadata=metadata,
                result=result,
                index=index,
                artifact=artifact,
                artifact_context=artifact_context,
                source_url=source_url,
            )
            rows.append(normalized)

        return ParseResult(
            rows=rows,
            warnings=warnings,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )


def _looks_like_json(
    text: str,
    content_type: str | None,
    original_name: str | None,
    source_url: str | None,
) -> bool:
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        return True
    haystack = " ".join(str(v or "") for v in (content_type, original_name, source_url)).lower()
    return "json" in haystack


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _artifact_name(original_name: str | None, source_url: str | None) -> str:
    raw = source_url or original_name or "rtx6kpro.json"
    parsed = urlparse(raw)
    path = unquote(parsed.path) if parsed.scheme else raw
    path = path.replace("\\", "/").strip("/")
    return path.rsplit("/", 1)[-1] or "rtx6kpro.json"


def _artifact_context(original_name: str | None, source_url: str | None) -> str:
    raw = source_url or original_name or ""
    parsed = urlparse(raw)
    path = unquote(parsed.path) if parsed.scheme else raw
    return path.replace("\\", "/").lower()


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", text.lower()) if token}


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _dig(data: Mapping[str, Any], *path: str) -> Any:
    cur: Any = data
    for part in path:
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _model_name(metadata: Mapping[str, Any]) -> str | None:
    raw = _first(metadata, "model", "model_name", "served_model_name")
    if raw in (None, ""):
        return None
    return re.sub(r"\bQwen3_5(?=-)", "Qwen3.5", str(raw))


def _engine(metadata: Mapping[str, Any], artifact_context: str) -> str | None:
    raw = _first(metadata, "engine", "runtime", "backend")
    if raw in (None, "") and _artifact_name(artifact_context, None).lower().startswith("vllm_"):
        raw = "vLLM"
    return normalize_engine(raw)


def _quantization(model_name: str | None, artifact_context: str) -> tuple[str | None, str | None]:
    text = " ".join(v for v in (model_name, artifact_context) if v)
    candidates = _tokens(text)
    raw_quant = None
    if "nvfp4" in candidates:
        raw_quant = "NVFP4"
    elif "awq" in candidates:
        raw_quant = "AWQ"
    return normalize_precision_quant(None, raw_quant)


def _max_context(metadata: Mapping[str, Any]) -> int | None:
    direct = _as_int(_first(metadata, "max_context_tokens", "context_limit"))
    if direct is not None:
        return direct
    values = metadata.get("context_lengths")
    if isinstance(values, list):
        numbers = [_as_int(value) for value in values]
        numbers = [value for value in numbers if value is not None]
        if numbers:
            return max(numbers)
    return None


def _gpu_from_diagnostics(payload: Mapping[str, Any]) -> tuple[str | None, int | None]:
    stdout = _dig(payload, "startup_diagnostics", "nvidia_smi_query", "stdout")
    if not isinstance(stdout, str):
        return None, None
    matches = [
        line.strip()
        for line in stdout.splitlines()
        if "rtx" in line.lower() and "6000" in line.lower() and "pro" in line.lower()
    ]
    if not matches:
        return None, None
    first = matches[0]
    model_match = re.search(
        r"(NVIDIA\s+RTX\s+PRO\s+6000[^,\n]*)",
        first,
        flags=re.IGNORECASE,
    )
    raw_model = model_match.group(1).strip() if model_match else first
    return raw_model, len(matches)


def _metadata_gpu_count(metadata: Mapping[str, Any]) -> int | None:
    direct = _first(metadata, "gpu_count", "gpus", "num_gpus")
    if direct is not None:
        return _as_int(direct)
    return None


def _methodology_notes(
    *,
    metadata: Mapping[str, Any],
    payload: Mapping[str, Any],
    artifact_context: str,
) -> str | None:
    notes: list[str] = []
    tokens = _tokens(artifact_context)
    if "nomtp" in tokens:
        notes.append("No MTP speculative decoding reported/inferred from artifact filename")
    elif "mtp" in tokens or any(re.fullmatch(r"mtp\d+", token) for token in tokens):
        notes.append("MTP speculative decoding reported/inferred from artifact filename")

    dcp = _as_int(metadata.get("dcp_size"))
    if dcp is None:
        dcp = _dcp_from_path(tokens)
        if dcp is not None:
            notes.append(f"DCP size {dcp} inferred from artifact path")
    else:
        notes.append(f"DCP size {dcp} reported in metadata")

    if metadata.get("skip_prefill") is True or metadata.get("prefill_mode") == "skipped":
        notes.append("skip_prefill reported; standalone prefill throughput not measured")

    p2p_override = _dig(payload, "nvidia_p2p_override", "effective")
    if p2p_override is None:
        p2p_override = _dig(payload, "startup_diagnostics", "nvidia_p2p_override", "effective")
    if p2p_override is True:
        notes.append("NVIDIA P2P override effective in diagnostics")

    diagnostics_blob = json.dumps(
        {
            "metadata": metadata,
            "startup_diagnostics": payload.get("startup_diagnostics"),
            "event_log": payload.get("event_log"),
            "nvidia_p2p_override": payload.get("nvidia_p2p_override"),
        },
        sort_keys=True,
        default=str,
    ).lower()
    if "patched nccl" in diagnostics_blob or "nccl pr2127" in diagnostics_blob:
        notes.append("patched NCCL reported in diagnostics")
    if "custom allreduce" in diagnostics_blob:
        notes.append("custom allreduce reported in diagnostics")

    return "; ".join(notes) if notes else None


def _dcp_from_path(tokens: set[str]) -> int | None:
    for token in tokens:
        match = re.fullmatch(r"dcp(\d+)", token)
        if match:
            return int(match.group(1))
    return None


def _row_from_result(
    *,
    payload: Mapping[str, Any],
    metadata: Mapping[str, Any],
    result: Mapping[str, Any],
    index: int,
    artifact: str,
    artifact_context: str,
    source_url: str | None,
) -> dict[str, Any]:
    model_raw = _model_name(metadata)
    ident = normalize_model_identity(model_raw)
    precision, quantization = _quantization(model_raw, artifact_context)
    raw_gpu, diagnostic_gpu_count = _gpu_from_diagnostics(payload)
    gpu_count = _metadata_gpu_count(metadata)
    if gpu_count is None:
        gpu_count = diagnostic_gpu_count
    raw_gpu = raw_gpu or _first(metadata, "gpu_model", "gpu") or "NVIDIA RTX PRO 6000 Blackwell 96GB"
    methodology_notes = _methodology_notes(
        metadata=metadata,
        payload=payload,
        artifact_context=artifact_context,
    )
    raw_metrics = {
        "artifact": artifact,
        "source_url": source_url,
        "metadata": dict(metadata),
        "result": dict(result),
        "methodology_notes": methodology_notes,
    }
    context_tokens = _as_int(result.get("context_tokens"))
    concurrency = _as_int(result.get("concurrency"))
    output_tokens = _as_int(result.get("output_seq_len_avg"))
    if output_tokens is None:
        output_tokens = _as_int(metadata.get("max_tokens"))

    return {
        "source_row_id": f"{artifact}:row={index}:ctx={context_tokens}:conc={concurrency}",
        "report_url": source_url,
        "model_name_raw": model_raw,
        "model_id_normalized": ident["model_id_normalized"],
        "model_family": ident["model_family"],
        "model_variant": ident["model_variant"],
        "modality": "text",
        "engine": _engine(metadata, artifact_context),
        "engine_version": _first(metadata, "engine_version", "version"),
        "precision": precision,
        "quantization": quantization,
        "gpu_model": normalize_gpu_name(raw_gpu),
        "gpu_count": gpu_count,
        "vram_gb": _as_float(_first(metadata, "vram_gb", "memory_gb")),
        "hardware_notes": str(raw_gpu) if raw_gpu else None,
        "context_tokens": context_tokens,
        "max_context_tokens": _max_context(metadata),
        "concurrency": concurrency,
        "prompt_tokens": _as_int(result.get("input_seq_len_avg")),
        "output_tokens": output_tokens,
        "ttft_ms": _ttft_ms(result),
        "decode_tok_s": _as_float(
            _first(result, "per_request_avg_tps", "output_tps_per_user_avg")
        ),
        "throughput_tok_s": _as_float(
            _first(result, "aggregate_tps", "server_gen_throughput")
        ),
        "peak_throughput_tok_s": _as_float(result.get("peak_throughput_tok_s")),
        "success_rate": None,
        "capacity_users_32k": None,
        "methodology_notes": methodology_notes,
        "raw_metrics_json": json.dumps(raw_metrics, sort_keys=True, default=str),
    }


def _ttft_ms(result: Mapping[str, Any]) -> float | None:
    direct = _as_float(result.get("ttft_ms"))
    if direct is not None:
        return direct
    seconds = _as_float(result.get("ttft_avg"))
    return seconds * 1000.0 if seconds is not None else None
