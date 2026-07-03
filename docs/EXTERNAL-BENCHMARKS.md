# External Benchmarks

External inference benchmarks are performance priors for Anvil Serving. They help answer:

> External sources report that model X on RTX PRO 6000 with vLLM/NVFP4 at 32K context usually lands around Y tok/s. Our local serve fingerprint lands at Z tok/s, with these flags and these methodology differences.

They are not routing-quality truth. Anvil's local work-class evals and quality profiles still decide whether a model is `allow`, `allow-with-verify`, or `deny` for a workload.

## Supported Sources

The first supported source adapter is `millstone`, for Millstone AI LLM inference benchmark snapshots. The adapter does not assume a stable public API. It supports:

- `fetch` mode for downloading and storing a raw snapshot from a configured URL.
- `import` mode for importing a previously saved JSON, CSV, Markdown, or HTML snapshot from disk.

Import mode is the foundation for tests and reproducible comparisons. It never needs network access.

Every snapshot stores the raw file path, source URL or import path, fetch/import timestamp, parser name and version, SHA256 content hash, parse status, and parse error if parsing fails.

## Initialize A Store

```bash
anvil-serving external-bench init --db .anvil/benchmarks.sqlite
```

This creates a local SQLite store with tables for sources, raw snapshots, normalized benchmark rows, serve fingerprints, and comparison records.

## Import A Saved Snapshot

```bash
anvil-serving external-bench import \
  --source millstone \
  --file tests/fixtures/external_benchmarks/millstone_sample.json \
  --db .anvil/benchmarks.sqlite
```

The raw snapshot is copied under `.anvil/external-benchmarks/raw/` when the DB lives under `.anvil/`. Parser failures are non-destructive: the raw snapshot stays stored, the snapshot row is marked `failed`, and the CLI prints the parse error.

## Fetch A Live Snapshot

```bash
anvil-serving external-bench fetch \
  --source millstone \
  --url https://example.com/millstone-snapshot.html \
  --db .anvil/benchmarks.sqlite
```

Use fetch mode only when you explicitly want live network access. Tests and fixture-based workflows should use import mode.

## List RTX PRO 6000 Rows

```bash
anvil-serving external-bench list \
  --gpu "RTX PRO 6000" \
  --top 20 \
  --db .anvil/benchmarks.sqlite
```

GPU names are normalized conservatively. These all map to `rtx_pro_6000_blackwell_96gb`:

- `RTX PRO 6000 Blackwell`
- `RTX PRO 6000`
- `RTX Pro 6000`
- `NVIDIA RTX PRO 6000 Blackwell 96GB`

RTX 5090 variants map to `rtx_5090_32gb`.

## Generate A Markdown Report

```bash
anvil-serving external-bench report \
  --gpu "RTX PRO 6000" \
  --format markdown \
  --db .anvil/benchmarks.sqlite
```

The output is a Markdown table suitable for docs or README inclusion.

## Export Rows

```bash
anvil-serving external-bench export \
  --format json \
  --out external-benchmarks.json \
  --db .anvil/benchmarks.sqlite
```

The export contains normalized benchmark rows with their source and snapshot metadata.

## Compare A Local Anvil Run

```bash
anvil-serving external-bench compare \
  --local tests/fixtures/external_benchmarks/local_benchmark_sample.json \
  --gpu "RTX PRO 6000" \
  --db .anvil/benchmarks.sqlite
```

The comparison matches external rows by:

1. Normalized GPU name.
2. Model family or normalized model id.
3. Engine, when known.
4. Precision or quantization, when known.
5. Context-length bucket.
6. Concurrency bucket.

If an exact match is not available, the report shows the nearest external row and explains the mismatches.

The report includes the local serve fingerprint, nearest external source, local and external throughput, delta percentage, TTFT when available, context/concurrency mismatch warnings, and methodology warnings.

Important warning example:

```text
Local run used NEXTN speculative decoding; external baseline did not report speculative decoding. Throughput delta is not an apples-to-apples model/engine comparison.
```

## Local Benchmark JSON

`anvil-serving benchmark` keeps its existing console output. For comparison workflows, pass `--json-out`:

```bash
anvil-serving benchmark \
  --base-url http://127.0.0.1:30000/v1 \
  --model local-specialist \
  --burst 20 \
  --json-out local-benchmark.json
```

Add GPU, engine, quantization, and serve flags to the JSON when the benchmark command cannot infer them from the endpoint. The compare command accepts the fields used in `tests/fixtures/external_benchmarks/local_benchmark_sample.json`.

## Serve Fingerprints

A serve fingerprint identifies a local serving setup:

- model id and served model name
- engine and engine version
- precision and quantization
- GPU model and GPU count
- context limit
- KV cache dtype
- reasoning parser and tool-call parser
- serve flags

The fingerprint hash is stored with comparison records so an engine, quant, parser, context, or serve-flag change does not get confused with a prior local measurement.

## Known Limitations

- External benchmarks are advisory only. They never silently become quality gates.
- Millstone is parsed from snapshots, not from a guaranteed API contract.
- HTML and Markdown parsing is table-oriented. Highly irregular pages may require saving a cleaner snapshot or adding source-specific extraction logic.
- Methodology fields are best-effort. If a source does not report prompt cache, speculative decoding, tokenizer details, or sampling settings, Anvil reports that as a comparison caveat.
- Wrapper names such as LM Studio or Ollama UI should be treated as wrappers. Store the underlying engine when known, such as `vLLM`, `SGLang`, `TensorRT-LLM`, `llama.cpp`, `ExLlamaV3`, `Transformers`, or `KTransformers`.

## Add Another Source Adapter

1. Add an adapter in `anvil_serving/external_benchmarks/sources/`.
2. Subclass `SourceAdapter` and return a `ParseResult`.
3. Preserve raw snapshots by using the shared CLI/store path. Do not parse before storage.
4. Normalize rows through `normalize_external_row()` unless the source already emits the exact internal fields.
5. Register the adapter in `sources/__init__.py`.
6. Add offline fixtures under `tests/fixtures/external_benchmarks/`.
7. Add tests that import from disk. Do not require network access.

