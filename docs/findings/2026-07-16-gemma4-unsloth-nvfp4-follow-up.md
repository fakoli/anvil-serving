# Gemma 4 Unsloth NVFP4 Fast/Heavy follow-up

**Captured:** 2026-07-16<br>
**Host/topology:** Fakoli Dark; RTX 5090 32 GB Fast lane and RTX PRO 6000 96 GB Heavy lane; one LLM serve per GPU during measurement<br>
**Engine:** `vllm/vllm-openai:v0.25.1` (`sha256:e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089`)<br>
**Runtime:** vLLM V1 runner for the existing WSL2 compatibility recipe; native `FlashInferCutlassNvFp4LinearKernel`; Triton attention; FP8 KV cache<br>
**Result:** do not promote an NVFP4 checkpoint; keep Fast E4B FP8-Dynamic and Heavy official 12B QAT W4A16

## Why this run happened

An Unsloth announcement claimed approximately 1.5x faster Gemma 4 NVFP4 quants and published new 12B, 26B-A4B, and 31B checkpoints. The post is a current publisher-authored advisory prior, not local evidence. This follow-up therefore pinned all three repositories by immutable revision, tested the bundled Gemma 4 tool/thinking template, and ran local Fast/Heavy gates before drawing a conclusion.

The source classification, dates, model-card links, and decision impact are in [source-registry.json](2026-07-16-gemma4-nvfp4-evidence/source-registry.json). Checkpoint sizes, revisions, and template metadata are in [checkpoint-metadata.json](2026-07-16-gemma4-nvfp4-evidence/checkpoint-metadata.json).

## Decision

- **Keep Fast on `leon-se/gemma-4-E4B-it-FP8-Dynamic`.** The 26B-A4B NVFP4 checkpoint is exceptionally fast and fits at 32K, but its routed-policy repeated quality run failed timeout triage 1/3. The 12B NVFP4 checkpoint failed the same gate 1/3 and did not beat official 12B QAT in the controlled generation A/B.
- **Keep Heavy on `google/gemma-4-12B-it-qat-w4a16-ct`.** The 12B NVFP4 checkpoint regressed repeated tool argument fidelity, the 26B-A4B checkpoint failed strict quality, and the 31B checkpoint was much slower despite passing quality.
- **Retain 26B-A4B NVFP4 as the most interesting future Gemma speed candidate.** It is the fastest variation in this matrix on both GPUs and supports the full 256K Heavy window, but promotion remains blocked by the quality gate.
- **The advertised 1.5x 12B speedup was not reproduced.** NVFP4 was 7.4% slower than QAT on Fast and 9.3% slower on Heavy in the matched 1,024-token diagnostic.

No router profile, production recipe, or tier recommendation changed.

## Checkpoints and template

| Checkpoint | Revision | Weight size | Tested lanes |
|---|---|---:|---|
| `unsloth/gemma-4-12b-it-NVFP4` | `b1f649734b34aa5575b03d186abd1b9be3d0d5c4` | 8.67 GiB | Fast 32K; Heavy 256K |
| `unsloth/gemma-4-26B-A4B-it-NVFP4` | `20df0542b1a86ce19f495ac2eca2c7c12bce82f9` | 15.75 GiB | Fast 32K; Heavy 256K |
| `unsloth/gemma-4-31B-it-NVFP4` | `373c00b5ecb0a8ee43942b5ca08b93805de8eee4` | 23.06 GiB | Heavy 256K |

All three repos carry the same 18,922-byte `chat_template.jinja`, SHA-256 `845f1ee4...4e73d1b`, with `enable_thinking` and tools. It is not byte-identical to Google's July 15 canonical large-model template (`ae53464b...4c6d4`). The material observed delta is that Unsloth renders pre-serialized string tool arguments non-fatally, while the canonical template raises and asks the caller to deserialize them to a mapping.

That difference is plausibly relevant to the 12B Heavy tool regression below, but the benchmark does not prove causation.

## Fast matrix — RTX 5090 32 GB

Fast quality used thinking disabled, 512 visible tokens, three attempts per check, and a 100% pass requirement. Both NVFP4 checkpoints passed smoke, JSON, context retrieval, and 20/20 preflight tool calls before capacity testing.

