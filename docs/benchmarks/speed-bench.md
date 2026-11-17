# SPEED-Bench integration

For a privately prepared corpus, validate and print its offline plan:

```bash
anvil-serving eval benchmark speed plan --manifest corpus.json
```

The command reads local files and prints JSON. It does not download data, write
artifacts, contact a model, or change serving. Optional `--categories coding,reasoning`,
`--samples-per-category 8` and `--seed 42` control selection. Invalid input exits 1
with a bounded error; invalid command syntax exits 2.

**Implemented:** prepared JSONL validation, deterministic sampling, content
digests, CLI discovery and planning. **Pending:** managed preparation of the
upstream Parquet distribution, model execution, speculation telemetry,
comparison reports and a live pilot. Every plan says `execution_ready=false`.
An offline plan is not benchmark evidence or model qualification.

## How this extends our benchmarks

Our existing qualification covers context, tools/JSON, software tasks, vision,
stability, capacity and operational recovery. SPEED-Bench adds realistic domain
and entropy strata for performance measurement, especially speculative decoding.
It complements those gates; drafter acceptance is not task correctness.

Keep natural, EOS-terminated output separate from strict capacity workloads.
Likewise, a chat endpoint measurement and an engine's pretokenized measurement
are separate protocols: equal text does not establish equal token sequences.

Observed on 2026-10-17:

