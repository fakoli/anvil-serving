# Architecture review + model landscape — 2026-07-02

> Session record of the full architecture review, the fixes it produced (PRs #96–#102),
> the mid-2026 local-model landscape research for the fakoli-dark rig, and the
> recommended next actions. Kept in-repo so it survives the ephemeral review
> environment and is retrievable with a `git pull`. (Dated findings normally live in
> the companion notes repo `fakoli/anvil-serving-notes`; this one is here by request —
> move it there if the product-focus convention should win.)

---

## What shipped (the PR series)

| PR | Landed | What it fixed |
|---|---|---|
| #96 | `78cb278` | **Tool-traffic wire fidelity.** The relay backends rebuilt the upstream body from the flattened `InternalRequest`, silently dropping `tools`/`tool_choice` and the `tool_use`/`tool_result` history — a routed tier could never call a tool. Same-dialect requests now forward raw messages verbatim; cross-dialect (Claude Code → local vLLM) get full translation (`router/dialects/translate.py`). Also: Anthropic streaming `output_tokens` no longer reports `1` for a buffered commit; `localhost` doc stragglers fixed. |
| #97 | `ea370bb` | **`[router].profile_path`** — the live server can load a MEASURED `profile.json` instead of always routing on hand-authored seeds (fail-fast if unloadable). **Real usage passthrough** — upstream `usage` blocks reach the response instead of word-count estimates. **Classifier haystack** — only short (≤150-word) system prompts join the keyword scan, so a harness's standing prompt can't multi-match every request into ambiguity. Auth-aware public-bind warning. |
| #98 | `58977a8` | **Production bug bash.** Multiplexer relay `read1` (real SSE TTFT through the proxy); `DecisionLog` bounded (10k ring buffer — was an unbounded per-request leak); calibrator backpressure; the quadratic unterminated-fence scan made linear (adversarial many-fence input was ~10⁹ comparisons); `RouterConfig.tier()` O(1); circuit-breaker probe expiry (wedge-proofing); loopback-default multiplexer bind; swap-path hardening (dead-child detect, `docker rm` verify, zombie reap, OOM-guard evictee credit); secrets-redaction + intent case fixes; GET Transfer-Encoding framing; POST trailing-slash. |
| #101 | `c0c9286` | **In-flight draining across multiplexer swaps ([ADR-0006](adr/0006-multiplexer-swap-draining.md)).** Relays hold an atomically-acquired lease; swaps wait for the old resident's leases (bounded by `--drain-timeout`, default 30 s) before `docker rm` — no more mid-stream connection resets on a routine swap. New arrivals queue behind an in-progress swap. |
| #102 | `48c8522` | **Residency-aware routing** — `RoutingBackend` tracks the last-served local tier and feeds `policy.route(residency=…)`, activating the AC3 anti-thrash reorder (was implemented+tested but never wired). **True streaming relay** — streaming requests issue `stream: true` upstream and forward the model's own deltas as they arrive (`router/backends/sse.py`); tool calls + usage reassembled identically to the buffered path. |

Suite grew 881 → 977 tests over the series; everything green on Linux/Windows × py3.11–3.13.

## New operator knobs introduced

- `[router].profile_path` — path to a measured `profile.json` (from
  `python -m anvil_serving.router.profile_bootstrap`); absent → built-in seed profile.
- `python -m anvil_serving.multiplexer --drain-timeout N` — seconds a swap waits for
  in-flight requests on the old model (default 30; `0` = old swap-immediately behaviour).
- `[router].verify_local_min = false` — opt out of the minimal local-allow commit window
  to get true streaming TTFT on a local `allow` tier you trust (the safety-vs-latency dial;
  cloud `allow` tiers stream for real out of the box).

## Architecture assessment (what's strong, what's still open)

**Strong:** the front door's HTTP framing discipline (smuggling rejection, keep-alive
drain accounting, constant-time auth); the never-raise posture of classify/resolve/route
with auditable notes; `verify.py`'s "only fail what you can prove" principle; the
fail-closed deny default for unmeasured local tiers on high-risk classes; the
availability-never-bypasses-the-gate invariant; the ADR discipline.

**Still open (in priority order):**

1. **Profile persistence loop** — `profile_path` loads at startup, but `record_grade`
   updates and the async calibration loop still aren't persisted/wired into the live
   server. The moat needs write-back: persist on grade (atomic `os.replace`), start the
   calibrate sampler as a daemon thread from `serve`, wire `apply_fingerprint` to serve
   changes.
