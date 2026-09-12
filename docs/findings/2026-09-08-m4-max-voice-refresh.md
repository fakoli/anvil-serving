# M4 Max local voice refresh: functional candidates, retained strict failures

**Date:** 2026-09-08

**Publication review:** 2026-09-12. All service, deployment, and harness
statements below describe September 8 observations. This publication reuses
retained sanitized artifacts; it makes no live request, deploys no code, and
does not attest to current fleet state. The capture used dirty source, and its
historical evidence-author model is unrecorded. The earlier native-dispatch
and SDK-harness edits are outside this documentation-only change; their
current release status is not established by these artifacts.

**Measured hardware:** Apple M4 Max laptop, 48 GB unified memory

**Topology:** same-host local voice lane. STT was Parakeet.cpp TDT 0.6B v3 on
Metal and TTS was Kokoro FastAPI on MPS. This does not measure the reference
Mini-to-Dark proxy topology.

**Decision:** `no-promotion` for the LLM lane. The existing Qwen3 4B LLM
configuration remains unchanged. The bounded candidate sweep is complete; a
separately verified Kokoro TTS runtime update was deployed.

<!-- benchmark-result-card/v1 -->
## Result card

> On one Apple M4 Max laptop, pinned Qwen3.5-9B passed functional preflight but
> failed strict spoken, patch-format, and controlled-output gates; it is not a
> performance-qualified voice replacement.

| Setup | Local result |
| --- | --- |
| Baseline | `mlx-community/Qwen3-4B-Instruct-2507-4bit@50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`; MLX-LM 0.31.3 / MLX 0.31.2 |
| Candidates | Qwen3.5-9B `8b2b98c00a6b4d291155e4890773ca8f769aee53`; Qwen3.8-27B `3e6447f082e89cc7f0bc6e5441afd38dfce760ff`; Qwen3.6-35B-A3B `38740b847e4cb78f352aba30aa41c76e08e6eb46` |
| Measurement path | direct loopback endpoints with retained mixed cold/warm preflights; 4K/C1 strict unique-cache capacity scout; one synthesized STT/TTS round trip |
| Contract | 12 spoken-assistant cases × 3 repeats; 32-word controlled output, 10 requests; thinking disabled |
| Evidence | local `functional`, diagnostic `quality`, failed `capacity`; Qwen3.8 compatibility-only evidence is incomplete |
| Decision | `no-promotion` for LLMs; Qwen3 4B LLM configuration unchanged; Kokoro 0.8.2 TTS runtime deployed separately |

| Headline measurement | Local result | Conditions |
| --- | ---:| --- |
| Functional preflight | **6/6 pass** | Qwen3.5-9B: smoke, JSON, 4K needle, tools 3/3, streaming tools, continuation |
| Spoken suite | **33/36 strict** | Qwen3.5-9B; `Tea.` failed the native exact `tea` validator |
| Patch-format diagnostic | **0/3** | Qwen3.5-9B built-in quality subset |
| Strict capacity | **0/10** | 4K/C1, 32 requested words; canaries 10/10, first retained sample observed 28 code words |
| Qwen3.6 spoken suite | **36/36 strict** | bounded exact-question suite; separate strict JSON preflight failure remains |
| Synthesized audio round trip | **WER 0.0; 1896.72 ms** | one Parakeet/Kokoro sample only |
| Kokoro 0.8.2 candidate round trip | **WER 0.0; 2139.31 ms** | one synthesized sample only; candidate endpoint, not a speed comparison |
| Deployed TTS Realtime follow-up | **TTFA 353.53 ms; E2E 2186.88 ms** | one warm session after repeated captures; no speed ratio |

**Why it matters:** functional compatibility alone would conceal strict output
failures that make a voice-lane performance comparison invalid.

**Important caveat:** the audio measurement is one synthesized sample, not a
corpus or perceptual-quality proof. Qwen3.8 and Qwen3.6 exploration do not
change this point-in-time decision.

Evidence: [artifact manifest](2026-09-08-m4-max-voice-refresh-evidence/artifact-manifest.json)
· [evidence index](2026-09-08-m4-max-voice-refresh-evidence/README.md)
· [publication summary](2026-09-08-m4-max-voice-refresh-evidence/publication-summary.md)

## Immutable identity and scope

