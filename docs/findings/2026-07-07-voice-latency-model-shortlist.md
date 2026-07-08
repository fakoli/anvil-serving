# Voice LLM candidate shortlist for OpenClaw Talk latency (2026-07-07)

> **STATUS: SHORTLIST ONLY for `voice-latency-model-ab:T002`.** This note chooses
> benchmark candidates for the Anvil Voice / OpenClaw Talk LLM leg. It does not
> download models, stop serves, delete caches, change router routing, or promote a
> model.

## Goal

Reduce perceived OpenClaw Talk latency without losing the behaviors that now
work: session memory, transcript delivery to the chat session, and tool calls
through the Anvil router.

The baseline finding shows warmed CLI voice turns around:

| Metric | Baseline |
|---|---:|
| Median TTFA | `1014.89 ms` |
| Median turn latency | `1230.51 ms` |
| Runs 2-3 average TTFA | `739.97 ms` |
| Runs 2-3 average LLM stage | `297.15 ms` |

The CLI benchmark and live Talk traces do not stress the same path. The model
A/B should keep both gates: CLI stage timing first, then live Talk validation
for memory, tool calls, transcript/session writes, and duplicate-message
behavior.

## Selection Rules

- Keep all model execution local unless a human explicitly opts into cloud.
- Prefer candidates that can run behind the same OpenAI-compatible vLLM surface
  and preserve function/tool calling.
- Do not displace the current production fast serve without a human gate.
- Treat new downloads, cache changes, new images, or port changes as
  human-gated operations.
- A candidate can win only after Anvil preflight, benchmark, and live Talk
  validation pass.

## Recommended Benchmark Matrix

| Candidate | Role | Family | Engine | Precision / quantization | Target GPU | Memory risk | Tool-call considerations | Expected latency upside | Gate |
|---|---|---|---|---|---|---|---|---|---|
| `nvidia/Qwen3.6-27B-NVFP4` served as `qwen36-27b` | Baseline | Qwen3.6 hybrid attention / GDN | vLLM nightly | ModelOpt NVFP4 plus FP8 KV | RTX 5090 32 GB (`fast-local`, `:30003`) | Medium; current config caps context at 32K and `max-num-seqs=4` to fit | Uses Qwen reasoning/tool parsers; current route proof shows `tool_support=true` and working OpenClaw tool calls | None; this is the reference | Already live; no action |
| `nvidia/Qwen3-32B-NVFP4` | First operational alternative | Qwen3 dense | vLLM nightly | ModelOpt NVFP4 / `modelopt_fp4`, FP8 KV | Prefer RTX PRO 6000 96 GB experiment port; 5090 only if a dry-run memory plan is accepted | Low on 96 GB, medium on 32 GB with long context | Use Qwen3 reasoning parser and `qwen3_coder` tool-call parser; should be closest to current OpenClaw tool behavior | Best practical chance to reduce first-token/first-sentence latency by avoiding the Qwen3.6 hybrid-attention path while staying dense NVFP4 | Human-gated before download, port bind, or fast-tier swap |
| `google/gemma-4-12B-it` | Gemma dense candidate | Gemma 4 dense unified | vLLM Gemma 4 support image/nightly | BF16 for clean signal; INT4 only if BF16 memory or latency is unacceptable | RTX PRO 6000 96 GB for first serve; RTX 5090 only with quantized or reduced-context plan | Medium: BF16 is comfortable on 96 GB but not a drop-in 32 GB fast-tier replacement | Gemma 4 uses a custom tool protocol/parser. Tool-call fidelity must be validated before any voice promotion | Strong expected TTFA upside from smaller dense model and MTP support; quality/tool reliability unknown until local preflight | Human-gated for model download, image choice, and parser/template validation |
| `google/gemma-4-E4B-it` | Ultra-fast Gemma floor | Gemma 4 effective 4B dense | vLLM Gemma 4 support image/nightly | BF16 or lower precision | RTX 5090 32 GB experiment port | Low memory risk, high quality risk | Same Gemma 4 tool parser/template risk; likely weaker at tool planning than 12B/27B-class models | Highest latency upside; useful to learn the lower bound for speech responsiveness | Human-gated; do not promote without live tool-call and memory validation |

## Rejected Or Parked For This A/B

| Candidate | Reason |
|---|---|
| `google/gemma-3-27b-it` / `RedHatAI/gemma-3-27b-it-quantized.w8a8` | Gemma 3 has function-calling support and a vLLM-ready INT8 quantized checkpoint, but Gemma 4 is now the better Gemma-family scout for agentic/local latency. Keep Gemma 3 as fallback only if Gemma 4 serving is unstable. |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | Strong current Qwen option, but it is MoE rather than dense. The repo's Blackwell notes still prefer dense NVFP4 for sm_120 experiments and avoid MoE-NVFP4 as a voice-latency first move. |
| `openai/gpt-oss-120b` | Already useful as the heavy tier, but the size and reasoning defaults make it a poor first candidate for low-latency spoken turns. It remains a fallback/quality tier, not the Talk fast-path target. |
| Metered cloud speech or LLM models | Out of scope for this cost-conscious A/B. Cloud remains opt-in only. |

## Source Notes

- Google documents Gemma 4 dense sizes, function-calling support, system-role
  support, MTP, and memory requirements in the Gemma 4 model overview:
  <https://ai.google.dev/gemma/docs/core>.
- The Gemma 4 12B model card describes the 12B unified instruction model,
  256K-family context claim, dense/MoE family split, native function calling, and
  system-prompt support:
  <https://huggingface.co/google/gemma-4-12B-it>.
- The vLLM Gemma 4 recipe documents the current serving surface, Gemma 4 tool
  parser, reasoning parser, and the need for a Gemma 4-capable image/nightly for
  `google/gemma-4-12B-it`:
  <https://recipes.vllm.ai/Google/gemma-4-12B-it>.
- The NVIDIA Qwen3.6-27B-NVFP4 model card documents the current baseline's
  ModelOpt NVFP4 quantization, vLLM support, Blackwell support, and agentic/tool
  use-case positioning:
  <https://huggingface.co/nvidia/Qwen3.6-27B-NVFP4>.
- Local repo evidence for the current deployment is in
  `examples/fakoli-dark/docker-compose.yml`,
  `examples/fakoli-dark/anvil-router.live.toml`, and
  `docs/findings/2026-07-07-voice-latency-baseline.md`.

## Recommended Execution Order

1. Keep `qwen36-27b` as the control.
2. Benchmark `nvidia/Qwen3-32B-NVFP4` first if it is already cached or can be
   served on an experiment port without touching `fast-local`.
3. Benchmark `google/gemma-4-12B-it` next as the main Gemma dense scout.
4. Use `google/gemma-4-E4B-it` only to quantify the fastest plausible Gemma Talk
   experience and to decide whether smaller Gemma variants are too weak for
   tool-using conversation.
5. Require live OpenClaw Talk validation before any router/profile promotion.
