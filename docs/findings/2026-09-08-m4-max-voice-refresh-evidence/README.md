# M4 Max voice refresh evidence

Published from retained artifacts on 2026-09-12. Runtime/deployment claims are
historical September 8 observations. No live services or model choices changed
during publication, and the earlier runtime/harness edits are not included.
The capture's dirty source and unrecorded evidence-author model limit historical
reproducibility and reviewer-independence claims.

This is the sanitized public evidence bundle for the
[dated finding](../2026-09-08-m4-max-voice-refresh.md). It records a local,
same-host Apple Silicon voice-lane review, not the reference Mini-to-Dark
topology. Private host names, paths, service identities, UIDs, PIDs, audio,
base64 payloads, and raw SDK event streams were removed. Model snapshot paths
are represented as `<model-cache>`.

## Campaign boundary

- **Campaign ID:** `2026-09-08-m4-max-voice-refresh`
- **Capability:** local voice LLM, STT/TTS round trip, and Realtime protocol
- **Repository revision:** `b85b5d27bc8da84216bcc7f5b5704dd340a884a6` plus later
  harness-fence work; bounded candidate sweep and restoration complete.
- **Measured hardware:** Apple M4 Max laptop with 48 GB unified memory.
- **Evidence labels:** `functional`, bounded diagnostic `quality`, failed
  `capacity`, and incomplete compatibility-only Qwen3.8 evidence.
- **Decision:** `no-promotion` for the LLM lane; the existing Qwen3 4B LLM
  configuration is unchanged, while Kokoro FastAPI 0.8.2 was separately deployed.

## Common campaign artifacts

- [`artifact-manifest.json`](artifact-manifest.json) — ten-role ledger and hashes
- [`source-registry.json`](source-registry.json) — dated source provenance
- [`summary.json`](summary.json) — bounded machine-readable decision
- [`friction-log.md`](friction-log.md) — retained failures and durable fixes
- [`restoration.json`](restoration.json) — verified final restoration record
- [`publication-summary.md`](publication-summary.md) — derivative claim ledger

## Raw run evidence

The [`native/`](native/) directory retains sanitized original JSON schemas,
including all four preflights, three spoken suites, diagnostic quality, strict
capacity, baseline/audio voice records, SDK session summaries, feasibility,
runtime and audio-version identity, artifact verification, thinking controls,
candidate startup failure, final deployment/restoration, and the source review.
[`native/redactions.json`](native/redactions.json)
records every removed private path, host identity, and audio/base64 field.

- [`native/candidate-preflight.json`](native/candidate-preflight.json) and
  [`native/qwen36-preflight.json`](native/qwen36-preflight.json) retain the
  failed answers, validators, and timing evidence.
- [`native/candidate-capacity.json`](native/candidate-capacity.json) retains
  all ten strict requests and their canary outcomes.
- [`native/final-restoration-receipt.json`](native/final-restoration-receipt.json)
  and [`native/final-deployment-receipt.json`](native/final-deployment-receipt.json)
  retain the verified LLM restoration and accepted Kokoro 0.8.2 deployment
  without private digests.
- [`sanitized-preflight.json`](sanitized-preflight.json),
  [`sanitized-quality-and-capacity.json`](sanitized-quality-and-capacity.json),
  and [`sanitized-voice-summary.json`](sanitized-voice-summary.json) are
  explicitly labeled derivative navigation summaries.

## Decision and publication

The 9B candidate passed six functional preflight groups but did not pass the
strict spoken, patch-format, or controlled-output gates. Qwen3.8 preliminary
preflight passed 5/6 groups but shared-prefix tools passed only 1/3 and it was
too slow for the voice goal; it did not advance to capacity. Qwen3.6-35B-A3B
also passed 5/6 preliminary groups but failed strict JSON; spoken and SDK voice
work then passed 36/36 strict and one accepted SDK session, but neither result
overrides the JSON failure. None of these results authorizes an LLM route,
serve, or client-catalog change. The separately tested Kokoro 0.8.2 runtime
was deployed after verified endpoint and fresh Realtime checks; it does not
promote an LLM candidate.

See the [publication summary](publication-summary.md) for claim-safe copy.