The retained 4B baseline is
`mlx-community/Qwen3-4B-Instruct-2507-4bit@50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`
on MLX-LM 0.31.3 and MLX 0.31.2. The completed candidate is
`mlx-community/Qwen3.5-9B-4bit@8b2b98c00a6b4d291155e4890773ca8f769aee53`.
Artifact verification passed for all three candidate conversions. Thinking was
disabled with `enable_thinking=false` through the chat template; this is an
observed control, not exhaustive runtime attestation.

Qwen3.8-27B uses
`mlx-community/Qwen3.8-27B-4bit@3e6447f082e89cc7f0bc6e5441afd38dfce760ff`.
It is a separate Apple Silicon lane from the existing large Qwen3.8 dossier,
which records other hardware. Qwen3.6-35B-A3B uses
`mlx-community/Qwen3.6-35B-A3B-4bit@38740b847e4cb78f352aba30aa41c76e08e6eb46`;
the Hub metadata was created or modified 2026-04-16. Its native `qwen3_5_moe`
MLX architecture is supported; the managed private service manifest declares a
24,576 MiB budget, while the conservative 24 GiB policy reserve interval overlaps demand. It is
bounded exploration only, not a measured-memory or all-gates-pass claim.

## Functional gates

The existing 4B baseline failed the shared-prefix tools group 0/3, while its
spoken suite passed 36/36 strict. Qwen3.5-9B passed smoke, JSON, a 4K needle,
shared-prefix tools 3/3, streaming tools, and tool-result continuation. These
six functional gates establish compatibility, not a promotion decision.

Qwen3.8 preliminary preflight passed 5/6 groups. The shared-prefix tool group
passed 1/3; its one retained successful sample took 35.463 seconds at 5,481
prompt tokens. The two failed exceptions were not individually serialized, so
this finding does not assign a failure cause. It was too slow for the local
voice goal and did not advance to capacity qualification.

Qwen3.6-35B-A3B preliminary preflight also passed 5/6 groups. It passed tools
3/3, streaming tools in 0.7 seconds, tool-result continuation in 0.8 seconds,
and a 4K-target needle in 2.5 seconds, but failed strict JSON: the model's
answer was `{"error":"Invalid request. The user's input does not match the required format."}`
rather than the required `language`/`ok` object. This is a bounded format
failure, not a universal model-quality conclusion. Spoken and SDK voice results
then completed: the spoken suite passed 36/36 strict, and one SDK session had
no acceptance errors, successful cancellation, and completed follow-up `54.`.
The cancelled response TTFA/latency was 3890.11/3907.36 ms; the follow-up was
484.69/664.81 ms. This is N=1 with different warm/cache histories, so it does
not support a cross-model speed ratio. The strict JSON failure persists and
blocks qualification.

## Negative results retained

The Qwen3.5-9B diagnostic built-in quality score was 9/12, with patch-format
0/3. Its spoken assistant suite was 33/36 strict because all three memory-case
answers were `Tea.` instead of the exact expected `tea`. That answer is
semantically correct, but the native evidence remains strict and was not
regraded. This tiny exact-question suite does not establish broad inferiority
to the baseline's 36/36 result.

The 4K/C1 unique-cache capacity scout requested 32 controlled output words in
ten requests. All ten canaries passed. The first retained sample observed 28
code words, and all ten requests failed strict adherence. The retained evidence
does not support calling this an OOM or parser failure. It makes every request
performance-ineligible, so no throughput or latency headline is published.

## Voice path and Realtime protocol

The Parakeet/Kokoro synthesized round trip returned WER 0.0 in 1896.72 ms for
one sample. It is useful as an end-to-end path smoke only; it is not a corpus
accuracy, latency distribution, or speech-quality claim.