- [Dataset revision](https://huggingface.co/datasets/nvidia/SPEED-Bench/tree/454f88454792dfa3ccfd7ef15fff248efde44cd1):
  `454f88454792dfa3ccfd7ef15fff248efde44cd1`.
- [Preparation code](https://github.com/NVIDIA/Model-Optimizer/tree/d7cbff3486a3ffb1a357e32d9810da0bfc649709/examples/specdec_bench):
  `d7cbff3486a3ffb1a357e32d9810da0bfc649709`.
- The qualitative split has 880 rows. Throughput split metadata reports 1,536
  rows per bucket; the card's concluding 1,538 figure disagrees. Preparation
  must retain verified counts and this discrepancy, rather than silently using
  the prose count.

These are source pins, not qualified runtime versions. See the
[upstream overview](https://huggingface.co/blog/nvidia/speed-bench) for motivation.

## Prepared corpus contract

Upstream Parquet is not accepted directly. A private preparer must resolve masked
examples and produce one manifest plus a JSONL file. Preparation is a later
delivery task; there is no supported `speed prepare` command yet.

The manifest is a closed JSON object with these exact fields:

| Field | Contract |
| --- | --- |
| `schema` | `anvil-serving.speed-corpus/v1` |
| `dataset` | `nvidia/SPEED-Bench` |
| `dataset_revision` | Full lowercase 40- or 64-digit hexadecimal commit |
| `preparer_revision` | Full lowercase 40- or 64-digit hexadecimal commit |
| `config` | `qualitative` or `throughput_1k`, `throughput_2k`, `throughput_8k`, `throughput_16k`, `throughput_32k` |
| `data_file` | Local JSONL basename; its resolved path stays inside the manifest directory |
| `data_sha256` | Lowercase SHA-256 of the exact JSONL bytes |
| `sources` | Object mapping every row's exact source URL to its provenance declaration |
| `sources_sha256` | SHA-256 of canonical JSON for `sources` |

Each source declaration has exactly `revision`, `license_url` and
`license_decision`. The revision is an immutable 40- or 64-digit hexadecimal
commit. Source and license URLs are HTTPS without credentials, query strings or
fragments. `license_decision` must be `approved`, meaning the operator recorded
approval for the intended use. This field does not grant rights: planning only
validates declarations, and reports `provenance_status=declarations_only`.
The preparer must separately verify source rights and immutable fetches.

Each JSONL row requires string `question_id`, `category`, `source`, `src_id`,
and `turns` containing one through five nonempty strings. Optional
`sub_category` and `difficulty` are nonempty strings or null. Optional
`multiturn` is a boolean agreeing with the turn count. Other fields are rejected.
Question IDs are unique across the entire corpus and at most 128 characters.
Source IDs may contain up to 65,536 characters, accommodating long upstream
RoleBench identifiers; they are hashed but never printed in the plan.
Optional string metadata is limited to 512 characters; source URLs to 2,048.
Metadata rejects Unicode controls, format controls, surrogates and line/paragraph
separators. Prompt text is retained without normalization, including newlines.

Qualitative categories are `coding`, `stem`, `humanities`, `math`,
`multilingual`, `qa`, `rag`, `roleplay`, `reasoning`, `summarization`, `writing`.
The default selection is coding, reasoning, RAG and writing. Throughput
categories are `high_entropy`, `mixed`, `low_entropy`, all selected by default.
Explicit selections must be unique, nonempty subsets of the config's categories.

Limits are 64 KiB per manifest, 128 MiB per corpus, 10,000 rows, 1 MiB per row,
and 128 source declarations. Inputs must be regular files. Use a quiescent,
privately prepared directory: static symlink escapes are rejected, but this
offline reader is not a sandbox against concurrent ancestor-directory swaps.
Do not let another process rewrite input files during planning.

All rows are validated before sampling, including categories not requested.
Duplicate JSON keys, non-object rows, invalid UTF-8, unknown fields, non-finite
numbers and unresolved upstream text fail before selection. Any turn containing
`FULL BENCHMARK DATA SHOULD BE FETCHED FROM THE SOURCE USING SPECDEC_BENCH`
requires preparation and is rejected. No prompt appears in a validation error.

### Reproducibility

Canonical JSON uses UTF-8, sorted keys, compact separators, `ensure_ascii=false`
and `allow_nan=false`, without Unicode normalization. A row digest covers the
complete row, ordered turns and optional metadata. Absent optional fields and
explicit nulls therefore have different identities.

Within each requested category, rank by SHA-256 of canonical JSON
`["anvil-speed-selection/v1", seed, question_id]`, breaking ties by question ID.
Categories are sorted. Defaults are seed 42 and eight rows per category;
insufficient strata fail instead of silently shrinking the sample.
The seed is an unsigned 64-bit integer; the sample count is 1 through 10,000,
subject to available rows.

Plans include counts, selected IDs, row digests, turn counts, dataset/preparer revisions,
source-registry digest, raw manifest/corpus hashes and selection controls.
`plan_sha256` hashes canonical JSON of the complete plan excluding that field.
Reordering JSONL rows preserves selected IDs/order and row digests, but changes
the raw corpus and plan identities. Throughput `nominal_input_tokens` is the
bucket label (1K = 1,024), not measured tokenizer usage.

## Delivery sequence and acceptance gates

1. **Offline contract and CLI — implemented in this source change.** Validate
   all input, bind selection/content identity and expose the short command above.
   Use original synthetic fixtures for malformed input, bounds, multi-turn
   identity, determinism, source declarations and no-network/no-write checks.
   Keep independent reviewers separate from the author.

2. **Pinned preparation and cache.** Review the pinned upstream loader and all
   transitive downloads. Implement `speed prepare` with a conventional config,
   preview/confirmation, time/download/disk bounds and an isolated helper
   environment for Parquet/tokenizer dependencies. Keep the product runtime
   stdlib-only. Resolve masks, pin every source, retain license decisions,
   atomically publish the manifest and demonstrate hash-verified offline reuse.
   Refuse partial caches, mutable references and content drift.

3. **Managed endpoint jobs.** Add a separately versioned speed profile and
   explicit dispatch in existing job specification/worker seams. Preserve the
   exact legacy `benchmark-profile/v1` context/agentic/SWE suite contract and
   hashes. Reuse existing ownership, credentials, preflight, cancellation, logs
   and artifacts; do not build another queue. Revalidate frozen corpus/plan
   digests before requests. Send each next turn with actual preceding replies.
   Fake endpoints must prove deadline/cancellation behavior, partial evidence,
   identity-drift refusal, reasoning handling and exact token usage.

4. **Optional speculation telemetry.** Start with one explicitly versioned
   engine adapter. Attribute drafted/accepted counters and verification-step
   histograms to the run; counter resets or unrelated traffic invalidate them.
   Record rate denominators and bonus-token conventions. Missing metrics stay
   null with reasons. HTTP chunks cannot establish token-level ITL or accepted
   token counts. Telemetry does not block ordinary endpoint reporting.

5. **Comparison and operator integration.** Add speed artifacts/reports to the
   existing evidence surface. Require matched workload, sampling, output,
   reasoning, cache and concurrency populations. Report TTFO, visible TTFT, E2E,
   actual lengths, per-request TPOT and aggregate throughput alongside counts,
   failures and repetition variation. Incomplete cells cannot claim wins.
   Wire CLI/controller/MCP using existing authorization and confirmation rules.
   Run focused compatibility/privacy checks and full regression before release.

6. **Bounded live pilot.** Resolve the incumbent and capacity from private owner
   state. Initialize coverage/gaps, source registry, pinned plan, starting state,
   launcher identity and restoration plan before the first probe. Preview the
   budget; then run independent preflight, smoke and explicitly authorized
   measurements. Start with the four qualitative strata plus all three entropy
   strata at nominal 2K/32K, concurrency 1/4 only within verified capacity, and
   at least three measured repetitions after separate warmups. Use matched
   speculation-off/on recipes and balanced arm order where feasible. If no
   compatible draft fits, label the useful baseline single-arm and leave A/B
   blocked. Stop on identity drift, correctness failure, OOM/runtime failure or
   reserve breach. Capture owner logs and verify restoration and routed-client
   acceptance. Publish the finding, index, run catalog, dossier and measured
   hardware page at campaign closure. Promotion remains a separate human gate.

Preparation, execution and live qualification are unmeasured until their own
artifacts exist. Rollback of this additive integration disables the optional
suite and restores the captured recipe; it never rewrites old benchmark evidence.
