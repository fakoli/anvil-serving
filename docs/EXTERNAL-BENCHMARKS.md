# External Benchmarks

External inference benchmarks are performance priors for Anvil Serving. They help answer:

> External sources report that model X on RTX PRO 6000 with vLLM/NVFP4 at 32K context usually lands around Y tok/s. Our local serve fingerprint lands at Z tok/s, with these flags and these methodology differences.

They are not routing-quality truth. Anvil's local work-class evals and quality profiles still decide whether a model is `allow`, `allow-with-verify`, or `deny` for a workload.

## Supported Sources

Supported source adapters:

- `millstone`: Millstone AI LLM inference benchmark snapshots.
- `rtx6kpro`: `local-inference-lab/rtx6kpro` RTX PRO 6000 Blackwell community inference-throughput JSON artifacts.

The Millstone adapter does not assume a stable public API. It supports:

- `fetch` mode for downloading and storing a raw snapshot from a configured URL.
- `import` mode for importing a previously saved JSON, CSV, Markdown, or HTML snapshot from disk.

The `rtx6kpro` v1 adapter is intentionally narrower. It imports individual raw JSON artifacts only:

- `benchmarks/inference-throughput/*.json`
- `models/glm5.1/benchmarks/**/decode-matrix.json`

It does not crawl the whole GitHub repository or wiki, and it does not ingest Markdown, CSV, quality benchmarks, hardware/network/power benchmarks, or prose as routing truth. Non-JSON imports still store the raw snapshot and mark parsing `failed` with a message pointing users to the machine-readable JSON artifacts.

Import mode is the foundation for tests and reproducible comparisons. It never needs network access.

Every snapshot stores the raw file path, source URL or import path, fetch/import timestamp, parser name and version, SHA256 content hash, parse status, and parse error if parsing fails.

## Initialize A Store

```bash
anvil-serving benchmark external init --db .anvil/benchmarks.sqlite
```

This creates a local SQLite store with tables for sources, raw snapshots, normalized benchmark rows, serve fingerprints, and comparison records.

## Import A Saved Snapshot

```bash
anvil-serving benchmark external import \
  --source millstone \
  --file tests/fixtures/external_benchmarks/millstone_sample.json \
  --db .anvil/benchmarks.sqlite
```

For `rtx6kpro`, save a raw JSON artifact first, then import that file:

```bash
anvil-serving benchmark external import \
  --source rtx6kpro \
  --file tests/fixtures/external_benchmarks/rtx6kpro_qwen_vllm_mtp.json \
  --db .anvil/benchmarks.sqlite
```

The raw snapshot is copied under `.anvil/external-benchmarks/raw/` when the DB lives under `.anvil/`. Parser failures are non-destructive: the raw snapshot stays stored, the snapshot row is marked `failed`, and the CLI prints the parse error.

## Fetch A Live Snapshot

```bash
anvil-serving benchmark external fetch \
  --source millstone \
  --url https://example.com/millstone-snapshot.html \
  --db .anvil/benchmarks.sqlite
```

For `rtx6kpro`, fetch individual raw GitHub JSON files rather than repository or wiki pages:

```bash
anvil-serving benchmark external fetch \
  --source rtx6kpro \
  --url https://raw.githubusercontent.com/local-inference-lab/rtx6kpro/master/benchmarks/inference-throughput/vllm_awq_mtp.json \
  --db .anvil/benchmarks.sqlite
```

Use fetch mode only when you explicitly want live network access. Tests and fixture-based workflows should use import mode.

## List RTX PRO 6000 Rows

```bash
anvil-serving benchmark external list \
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
anvil-serving benchmark external report \
  --gpu "RTX PRO 6000" \
  --format markdown \
  --db .anvil/benchmarks.sqlite
```

The output is a Markdown table suitable for docs or README inclusion.

## Use External Priors For Voice Model A/B

For OpenClaw Talk or other voice-latency experiments, external benchmark rows are
only a candidate-selection aid. They can help decide which local model to test
first, but they do not prove tool-call behavior, session-memory behavior, audio
turn latency, or promotion safety.

The practical workflow is:

1. Start from the current local baseline in `configs/serve-recipes.toml`.
2. Use `anvil-serving benchmark external sources` and `anvil-serving benchmark external
   report` to find advisory throughput or TTFT priors for the same GPU family.
3. Mark any candidate that needs a model download, cache deletion, new image, new
   port, cloud API usage, or current-serve disruption as human-gated.
4. Run local `preflight`, `benchmark`, and `voice benchmark` before any live Talk
   trial.
5. Promote nothing from external priors alone.

The dated findings under `docs/findings/` should record why a candidate was
included or rejected, the exact serve recipe used, and whether the evidence came
from local measurement or an external advisory source.

## Export Rows

```bash
anvil-serving benchmark external export \
  --format json \
  --out external-benchmarks.json \
  --db .anvil/benchmarks.sqlite
```

The export contains normalized benchmark rows with their source and snapshot metadata.

## Compare A Local Anvil Run

```bash
anvil-serving benchmark external compare \
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

## Agent MCP Advisory Workflow

Agents should prefer the MCP/controller wrappers when they need external
benchmark priors:

- `external_bench_sources`
- `external_bench_list`
- `external_bench_report`
- `external_bench_compare`

Every wrapper returns `advisory_only: true` and
`promotion_quality_evidence: false`. `external_bench_compare` returns structured
local-vs-external deltas for throughput and TTFT, plus exact/nearest match
metadata. The MCP wrappers read initialized benchmark stores only: they do not
import snapshots, initialize a missing DB, or record comparison history. Use the
CLI `init`, `import`, `fetch`, or `compare` commands when you intentionally want
those persistent writes. Workflow packets may include MCP results in
`advisory_priors`, but `workflow_packet_validate` rejects priors that lack
`advisory_only: true` and `promotion_quality_evidence: false`, and still
requires a human-approved `router_promote` result before any packet can claim
`promoted: true`.

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
- `rtx6kpro` v1 supports individual JSON artifacts only. Whole-repo crawling, wiki ingestion, quality CSVs such as GPQA/GSM8K/HardMath, and hardware/network/power benchmarks are out of scope until Anvil has separate schemas for those priors.
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