The retained STT runtime is Parakeet.cpp tag `v0.5.0` at
`1bfbebfaaf493866f49597cd3b7901959d395c60`, with local ggml-submodule
modifications retained in the native runtime record. The campaign started with
Kokoro FastAPI `0.8.0rc1` at `577595854864fa014b041f5120a84810558942dc`.
Upstream `v0.8.2`, released 2026-09-05 at
`58b08a915b3463cb76e376a2867e04f9d828f4df`, was evaluated in an isolated
frozen-lock environment and then deployed to the baseline TTS port. Its one
synthesized candidate round trip had WER 0.0 in 2139.31 ms (STT 94.66 ms, TTS
2044.66 ms); this is one path smoke, not a quality or speed comparison. The
initial candidate definition failed warmup because relative asset paths resolved
from the repository root; the retained native failure records the error and the
pinned definition was corrected with absolute paths. The deployed runtime keeps
the Kokoro 0.9.4 model and voice asset hashes unchanged. Versioned TTS/proxy
supervisor definitions use `KeepAlive=true` and `ThrottleInterval=10`; prior
definitions were disabled and retained for rollback.

The Qwen3.5-9B official OpenAI Python SDK capture initially observed one
inflight transcript after `response.cancel` was sent. The historical dirty-worktree harness treated
the standard server `response.done` event with cancelled status as the
cancellation fence. The current main-branch harness still uses the client-send
fence; this publication does not change or qualify that behavior. Subsequent
September 8 baseline and Qwen3.6 captures passed the modified fence with
their own model configurations; they are not a same-model Qwen3.5 repeat. The
original Qwen3.5 negative capture remains retained as diagnostic evidence.

The existing long-running managed proxy then passed the corrected fence with
no acceptance errors: both audio utterances were recognized, no event appeared
after either the client cancel request or the cancellation acknowledgement, and
the completed answer `54.` had TTFA 1678.46 ms and end-to-end latency 2740.76
ms. The first cancelled response TTFA was 3559.19 ms. Exact package versions
and source-file hashes remain in the private runtime receipt. The acknowledgement
boundary follows the official [Realtime cancel event documentation](https://platform.openai.com/docs/api-reference/realtime-client-events#response/cancel).

After the TTS deployment, a fresh corrected SDK wrapper exited zero with no
acceptance errors, observed the cancelled acknowledgement, and completed the
follow-up answer `54 countries in Africa.` at TTFA 353.53 ms and end-to-end
2186.88 ms. It is one warm session after repeated captures, so it provides no
cross-run speed ratio. Two earlier wrapper invocations raised Python invocation
errors after valid captures; they are retained privately and are not described
as pipeline failures. Reboot, physical microphone/OpenClaw client, and actual
rollback execution were not tested.

## Feasibility and research

The local feasibility screen uses an 8,192-token requirement and a 24 GiB
host/co-resident reserve on 48 GB unified memory. Qwen3.5-9B and Qwen3.8-27B
were benchmark survivors; Qwen3.6-35B-A3B crossed the conservative policy
interval and remains unresolved despite a physical-fit bound. This screen is
not behavioral proof.

Official Qwen repositories, the exact MLX community conversion repositories,
LiquidAI LFM2.5-8B-A1B, and Hugging Face speech-to-speech 1.0.0 were reviewed
on 2026-09-08. The LFM item is only a future Pythonic-tools/reasoning tradeoff
lead. Hugging Face speech-to-speech 1.0.0 was released 2026-09-06, but Anvil
uses its native pipeline, so no blind package update occurred.

## Decision and promotion boundary

No model is promoted. All three candidates were unloaded and unregistered; both
candidate ports are closed. The 9B candidate failed strict gates, Qwen3.8 was
too slow with an incomplete tool group, and Qwen3.6 retained its strict JSON
failure. Qwen3.6 is a stronger bounded spoken challenger than 9B, but not a
fully qualified replacement because JSON failed. The existing 4B LLM
configuration has not changed. Four local jobs were adopted in the private
service inventory; the stopped proxy was repaired with ordered dependencies and
STT `/health` readiness. Kokoro FastAPI 0.8.2 and versioned TTS/proxy
supervisor definitions are the accepted audio-runtime changes. Baseline LLM,
STT, TTS, and Realtime endpoint checks all returned HTTP 200. The live voice
configuration bytes are unchanged since the audio update began; this does not
claim identity with an unavailable pre-campaign byte digest.

## Evidence and redaction

The [sanitized bundle](2026-09-08-m4-max-voice-refresh-evidence/README.md)
keeps preserved native JSON plus derived navigation summaries, exact revisions,
metrics, and material failures. It replaces personal paths with `<operator-home>` or `<model-cache>`,
host identity with `apple-m4-max-laptop`, and excludes audio, base64, and raw
SDK streams. Private raw evidence remains operator state.
