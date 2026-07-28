# STT corpus and evidence reference

## Corpus

Each non-blank JSONL line uses `schema_version = "stt-corpus/v1"` and contains:

- `id`: unique stable case ID.
- `audio_path`: relative path resolved against the manifest directory.
- `reference_text`: non-empty ground truth.
- `category`: aggregate key; `librispeech-*` is human and `synthetic-*` is
  Kokoro-generated.
- `language`: expected language code.
- `source_identity`: archive/sample or generator identity.
- `license`: attribution or generated-fixture statement.
- `sha256`: lowercase SHA-256 of the audio bytes.

Audio must be 16-kHz mono WAV or FLAC. Manifest identity is the SHA-256 of the
exact JSONL bytes.

## Candidate overlay

Use only `[voice.stt]` and optional `[stt_benchmark.identity]`. Identity accepts
`served_name`, `checkpoint`, `revision`, `runtime`, `runtime_version`, `image`,
`image_digest`, and `hardware`. Secrets remain environment references.

## Evidence

`stt-benchmark-evidence/v1` records:

- exact corpus, endpoint, model, and runtime identity;
- one cold request outside warm aggregates;
- every warm case transcript, failure, WER/CER counts, latency, RTF, and
  repetition flag;
- aggregate, primary-human, synthetic-agent, and category summaries;
- wall-clock throughput and concurrency schedule;
- optional automatic-language probes;
- `complete`, request counts, and gate observations.

An incomplete artifact is useful failure evidence but is not qualification
evidence. Atomic output preserves the previous artifact if replacement fails.
