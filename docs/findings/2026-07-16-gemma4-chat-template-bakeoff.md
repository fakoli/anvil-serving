# Gemma 4 July 15 chat-template bakeoff and Heavy promotion

**Captured:** 2026-07-16<br>
**Host/topology:** Fakoli Dark; RTX 5090 32 GB Fast lane and RTX PRO 6000 96 GB Heavy lane; one LLM serve per GPU during measurement<br>
**Engine:** `vllm/vllm-openai:v0.25.1` (`sha256:e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089`)<br>
**KV cache:** FP8<br>
**Result:** keep the current Fast model; promote official Gemma 4 12B IT QAT W4A16 to Heavy with ThinkingCap rollback

## Why this run happened

Google updated the official Gemma 4 instruction tokenizers on 2026-07-15. This run pinned the new tokenizer revisions independently from the model weights and removed the repository's old explicit Gemma chat-template override. The test matrix covered E2B, E4B, 12B, 26B, and 31B at practical and native context lengths on both Blackwell cards. The official [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4), upstream [vLLM Gemma 4 recipe](https://github.com/vllm-project/recipes/blob/main/Google/Gemma4.md), and exact checkpoint metadata are recorded in [source-registry.json](2026-07-16-gemma4-chat-template-bakeoff-evidence/source-registry.json).

The canonical template SHA-256 was `0a2c8073…1c5b5` for E2B/E4B and `ae53464b…4c6d4` for 12B/26B/31B. Model and tokenizer revisions were pulled by immutable SHA. The 26B row used official BF16 because no official 26B W4A16 checkpoint was available in the tested set.

## Decision

- **Fast stays on `leon-se/gemma-4-E4B-it-FP8-Dynamic`**, served as `gemma4-e4b-it` at 32K. The control passed every repeated quality gate. E2B, new-template E4B, and 12B each failed the strict timeout-triage check with thinking disabled; 12B was also slower. The 26B BF16 checkpoint cannot fit, and 31B cannot serve its 128K native window on the 32 GB card.
- **Heavy is promoted to `google/gemma-4-12B-it-qat-w4a16-ct`**, served as `gemma4-12b-it-w4a16-ct` at 256K with thinking enabled by router default. It matched the control's perfect repeated quality result, reduced context TTFT at every target, greatly improved loaded 32K capacity, and used an official checkpoint/template pair.
- `bottlecapai/ThinkingCap-Qwen3.6-27B-FP8` remains the immediate managed rollback. Its MTP speculative path was removed because the deployed vLLM validation now rejects the checkpoint's compressed-tensors main model plus FP8 MTP-head mismatch. The non-MTP rollback passed health, reasoning evidence, 30K retrieval, and 5/5 tools before promotion; the automatic rollback later passed 240K and 20/20 tools.

## Fast matrix — RTX 5090 32 GB

The capacity rows below are mixed short-generation workloads with fixed context; aggregate output tok/s is not a controlled decode-only rate. Repeated quality used three attempts per check and a 100% pass requirement.

| Candidate | Served window | Quality result | 32K capacity, c1 | 32K capacity, c2 | Context / operational result | Verdict |
|---|---:|---|---:|---:|---|---|
| Legacy E4B FP8-Dynamic control, old embedded template | 32K | **pass**: chat/context/tool/session/intelligence | 0.46 s TTFT; 49 tok/s | 0.58 s; 79 tok/s | 30K retrieval pass | **Keep Fast** |
| Official E2B W4A16, July 15 template | 128K | fail: timeout triage 0/3 | 0.43 s; 96 tok/s | 0.21 s; 204 tok/s | 30K/60K/120K preflight pass | Faster, insufficient strict quality |
| Official E4B W4A16, July 15 template | 128K | fail: timeout triage 0/3 | 0.63 s; 41 tok/s | 0.14 s; 109 tok/s | 30K/60K/120K preflight pass | Does not beat the E4B control |
| Official 12B W4A16, July 15 template | 256K | fail with thinking disabled: timeout triage 0/3 | 1.43 s; 22 tok/s | 1.44 s; 27 tok/s | 240K pass; 78.66 s quality TTFT at 240K | Too slow and no strict-quality win for Fast |
| Official 26B BF16 | none | not run | — | — | 48.57 GiB model allocation; negative 5.74 GiB KV headroom | Cannot fit Fast |
| Official 31B W4A16 | 64K practical | protocol preflight pass at 30K/45K/60K | 2.60 s; 9 tok/s at 30K | 8.81 s; 8 tok/s | 128K startup needs 6.35 GiB KV but only 4.28 GiB is available; estimated ceiling 76,736 | Reject for Fast |

