# ADR-0008 — Heavy tier enables NEXTN speculative decoding (self-speculation, no draft model)

- **Status:** Accepted
- **Date:** 2026-07-02
- **Relates to:** the fakoli-dark heavy tier (`examples/fakoli-dark/docker-compose.yml`, service
  `sglang`, model `qwen35-awq` = Qwen3.5-35B-A3B AWQ), ADR-0002 (serves are compose-defined)

## Context

The heavy tier serves a hybrid architecture — Qwen3.5-35B-A3B is MoE (256 experts, 8 active) with
an attention pattern that alternates 3 linear-attention (Gated-DeltaNet-style) layers per 1
full-attention layer (10 full-attention / 30 linear-attention layers total, confirmed via the
model's `config.json`). This was previously undocumented: prior investigation of hybrid-model
serving problems focused on the parked `Qwen3.6-27B-NVFP4` trial; the production tier turned out to
share the same architecture family.

SGLang ships `--speculative-algorithm NEXTN`, which self-speculates from the model's own built-in
multi-token-prediction (MTP) head — no separate draft model or additional VRAM budget for one is
needed (confirmed live: the MTP head loaded as an extra ~2.3 GB, `type=Qwen3_5ForCausalLMMTP`).
Community/upstream research (a PyTorch blog post on hybrid-model speculative decoding in SGLang)
showed this mechanism is architecturally sound for GDN-style hybrid models — SGLang allocates an
independent Mamba cache slot per draft token and promotes the accepted one, avoiding the state-
rollback problem that breaks naive speculative decoding on recurrent architectures. That research
also surfaced one specific, concrete risk for our exact hardware: SGLang issue #19796 — an
SM120-specific NaN-in-logits crash when Eagle-style speculative decoding hits a radix-cache prefix
match. Our production config runs 131072-context agent traffic specifically to make prefix caching
valuable, so this risk was not a footnote to wave past.

## Considered options

1. **Do not adopt speculative decoding.** Zero risk, zero gain. Rejected once step 1 showed a real,
   repeatable decode-throughput gain with no observed downside on this checkpoint.
2. **Adopt spec-decode, disable radix cache (prefix caching) to sidestep #19796 entirely.** Avoids
   the known risk but forfeits prefix caching for 131072-context agent traffic — likely a worse
   trade than the one it avoids, given how much agent turns benefit from not re-prefilling a shared
   system-prompt/tool-schema prefix on every request.
3. **Adopt spec-decode with radix cache left on, after directly testing whether #19796
   reproduces on our exact stack (chosen).** Requires live verification before rollout, not just
   trusting the upstream report — this repo's rule is never to self-verify a correctness claim
   without an independent check, and "the bug report is for a different config than ours" is
   exactly the kind of assumption that needs testing, not trusting.

## Decision

Enable `--speculative-algorithm NEXTN --speculative-num-steps 3 --speculative-eagle-topk 1
--speculative-num-draft-tokens 4` on the heavy tier's SGLang command, **with radix cache left at
its default (enabled)** — i.e. do not add `--disable-radix-cache`.

This was validated with a live A/B on fakoli-dark before merging, not just adopted on the strength
of upstream reports:

- **Step 1 (throughput/correctness on the actual AWQ checkpoint, radix cache off to isolate the
  spec-decode variable):** +42.8% output tok/s at concurrency=1 (187.81→268.15), +29.7% at
  concurrency=4 (486.16→630.64), stable ~82% draft-token acceptance rate, zero crashes/NaN/garbage
  output across all runs.
- **Step 2 (does #19796 reproduce on our stack, radix cache back on):** two escalating tests —
  single-turn shared-prefix at 78.6% cache-hit rate, then concurrent (concurrency=4) 3-turn
  conversations at 96.2% cache-hit rate (the closest analog to real agent traffic) — both clean,
  zero errors, confirmed via the server's own `#cached-token` log lines, not just the client-side
  report.

The key numbers above are the complete record of what was measured; the raw benchmark logs from
this validation session are not committed to this repo (per the existing convention — dated
trial/bake-off findings live in the private companion repo `fakoli/anvil-serving-notes`, not
inline in the product repo).

## Consequences

- **Gain:** genuine decode-throughput improvement (30-43% depending on concurrency) at zero
  additional steady-state VRAM cost (self-speculation reuses the target model's own weights).
- **Known, accepted tradeoff — TTFT regresses under concurrency.** Mean TTFT was 12.4% *better*
  at concurrency=1 but 37.0% *worse* at concurrency=4 (146.66→200.89 ms) in the step-1 trial. Not
  yet explained (spec-decode targets decode speed, not prefill/first-token latency); flagged for
  monitoring post-rollout rather than blocking on it, since net end-to-end latency still improved
  in every trial run (E2E latency dropped 20-30% despite the TTFT regression, because decode
  dominates total latency for multi-hundred-token completions).
- **Cold-start cost, first time only.** The first container to run with these flags anywhere pays
  a one-time ~9.5-minute CUDA graph JIT tax for the new "target verify" code path (vs. the usual
  ~80-114s warm restart). This amortizes: once cached in the `sglang-cache` volume, subsequent
  restarts with the same flags were observed to return to normal restart times (~110-120s) in this
  session's own testing.
- **#19796 does not reproduce on this stack, but this is not a universal guarantee.** Tested
  configuration: single-GPU (`tp_size=1`), this SGLang build (`lmsysorg/sglang:latest` as of
  2026-07-02), Qwen3.5-35B-A3B AWQ specifically. The upstream issue may still apply under different
  conditions (multi-GPU tensor parallelism, other quantizations, other model families) not tested
  here — re-verify before assuming this finding transfers to a different serving configuration.
- **No wire-level or contract change.** `--served-model-name qwen35-awq-local` is unchanged; the
  router, `configs/example-docker.toml`, and any harness pointed at the router (including OpenClaw
  on Fakoli Mini) require no changes — this is purely a backend performance tuning decision.
- `examples/fakoli-dark/docker-compose.heavy.yml` (the superseded single-tier reference file) is
  **not** kept flag-for-flag in sync with this change — its header now says so explicitly, so a
  future reader does not assume it reflects current production flags.