| Candidate | Functional/context result | Repeated quality | 8K capacity c1 / c2 | 24K capacity c1 | 1,024-token diagnostic | Verdict |
|---|---|---|---:|---:|---:|---|
| Official 12B QAT W4A16 control | Prior 240K protocol pass | Prior Fast result: timeout triage 0/3 | 50 / 74 tok/s | — | **112.10 tok/s** | Direct speed control; not the routed Fast model |
| Unsloth 12B NVFP4 | 8K retrieval; 20/20 tools | fail: timeout triage 1/3 | 55 / 144 tok/s | 46 tok/s | 103.82 tok/s | No quality or decode-rate win |
| Unsloth 26B-A4B NVFP4 | 24K retrieval; 20/20 tools | fail: timeout triage 1/3 | **121 / 233 tok/s** | **96 tok/s** | **218.09 tok/s** | Fastest Gemma variation; promotion blocked |

The Fast disabled-thinking quality artifacts are [12B](2026-07-16-gemma4-nvfp4-evidence/fast-12b-nvfp4-32k-quality-disabled-r3.json) and [26B-A4B](2026-07-16-gemma4-nvfp4-evidence/fast-26b-a4b-nvfp4-32k-quality-disabled-r3.json). The earlier thinking-enabled diagnostics are retained separately and are not used for the Fast promotion comparison.

## Heavy matrix — RTX PRO 6000 96 GB

Heavy quality used thinking enabled, 512 visible tokens plus 4,096 reasoning-headroom tokens, three attempts per check, and 32K/128K/240K context targets. Functional preflight used thinking disabled and required smoke, JSON, 240K retrieval, and 20/20 tools.

| Candidate | Functional gate | Repeated quality | 32K capacity c1 / c2 | Quality TTFT 32K / 128K / 240K | 1,024-token diagnostic | Verdict |
|---|---|---|---:|---:|---:|---|
| Official 12B QAT W4A16 | Prior full pass | **pass** | 20 / 68 tok/s | 6.96 / 44.61 / 97.33 s (prior comparable run) | **109.03 tok/s** | **Current Heavy tier** |
| Unsloth 12B NVFP4 | pass; 240K needle 55.0 s; 20/20 tools | fail: repeated tool 1/3; two ZIP values included literal quote characters | 21 / 76 tok/s | 3.23 / 32.70 / 81.47 s | 98.86 tok/s | Faster context prefill, slower long generation, tool regression |
| Unsloth 26B-A4B NVFP4 | pass; 240K needle 32.2 s; 20/20 tools | fail: timeout triage 1/3 | **45 / 122 tok/s** | **1.83 / 18.93 / 48.27 s** | **191.46 tok/s** | Fastest Heavy variation; quality blocked |
| Unsloth 31B NVFP4 | pass; 240K needle 155.0 s; 20/20 tools | **pass** | 7 / 30 tok/s | 9.39 / 92.92 / 223.32 s | 51.49 tok/s | Quality pass, operationally too slow |

The 26B-A4B 128K and 240K single-request capacity probes recorded 11.74 s and 30.00 s TTFT. The 31B first 256K start took about 250 seconds through weight load, compile, FlashInfer autotune, and graph capture; it then exposed a 1,262,607-token KV cache, enough for 4.82 concurrent 256K requests according to vLLM. Runtime details are in [runtime-observations.json](2026-07-16-gemma4-nvfp4-evidence/runtime-observations.json).

Repeated Heavy quality evidence: [12B NVFP4](2026-07-16-gemma4-nvfp4-evidence/heavy-12b-nvfp4-256k-quality-r3.json), [26B-A4B NVFP4](2026-07-16-gemma4-nvfp4-evidence/heavy-26b-a4b-nvfp4-256k-quality-r3.json), and [31B NVFP4](2026-07-16-gemma4-nvfp4-evidence/heavy-31b-nvfp4-256k-quality-r3.json).

## Direct 12B QAT versus NVFP4 result