2. **Swap debounce/hysteresis** — residency reorder + drain remove most thrash; a queued
   old-model request still triggers a swap back. If decision logs show alternating-model
   ping-pong, add batching (swap only after N queued / T seconds).
3. **Multiplexer registry genericity** — the default `REGISTRY` in `multiplexer.py` still
   ships fakoli-dark personal paths (`C:/Users/sdoum/...`) inside the pip package;
   should move to an example registry file (ADR-0003 spirit).
   *[Status update, later on 2026-07-02: substantially closed by #107 — the registry now
   uses container paths on the `fakoli-models` named volume and no personal host paths
   ship in the package. Residue: the default still presumes that pre-populated volume,
   and `init`/`doctor` still have no multiplexer coverage.]*
4. **`/healthz` drain visibility** — surface in-flight counts + drain waits for operators
   (noted in ADR-0006 consequences).

## Model landscape for fakoli-dark (mid-2026, sm_120: RTX 5090 32 GB + RTX PRO 6000 96 GB)

**MoE-NVFP4 on sm_120 — gotcha #16 still stands, barely.** CUTLASS #3096 documents a
working fix (FlashInfer SM120 patches + CUDA 13.0 `compute_120f` → correct output at
~39 tok/s) but it needs extensive patches to FlashInfer 0.6.5 + vLLM 0.17 that haven't
fully landed mainline, and it's still ~20% behind Marlin W4A16 (46–49 tok/s).
**Keep AWQ/Marlin for MoE, NVFP4 for dense; watch for `compute_120f` in stable vLLM.**
Refs: NVIDIA/cutlass#3096 · vllm-project/vllm#33416 · vllm-project/vllm#31085 ·
flashinfer-ai/flashinfer#2723.

**The headline: Qwen3.6-27B (dense).** Beats the previous open flagship
Qwen3.5-397B-A17B on every major coding benchmark — SWE-bench Verified **77.2** vs 76.2,
SWE-bench Pro 53.5, Terminal-Bench 2.0 59.3, GPQA 87.8 — and NVIDIA ships an official
Model-Optimizer checkpoint: **`nvidia/Qwen3.6-27B-NVFP4`**. Dense + NVFP4 is exactly this
rig's native fast path (gotchas #10/#16/#17). ~13–14 GB FP4 weights → fits the 5090 with
long-context KV headroom. Unified thinking/non-thinking checkpoint (gotcha #6 applies).

**Recommended tier shuffle:**

| Slot | Today | Recommended | Why |
|---|---|---|---|
| fast (5090 32 GB) | gpt-oss-20b (vLLM) | **Qwen3.6-27B-NVFP4** — `--quantization modelopt_fp4 --kv-cache-dtype fp8`, qwen3 reasoning/tool parsers | Dense NVFP4 native path; 77.2% SWE-bench dwarfs the current tier |
| heavy (Pro 6000 96 GB) | Qwen3.5-35B-A3B AWQ (SGLang) | **gpt-oss-120b** (MXFP4, ~60–65 GB, proven single-card: ~134 tok/s @12k ctx) — or consolidate on Qwen3.6-27B at huge batch/context | 122B-A10B NVFP4 wants 2 cards per the rtx6kpro wiki; MoE-NVFP4 still gated on sm_120 |

**Strategic implication:** if a 27B dense model beats the heavy tier on coding, the
fast/heavy split becomes about *context length + concurrency*, not quality. The
`planning → deny` verdict was measured against much weaker locals — **re-run the
shadow-eval against Qwen3.6-27B** before assuming it still holds.

Sources: qwen.ai/blog?id=qwen3.6-27b · the-decoder.com (Qwen3.6-27B coverage) ·
huggingface.co/nvidia/Qwen3.6-27B-NVFP4 · github.com/local-inference-lab/rtx6kpro ·
hardware-corner.net (gpt-oss-120b on RTX Pro 6000) · insiderllm.com VRAM-tier guides.

## Next actions (ordered)

1. `hf download nvidia/Qwen3.6-27B-NVFP4` (to D:, per gotcha #13) → serve on the 5090 →
   `anvil-serving preflight --needle-ctx 60000` (large-prefill path especially, gotcha #10).
2. Re-run the shadow-eval / `eval bootstrap` against it → write `profile.json` → set
   `[router].profile_path` — let the measured profile decide the new tier ladder.
3. Consider gpt-oss-120b MXFP4 as the 96 GB heavy tier; benchmark against the AWQ 35B.
4. Wire profile write-back + live calibration (open item 1 above).