Key Fast evidence: [control quality](2026-07-16-gemma4-chat-template-bakeoff-evidence/fast-control-legacy-e4b-fp8-32k-quality-r3.json), [E2B quality](2026-07-16-gemma4-chat-template-bakeoff-evidence/fast-e2b-w4a16-128k-quality-r3.json), [E4B quality](2026-07-16-gemma4-chat-template-bakeoff-evidence/fast-e4b-w4a16-128k-quality-r3.json), [12B quality](2026-07-16-gemma4-chat-template-bakeoff-evidence/fast-12b-w4a16-256k-quality-r3.json), [26B startup failure](2026-07-16-gemma4-chat-template-bakeoff-evidence/fast-26b-bf16-startup-failure.json), and [31B 128K startup failure](2026-07-16-gemma4-chat-template-bakeoff-evidence/fast-31b-w4a16-128k-startup-failure.json).

## Heavy matrix — RTX PRO 6000 96 GB

| Candidate | Quality result | 32K capacity, c1 | 32K capacity, c2 | Quality context TTFT (32K / 128K / 240K) | Verdict |
|---|---|---:|---:|---:|---|
| ThinkingCap 27B FP8 control, thinking enabled | **pass** | 4.84 s; 3 tok/s | 5.06 s; 3 tok/s | 7.83 / 57.60 / 124.70 s | Valid control and rollback |
| Official E2B W4A16 | protocol preflight pass | 0.45 s; 85 tok/s | 0.19 s; 172 tok/s | 120K capacity TTFT 7.61 s | Too small for Heavy quality selection |
| Official E4B W4A16 | protocol preflight pass | 0.63 s; 41 tok/s | 0.21 s; 101 tok/s | 120K capacity TTFT 9.44 s | Too small for Heavy quality selection |
| Official 12B W4A16, thinking enabled | **pass** | 1.52 s; 21 tok/s | 0.27 s; 54 tok/s | **6.96 / 44.61 / 97.33 s** | **Promote Heavy** |
| Official 26B BF16, thinking enabled | fail: timeout triage 0/3 | 0.73 s; 36 tok/s | 0.31 s; 77 tok/s | 120K/240K capacity TTFT 11.93 / 34.07 s | Fastest larger model, strict-quality failure |
| Official 31B W4A16, thinking enabled | **pass** | 4.02 s; 7 tok/s | 0.41 s; 19 tok/s | 15.44 / 112.30 / 248.57 s | Quality pass, materially slower than 12B |

The repeated Heavy quality artifacts are [ThinkingCap control](2026-07-16-gemma4-chat-template-bakeoff-evidence/heavy-control-thinkingcap-256k-quality-r3.json), [12B](2026-07-16-gemma4-chat-template-bakeoff-evidence/heavy-12b-w4a16-256k-quality-r3.json), [26B](2026-07-16-gemma4-chat-template-bakeoff-evidence/heavy-26b-bf16-256k-quality-r3.json), and [31B](2026-07-16-gemma4-chat-template-bakeoff-evidence/heavy-31b-w4a16-256k-quality-r3.json). Every raw preflight and capacity artifact is retained in the [evidence directory](2026-07-16-gemma4-chat-template-bakeoff-evidence/).

## July 15 template-specific result

