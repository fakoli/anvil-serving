# Voice LLM MLX local lane

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** local Apple Silicon voice-LLM candidate lane.
    - **Selected or best-qualified configuration:** None. Qwen3.5-9B completed functional qualification but failed strict quality and capacity gates; Qwen3.8-27B and Qwen3.6-35B-A3B are incomplete as replacements.
    - **Measured hardware:** Apple M4 Max laptop, 48 GB unified memory; same-host STT/TTS only.
    - **Evidence:** `functional`, diagnostic `quality`, failed `capacity`, and compatibility-only evidence. Qwen3.5-9B preflight 6/6; spoken suite 33/36 strict; strict capacity 0/10.
    - **Decision:** `no-promotion` for LLMs; no LLM route, serve, or client-catalog change. Kokoro 0.8.2 was separately deployed as an audio-runtime update.
    - **Important limitation:** no performance-eligible capacity population; one audio round trip is not corpus evidence.
    - **Review dates:** evidence included through 2026-09-08; archival publication reviewed 2026-09-12.

All deployment statements describe the September 8 capture. This documentation
does not attest to current services or ship the historical dirty-worktree
native-dispatch/SDK-harness edits. The historical evidence-author model was
not recorded; current publication review does not establish its independence.

### Review narrative

#### 2026-09-08 — strict gates prevent a local voice replacement claim

Qwen3.5-9B passed functional compatibility but missed exact spoken formatting,
patch-format diagnostic quality, and strict controlled output. Qwen3.8-27B
preflight was incomplete and too slow for the voice goal. Qwen3.6-35B-A3B is a
bounded exploration with unresolved policy memory. **Outcome:** retain all
three as unpromoted local candidates and leave the existing 4B configuration
unchanged.

## Immutable identity

### Qwen3.5-9B MLX 4-bit

- **Model:** `mlx-community/Qwen3.5-9B-4bit@8b2b98c00a6b4d291155e4890773ca8f769aee53`.
- **Runtime:** MLX-LM 0.31.3; MLX 0.31.2.
- **Artifacts:** conversion verification passed; model snapshot path is not public.
- **License:** Not recorded in retained public evidence.

### Qwen3.8-27B and Qwen3.6-35B-A3B MLX 4-bit

- **Models:** `mlx-community/Qwen3.8-27B-4bit@3e6447f082e89cc7f0bc6e5441afd38dfce760ff`; `mlx-community/Qwen3.6-35B-A3B-4bit@38740b847e4cb78f352aba30aa41c76e08e6eb46`.
- **Runtime:** MLX-LM local native lane; Qwen3.6's `qwen3_5_moe` architecture is supported.
- **Artifacts:** conversion verification passed for Qwen3.8 and Qwen3.6.
- **License:** Not recorded in retained public evidence.

## Tested hardware and topology

- **Measured:** Apple M4 Max laptop with 48 GB unified memory.
- **Protected or co-resident:** local Parakeet.cpp TDT 0.6B v3 Metal STT and
  Kokoro FastAPI 0.8.2 MPS TTS; its runtime update is separately evidenced and
  is not a separate LLM comparison.
- **Execution mode:** same-host direct loopback local voice lane.
- **Comparability boundary:** this is not comparable to the Qwen3.8 27B RTX
  5090 or RTX PRO 6000 dossier lanes, nor the model-free Mini-to-Dark reference topology.

## Engine, quantization, KV, context, and concurrency recipe

The managed recipe is private because it contains operator topology. The
[public configuration guide](../configurations.md) explains reconstruction
principles; the controls below describe this lane. Exact private service files,
pinned working-directory links, and installed environments are not a complete
public reproduction bundle. Reconstruct and review them before any managed load.

### Completed Qwen3.5-9B lane

- **Engine and image:** MLX-LM 0.31.3 / MLX 0.31.2; no container image.
- **Weights and KV:** MLX community 4-bit conversion; no KV quantization override recorded.
- **Topology:** one Apple Silicon unified-memory device; same-host local endpoints.
- **Contract:** 4K/C1 strict capacity scout, 32 controlled output words; thinking disabled via `enable_thinking=false`.
- **Runtime controls:** `chat_template_args.enable_thinking=false`; server
  `max_tokens=512`, voice `max_tokens=256`, decode and prompt concurrency 1,
  cache two entries/512 MB; strict scout uses unique prompt cache and request canaries.
- **Recipe:** private managed native recipe; public reconstruction above.

### Incomplete 27B and 35B-A3B lanes

- **Engine and image:** MLX-LM local native lane; no container image.
- **Runtime controls:** MLX-LM 0.31.3 / MLX 0.31.2, the same
  `enable_thinking=false`, 512/256 server/voice token limits, C1 decode/prompt,
  two-entry/512 MB cache, and no KV quantization override recorded.
