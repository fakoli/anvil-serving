---
name: anvil-serving-stt-benchmark
description: Prepare, validate, execute, compare, and publish reproducible multi-sample speech-to-text qualifications in anvil-serving. Use for STT model bakeoffs, corpus-based WER/CER and latency testing, candidate overlays, concurrency tests, restoration checks, and fail-closed stt-benchmark-evidence/v1 artifacts. Do not use for a single voice-pipeline turn or LLM qualification.
---

# Anvil Serving STT Benchmark

Use this skill for corpus-based STT candidate qualification. Keep lifecycle in
the `anvil-serving` CLI and managed serve manifests; do not add skill scripts.

## Start Here

1. Read `README.md`, `CLAUDE.md`,
   `docs/OPERATOR-SKILLS-AND-SUBAGENTS.md`, and
   `references/corpus-and-evidence.md`.
2. Record the command host, GPU identity, live containers, endpoint health,
   model names, image identities, and router audio route before mutation.
3. Keep reference OpenClaw testing on Fakoli Dark. Fakoli Mini remains
   model-free. Use `127.0.0.1` for same-host endpoints.
4. Treat readiness, hermetic tests, external claims, and local qualification as
   separate evidence classes. Only recorded live corpus runs are local
   qualification.

## Prepare and Validate

Build the fixed English corpus through the CLI:

```text
anvil-serving voice corpus prepare --config <voice.toml> --out <workspace/corpus>
anvil-serving voice corpus validate --manifest <workspace/corpus/manifest.jsonl> --expected-cases 30
```

Preserve the OpenSLR archive checksums, the deterministic selection manifest,
selected audio hashes, generated Kokoro samples, and attribution. Do not commit
the full LibriSpeech archives.

Stop on duplicate IDs, missing references, path escapes, unsupported audio,
non-16-kHz/non-mono inputs, hash mismatches, or a case-count mismatch.

## Run a Candidate

Use an STT-only overlay. Never use an LLM candidate overlay for this workflow.

```text
anvil-serving voice benchmark --scope stt \
  --config <voice.toml> \
  --corpus <manifest.jsonl> \
  --repetitions 3 \
  --concurrency 1 \
  --stt-candidate-overlay <candidate.toml> \
  --evidence-out <candidate-sequential.json>
```

Run the concurrency lane separately:

```text
anvil-serving voice benchmark --scope stt \
  --config <voice.toml> \
  --corpus <manifest.jsonl> \
  --repetitions 1 \
  --concurrency 4 \
  --stt-candidate-overlay <candidate.toml> \
  --evidence-out <candidate-concurrency4.json>
```

Use `--auto-language-probes 6` only for the bounded human-sample language-tag
probe. It does not qualify multilingual behavior.

Run the current baseline first. Start candidates sequentially through their
managed serves manifest, never by replacing the production STT port or router
route. Preview any lifecycle change. If the approved memory fallback is
needed, pause only the explicitly authorized service and capture its exact
pre-run identity for restoration.

## Interpret Evidence

Require `stt-benchmark-evidence/v1`, `complete=true`, the expected request
count, zero malformed/empty/timed-out cases, and zero repetition flags before
considering quality or latency. Use `summary.primary_human` for the primary
quality decision and report `summary.synthetic_agent` separately.

Compare normalized micro-WER, raw case/punctuation-sensitive CER, warm p50/p95,
RTF, and concurrency throughput. Apply the task's explicit decision rules; do
not invent a promotion rule or change the default route.

After the final candidate, restore any approved temporary pause and verify its
container, image, model, endpoint, and health against the pre-run record.
Reverify STT, TTS, router, and Heavy independently. Report every restoration
difference or failure.

## Publish and Reflect

Delegate publication to
`skills/anvil-serving-benchmark-docs/SKILL.md`. Publish raw JSON links and a
dated finding with checkpoint revision, image digest, measured hardware,
protected/co-resident topology, corpus provenance, schedule, primary and
synthetic metrics, failures, restoration state, capability boundaries, and a
non-promotion decision. Update the run catalog, model dossier, and measured
hardware page through that canonical publication contract.

Maintain a friction log while running:

- manual workaround;
- ambiguous output;
- missing identity;
- unsafe default;
- repeated command.

Before closing, revise the CLI or skills for recurring friction, validate every
documented command, run the skill validator, and regenerate the full CLI
reference audit.
