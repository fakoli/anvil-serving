"""Conservative normalization for external benchmark rows.

These helpers intentionally normalize only facts that are well-known enough to
be useful. Unknown values are slugged or left empty rather than invented.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping


def _compact_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slug(value: Any, *, keep_dot: bool = False) -> str:
    text = _compact_space(value).lower()
    text = text.replace("_", "-")
    allowed = r"[^a-z0-9.\-]+" if keep_dot else r"[^a-z0-9]+"
    text = re.sub(allowed, "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def normalize_gpu_name(value: Any) -> str | None:
    raw = _compact_space(value)
    if not raw:
        return None
    text = raw.lower()
    if "5090" in text and "rtx" in text:
        return "rtx_5090_32gb"
    if "6000" in text and "rtx" in text and ("pro" in text or "professional" in text):
        return "rtx_pro_6000_blackwell_96gb"
    return _key(raw)


def normalize_engine(value: Any) -> str | None:
    raw = _compact_space(value)
    if not raw:
        return None
    text = raw.lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if text in {"vllm", "vllm openai", "v llm"} or text.startswith("vllm "):
        return "vLLM"
    if text in {"sglang", "sg lang"}:
        return "SGLang"
    if text in {"llamacpp", "llama cpp", "llama.cpp"}:
        return "llama.cpp"
    if text in {"ollama"}:
        return "llama.cpp"
    if text in {"lm studio", "ollama ui"}:
        return None
    if text in {"tensorrt llm", "tensorrt-llm", "tensorrtllm"}:
        return "TensorRT-LLM"
    if text in {"exllamav3", "exllama v3", "exllama3", "exl3"}:
        return "ExLlamaV3"
    if text in {"transformers", "huggingface transformers"}:
        return "Transformers"
    if text in {"ktransformers", "k transformers"}:
        return "KTransformers"
    return raw


_PRECISIONS = {
    "FP8": "FP8",
    "BF16": "BF16",
    "INT8": "INT8",
}

_QUANTS = {
    "NVFP4": "NVFP4",
    "MXFP4": "MXFP4",
    "AWQ": "AWQ",
    "GGUF Q4_K_M": "GGUF Q4_K_M",
    "GGUF Q5_K_M": "GGUF Q5_K_M",
    "EXL3": "EXL3",
}


def _upper_quantish(value: Any) -> str:
    text = _compact_space(value).upper()
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("GGUF Q4 K M"):
        return "GGUF Q4_K_M"
    if text.startswith("GGUF Q5 K M"):
        return "GGUF Q5_K_M"
    if text == "Q4 K M":
        return "GGUF Q4_K_M"
    if text == "Q5 K M":
        return "GGUF Q5_K_M"
    return text


def normalize_precision(value: Any) -> str | None:
    text = _upper_quantish(value)
    return _PRECISIONS.get(text)


def normalize_quantization(value: Any) -> str | None:
    text = _upper_quantish(value)
    return _QUANTS.get(text)


def normalize_precision_quant(
    precision: Any = None, quantization: Any = None
) -> tuple[str | None, str | None]:
    """Return ``(precision, quantization)`` from possibly overlapping fields."""
    p = normalize_precision(precision)
    q = normalize_quantization(quantization)
    if q is None:
        q = normalize_quantization(precision)
    if p is None:
        p = normalize_precision(quantization)
    return p, q


def normalize_model_identity(value: Any) -> dict[str, str | None]:
    raw = _compact_space(value)
    if not raw:
        return {"model_id_normalized": None, "model_family": None, "model_variant": None}
    variant = _slug(raw, keep_dot=True)
    family = variant.split("-", 1)[0] if variant else None
    if variant.startswith("gpt-oss"):
        family = "gpt-oss"
    elif variant.startswith("qwen"):
        family = "qwen"
    elif variant.startswith("nemotron"):
        family = "nemotron"
    elif variant.startswith("gemma"):
        family = "gemma"
    return {
        "model_id_normalized": variant,
        "model_family": family,
        "model_variant": variant,
    }


def _lookup(row: Mapping[str, Any], *names: str) -> Any:
    keyed = {_key(k): v for k, v in row.items()}
    for name in names:
        k = _key(name)
        if k in keyed and keyed[k] not in (None, ""):
            return keyed[k]
    return None


def _number(value: Any, *, binary_suffix: bool = False) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*([kKmM])?\b", text)
    if not m:
        return None
    try:
        number = float(m.group(1))
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    if suffix == "k":
        return number * (1024 if binary_suffix else 1000)
    if suffix == "m":
        return number * (1024 * 1024 if binary_suffix else 1000 * 1000)
    return number


def _integer(value: Any) -> int | None:
    n = _number(value)
    return None if n is None else int(n)


def _token_count(value: Any) -> int | None:
    n = _number(value, binary_suffix=True)
    return None if n is None else int(n)


def normalize_external_row(
    row: Mapping[str, Any], defaults: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Map a source row with loose headers to the normalized DB row contract."""
    defaults = defaults or {}
    model_raw = (
        _lookup(row, "model", "model name", "model_name", "model_name_raw")
        or defaults.get("model_name_raw")
    )
    ident = normalize_model_identity(model_raw)
    precision, quantization = normalize_precision_quant(
        _lookup(row, "precision", "dtype", "data type"),
        _lookup(row, "quantization", "quant", "quantization format"),
    )
    raw_gpu = _lookup(row, "gpu", "gpu model", "hardware", "accelerator")
    if raw_gpu is None:
        raw_gpu = defaults.get("gpu_model")
    gpu_model = normalize_gpu_name(raw_gpu)
    engine = normalize_engine(_lookup(row, "engine", "runtime", "server", "backend"))
    if engine is None:
        engine = normalize_engine(defaults.get("engine"))

    methodology = (
        _lookup(row, "methodology", "methodology notes", "notes", "benchmark notes")
        or defaults.get("methodology_notes")
    )
    report_url = _lookup(row, "report url", "report_url", "url") or defaults.get("report_url")

    out = {
        "source_row_id": _lookup(row, "id", "row id", "source row id") or defaults.get("row_id"),
        "report_url": report_url,
        "model_name_raw": model_raw,
        "model_id_normalized": ident["model_id_normalized"],
        "model_family": ident["model_family"],
        "model_variant": ident["model_variant"],
        "modality": _lookup(row, "modality") or defaults.get("modality") or "text",
        "engine": engine,
        "engine_version": _lookup(row, "engine version", "engine_version"),
        "precision": precision,
        "quantization": quantization,
        "gpu_model": gpu_model,
        "gpu_count": _integer(_lookup(row, "gpu count", "gpus", "gpu_count")),
        "vram_gb": _number(_lookup(row, "vram", "vram gb", "vram_gb", "memory gb")),
        "hardware_notes": _lookup(row, "hardware notes", "hardware_notes") or raw_gpu,
        "context_tokens": _token_count(
            _lookup(row, "context", "context tokens", "context_tokens", "context length")
        ),
        "max_context_tokens": _token_count(
            _lookup(row, "max context", "max_context_tokens", "max context tokens")
        ),
        "concurrency": _integer(_lookup(row, "concurrency", "parallel users", "users")),
        "prompt_tokens": _token_count(_lookup(row, "prompt tokens", "prompt_tokens")),
        "output_tokens": _token_count(_lookup(row, "output tokens", "output_tokens")),
        "ttft_ms": _number(_lookup(row, "ttft", "ttft ms", "ttft_ms", "time to first token ms")),
        "decode_tok_s": _number(
            _lookup(row, "decode tok/s", "decode_tok_s", "decode tokens per second")
        ),
        "throughput_tok_s": _number(
            _lookup(row, "throughput tok/s", "throughput_tok_s", "tokens/s", "tok/s")
        ),
        "peak_throughput_tok_s": _number(
            _lookup(row, "peak throughput tok/s", "peak_throughput_tok_s")
        ),
        "success_rate": _number(_lookup(row, "success rate", "success_rate")),
        "capacity_users_32k": _number(
            _lookup(row, "capacity users 32k", "capacity_users_32k", "32k users")
        ),
        "methodology_notes": methodology,
        "raw_metrics_json": json.dumps(dict(row), sort_keys=True),
    }
    return out


def context_bucket(tokens: Any) -> str | None:
    n = _token_count(tokens)
    if n is None:
        return None
    for bound, label in (
        (8192, "8k"),
        (16384, "16k"),
        (32768, "32k"),
        (65536, "64k"),
        (131072, "128k"),
    ):
        if n <= bound:
            return label
    return "128k+"


def concurrency_bucket(value: Any) -> str | None:
    n = _integer(value)
    if n is None:
        return None
    if n <= 1:
        return "1"
    if n <= 4:
        return "2-4"
    if n <= 8:
        return "5-8"
    if n <= 16:
        return "9-16"
    return "17+"