- **Contract:** Qwen3.8 stopped after preliminary preflight; Qwen3.6 bounded exploration is complete.
- **Limits:** neither lane has an eligible capacity result or promotion decision.

## Evidence by measurement class

### Qwen3.5-9B functional, quality, and capacity

- **Status:** `functional` pass; diagnostic `quality` and strict `capacity` failures.
- **Measured:** preflight 6/6 groups pass; spoken assistant 33/36 strict;
  built-in diagnostic quality 9/12 with patchformat 0/3; capacity 0/10 strict,
  while all ten canaries passed.
- **Limits:** the native exact validator counts `Tea.` as a failure against
  `tea`; all canaries passed 10/10, the first retained sample had 28 rather
  than 32 controlled words, and all ten requests failed strict adherence. This
  is not an OOM or parser-failure claim. No request is performance eligible.
- **Evidence:** [dated finding](../../findings/2026-09-08-m4-max-voice-refresh.md)
  · [sanitized evidence](../../findings/2026-09-08-m4-max-voice-refresh-evidence/README.md).

### Qwen3.8-27B preliminary compatibility

- **Status:** `compatibility-only`, incomplete.
- **Measured:** preflight 5/6 groups pass; shared-prefix tools 1/3, with the
  only retained successful sample at 35.463 s and 5,481 prompt tokens.
- **Limits:** individual failed exception causes were not serialized. It was too slow for the local voice goal and did not advance to capacity.
- **Evidence:** [dated finding](../../findings/2026-09-08-m4-max-voice-refresh.md).

### Qwen3.6-35B-A3B preliminary compatibility

- **Status:** `compatibility-only`, incomplete.
- **Measured:** 5/6 preflight groups; tools 3/3, streaming tools 0.7 s,
  continuation 0.8 s, a 4K-target needle 2.5 s, spoken suite 36/36 strict,
  and one accepted SDK cancellation/follow-up session.
- **Limits:** strict JSON returned an invalid-request object rather than the
  required `language`/`ok` object. One SDK session's cancelled/follow-up
  TTFA/E2E values are 3890.11/3907.36 and 484.69/664.81 ms, but differing
  warm/cache histories make cross-model speed comparison invalid.
- **Evidence:** [dated finding](../../findings/2026-09-08-m4-max-voice-refresh.md).

### Voice path and Realtime protocol

- **Status:** bounded functional path evidence.
- **Measured:** the baseline synthesized Parakeet/Kokoro round trip had WER 0.0
  and 1896.72 ms. The Kokoro 0.8.2 candidate round trip had WER 0.0 and
  2139.31 ms; after deployment, one warm Realtime follow-up completed at
  TTFA/E2E 353.53/2186.88 ms with no acceptance errors. The existing
  long-running proxy separately passed the corrected fence with two recognized
  utterances and completed TTFA/E2E 1678.46/2740.76 ms.
- **Limits:** each audio measurement is one sample, not corpus or perceptual
  quality evidence; the final capture is warm after repeated captures and has
  no speed-ratio meaning. Raw audio and SDK events are intentionally not retained publicly.
- **Evidence:** [voice summary](../../findings/2026-09-08-m4-max-voice-refresh-evidence/sanitized-voice-summary.json).

## Decision and promotion state

!!! warning "Promotion remains human-gated"

    A configuration, load, health check, benchmark, qualification, or
    documentation update does not authorize a serve, route, or client-catalog change.

### Retained or selected

- **Existing Qwen3 4B:** unchanged local LLM configuration; baseline spoken suite
  passed 36/36, but shared-prefix tools were 0/3 in this refresh.

### Rejected, superseded, or incomplete

- **Qwen3.5-9B:** `no-promotion`; functional pass does not overcome strict
  spoken, patch-format, and controlled-output failures.
- **Qwen3.8-27B:** incomplete `no-promotion`; too slow and tool group 1/3.
- **Qwen3.6-35B-A3B:** `no-promotion`; 36/36 bounded spoken result and one
  accepted SDK session do not override strict JSON failure or the unresolved
  conservative 24 GiB policy envelope.

## Failures and gotchas

### Evidence and interpretation limits

- **Strict output:** 0/10 controlled-output completions means timing cannot be
  published as capacity performance.
- **Audio samples:** the baseline 1896.72 ms and Kokoro 0.8.2 2139.31 ms WER
  results are each one synthesized sample only.
- **Hardware lane:** Apple unified-memory results are not cross-hardware rankings.

### Runtime, topology, or integration limits

- **Cancellation fence:** the historical dirty-worktree harness used the
  server's `response.done` cancelled acknowledgement. Current main retains the
  client-send boundary; neither policy is changed or newly qualified here.
- **Private operations:** managed service inventory, proxy dependencies, and
  endpoint details remain operator-private; public evidence records outcomes only.

## Dated run history

- 2026-09-08 — [M4 Max local voice refresh](../../findings/2026-09-08-m4-max-voice-refresh.md)