The 12B endpoint completed a two-request OpenAI tool round trip using `enable_thinking=true` and `preserve_thinking=true`: first response `finish_reason=tool_calls`, JSON-string arguments `{"city": "Seattle"}`, verbatim assistant replay with the matching `tool_call_id`, then a tool result and correct final answer. This directly exercises the new template's assistant/tool rendering rather than relying only on one-turn tool generation. See [multiturn replay evidence](2026-07-16-gemma4-chat-template-bakeoff-evidence/fast-12b-new-template-multiturn-tool-replay.json).

The tool turns did not expose a non-empty `reasoning_content` field even though thinking was requested. Separate enabled-thinking smoke/JSON gates did expose reasoning and passed the required-evidence policy, so the production claim is limited to: tool replay renders and completes correctly; it does not claim that every tool call carries visible reasoning metadata.

## Live promotion record

The guarded `serves promote gemma4-12b-heavy` transaction was previewed before apply. The first authenticated apply started Gemma, passed the semantic smoke check, JSON, 240K retrieval, and 20/20 tools, but failed evidence policy because the smoke answer reached the 256-visible-token cap with `finish_reason=length`. The transaction automatically restored ThinkingCap, ran its rollback gate at 240K with 20/20 tools, restored the router config/profile, and readmitted Heavy.

The functional promotion gate was corrected to the already-benchmarked 512-visible-token allocation; no check or pass threshold was removed. The second transaction passed:

- disabled-thinking smoke/JSON/240K retrieval/20-tool gate with allowed finish reasons;
- enabled-thinking smoke/JSON gate with required reasoning evidence and 4,096-token reasoning headroom;
- deployed-router profile/config validation;
- router reload and gateway health;
- post-reload exact identity: expected and observed `gemma4-12b-it-w4a16-ct`, readiness `identity_passed`, state `admitting`.

The final artifacts are [functional preflight](2026-07-16-gemma4-chat-template-bakeoff-evidence/promotion-functional-preflight.json), [thinking preflight](2026-07-16-gemma4-chat-template-bakeoff-evidence/promotion-thinking-preflight-4k.json), and [promotion profile](2026-07-16-gemma4-chat-template-bakeoff-evidence/promotion-profile.json). The failed 256-token artifact was overwritten by the CLI's stable output path, but its exact observed failure and automatic rollback are recorded here; all earlier matrix artifacts remain immutable.

## Cache cleanup and caveats

The user approved deletion of six stale, unreferenced model caches before downloads. The cleanup moved the Docker volume from 0 to 483.4 GiB free; pinned Gemma downloads left 370.8 GiB free. Exact repos, measured sizes, and the zero-running-reference check are in [cache-cleanup.json](2026-07-16-gemma4-chat-template-bakeoff-evidence/cache-cleanup.json).

- Cold vLLM 0.25.1 Gemma startup on this WSL2/Blackwell host includes several minutes of graph compilation; warm request latency is reported separately.
- FP8 KV cache may trade some accuracy for capacity. The correctness gates were run on the exact FP8-KV production shape.
- The built-in timeout-triage validator is intentionally strict. Some rejected visible answers were operationally plausible, but promotion uses the recorded deterministic contract rather than an after-the-fact subjective regrade.
- These results apply to the pinned revisions, vLLM 0.25.1, and the two named GPUs. They are not generic family rankings.

## Reproduction surface

All serve lifecycle changes used `anvil-serving serves`; no ad hoc model container is the operational path. Representative commands:

```powershell
anvil-serving serves up gemma4-fast-lab --manifest examples/fakoli-dark/serves.toml --recreate --no-router --confirm
anvil-serving eval benchmark quality --base-url http://127.0.0.1:39037/v1 --model gemma4-fast-lab --suite chat,context,tool,session,intelligence --eval-repetitions 3 --confirm
anvil-serving serves promote gemma4-12b-heavy --manifest examples/fakoli-dark/serves.toml --dry-run
anvil-serving serves promote gemma4-12b-heavy --manifest examples/fakoli-dark/serves.toml --confirm
```

The experiment and production recipes are in `examples/fakoli-dark/docker-compose.experiment.yml`, `examples/fakoli-dark/docker-compose.yml`, and `examples/fakoli-dark/serves.toml`.