The matched diagnostic asked for a long deterministic token sequence with thinking disabled and a 1,024-token cap. Every QAT and NVFP4 attempt reached exactly 1,024 completion tokens, making the timing comparison equal-length on each GPU.

| GPU | Official QAT W4A16 | Unsloth NVFP4 | NVFP4 delta |
|---|---:|---:|---:|
| RTX 5090 32 GB | 112.10 tok/s | 103.82 tok/s | **-7.4%** |
| RTX PRO 6000 96 GB | 109.03 tok/s | 98.86 tok/s | **-9.3%** |

The diagnostic suite itself is marked failed because the model continued the requested repeated token until `finish_reason=length` and never emitted the final marker. That is an instruction-following failure and is retained honestly. It does not invalidate the equal-length latency calculation, which uses the API's recorded 1,024 completion tokens and end-to-end latency for each of three attempts. Raw artifacts are [Fast QAT](2026-07-16-gemma4-nvfp4-evidence/fast-12b-w4a16-32k-longgen-r3.json), [Fast NVFP4](2026-07-16-gemma4-nvfp4-evidence/fast-12b-nvfp4-32k-longgen-r3.json), [Heavy QAT](2026-07-16-gemma4-nvfp4-evidence/heavy-12b-w4a16-256k-longgen-r3.json), and [Heavy NVFP4](2026-07-16-gemma4-nvfp4-evidence/heavy-12b-nvfp4-256k-longgen-r3.json).

## Caveats

- The existing WSL2 recipe sets `VLLM_USE_V2_MODEL_RUNNER=0`; vLLM logged the V1 engine. It still selected the native FlashInfer-CUTLASS NVFP4 GEMM kernel requested by the model card. A native-Linux/V2-runner retest could produce a different speed result.
- Mixed capacity throughput is a short-generation workload and is cache/order sensitive; it is not a decode-only rate. The equal-length 1,024-token diagnostic is the primary local check of the 1.5x claim.
- FP8 KV cache may trade some accuracy for capacity. Correctness gates ran on the exact FP8-KV serve shapes reported here.
- The strict timeout-triage validator rejects some operationally plausible answers whose wording misses its deterministic markers. The recorded gate is applied as written; results were not subjectively regraded after the run.
- A Windows cp1252 decode error can obscure bounded vLLM banner logs unless `PYTHONIOENCODING=utf-8` is set. It did not affect model execution or saved benchmark artifacts.

## Restoration and reproduction surface

All swaps used `anvil-serving serves` with a dry-run before apply. Final restored state:

- Fast: `gemma4-e4b-it`, 32K, HTTP 200.
- Heavy: `gemma4-12b-it-w4a16-ct`, 256K, HTTP 200.
- Router: running on its configured Tailscale bind `100.87.34.66:8000`; unauthenticated probe returned the expected HTTP 401.
- Both Gemma 4 lab serves: stopped.

Representative command shape:

```powershell
$env:GEMMA4_HEAVY_MODEL='unsloth/gemma-4-26B-A4B-it-NVFP4'
$env:GEMMA4_HEAVY_REVISION='20df0542b1a86ce19f495ac2eca2c7c12bce82f9'
$env:GEMMA4_HEAVY_TOKENIZER=$env:GEMMA4_HEAVY_MODEL
$env:GEMMA4_HEAVY_TOKENIZER_REVISION=$env:GEMMA4_HEAVY_REVISION
$env:GEMMA4_HEAVY_MAX_MODEL_LEN='262144'
anvil-serving serves up gemma4-heavy-lab --manifest examples/fakoli-dark/serves.toml --recreate --no-router --confirm
anvil-serving eval preflight --base-url http://127.0.0.1:39038/v1 --model gemma4-heavy-lab --checks smoke,json,needle,tools --needle-ctx 240000 --thinking-mode disabled --confirm
anvil-serving eval benchmark quality --base-url http://127.0.0.1:39038/v1 --model gemma4-heavy-lab --suite chat,context,tool,session,intelligence --context-targets 32768,128000,240000 --thinking-mode enabled --eval-repetitions 3 --confirm
```

Every raw artifact is retained in the [evidence directory](2026-07-16-gemma4-nvfp4-evidence/).
