# Agents-A1 FP8 versus Qwen3.5 122B at 262K

## Goal

Run a direct, publication-grade head-to-head between official Agents-A1 FP8
and the current Qwen3.5 122B NVFP4 Primary checkpoint on one RTX PRO 6000.
Both profiles must serve a 262,144-token window, use thinking-disabled
requests, run the exact same hashed image/video/mixed-media corpus, and retain
matched context and latency evidence. Do not promote or change production
routing.

## Required evidence

- Exact worktree, branch, model revisions, runtime images/digests, engine
  revisions, GPU identity, cache inventory, and pre-campaign serve/router
  state.
- Direct startup proof for each 262,144-token profile, including model,
  activation, KV allocation, and failure detail.
- A 240K text/retrieval gate with at least 16K output/reasoning headroom.
- The unchanged `multimodal-corpus/v1` manifest at concurrency one with
  identical frame controls and thinking disabled.
- Matched 8K concurrency-one capacity telemetry and retained TTFT, effective
  prefill, generation, decode, inter-token, end-to-end, and usage evidence.
- A case-by-case comparison and an explicit distinction between quality,
  context, memory, throughput, and locally demonstrated video support.
- Exact restoration proof for the pre-campaign router and serve state.

## Compatibility policy

Backwards compatibility is not a campaign goal. Durable fixes needed to run or
measure the comparison are allowed when they preserve direct routing, bounded
evidence, sanitized errors, and the human promotion gate. Every fix must be
recorded in this ticket.

## Progress

- Fast-forwarded `main` to merge commit
  `06f54e2f5fce8e1784f598de963242edba3fe20e`.
- Created isolated worktree
  `C:\Users\operator\ai-code\anvil-serving-wt-agents-a1-qwen-head-to-head` on
  `codex/agents-a1-qwen-head-to-head`.
- Confirmed the live model serves were already absent before the campaign.
  The production router remained running and healthy on `127.0.0.1:8000`.
- Confirmed the RTX PRO 6000 is GPU index 1 with UUID
  `GPU-d0f446cf-1771-414c-e116-a39138798a8c`.
- Served Agents-A1 official FP8 at 262,144 tokens. Startup reported 35.31 GiB
  model memory, 51.93 GiB KV, 5,277,426 KV tokens, and 20.13 full-window
  concurrency. Smoke, JSON, 240K retrieval, and 20/20 tools passed.
- Served the current Qwen3.5 122B NVFP4 checkpoint at the same context. Startup
  reported 73.22 GiB model memory, 13.84 GiB KV, 571,950 KV tokens, and 2.18
  full-window concurrency. The same text functional gate passed.
- Ran matched 8K and 240K c1 capacity lanes with TTFT, effective prefill,
  generation, decode, inter-token, E2E, usage, and aggregate throughput
  retained in `capacity-v3` artifacts.
- Ran the unchanged 30-attempt corpus. Agents-A1 repeated its 28/30 result.
  Qwen passed 12/12 images but its current NGC image failed all video decode
  before model inference because FFmpeg/OpenCV lacked codec ID 27 (H.264).
- Fixed `eval benchmark multimodal` evidence identity so runtimes that expose
  a full container build ref but only an abbreviated engine revision can
  record mutually exclusive `engine_build_ref` instead of mislabeling it as an
  engine source commit. Added focused regression coverage.
- Preserved the first Qwen startup failure: the NGC vLLM launcher attempted to
  parse a GPU UUID as an integer when the recipe loader overwrote
  `CUDA_VISIBLE_DEVICES=1`. The corrected launch omitted the redundant GPU
  override while Docker remained pinned to the exact GPU UUID.
- Preserved the managed-log discovery gap: recipe-loaded candidate containers
  are not discoverable through `serves logs`, so the narrowest read-only
  `docker logs` fallback captured startup evidence.
- Closed that gap for future campaigns. Recipe loading now labels exact model
  and revision ownership, and `models recipes status`, `models recipes logs`,
  and `models recipes unload` provide fail-closed candidate lifecycle without
  raw Docker. The load dry-run and readiness-failure recovery text now point
  only to those Anvil commands.
- Added an AGENTS.md rule requiring recorded recipes plus Anvil recipe/serve
  lifecycle verbs. Raw Docker is now explicitly limited to the narrowest
  read-only diagnosis of a broken product surface, which must be ticketed and
  fixed immediately.
- Removed both campaign containers only after exact name, image-ID, and state
  checks. Verified the production router was unchanged and healthy, all
  pre-campaign managed serves remained absent, and the RTX PRO 6000 returned
  to 510 MiB idle use.

## Outcome

Agents-A1 official FP8 wins the bounded 262K head-to-head on memory, KV
capacity, long-context latency, decode speed, and locally usable video. Qwen
retains the current Primary role because this campaign did not repeat its full
protocol-v3 quality suite against Agents-A1 at 262K. No promotion or route
change was performed.

## Verification

- Focused recipe, model CLI, and multimodal tests: 157 passed.
- Full repository suite: 3,356 passed and 8 skipped.
- Ruff, strict MkDocs, tracked Markdown link validation, and the full
  553-file CLI inventory check passed.
- Final Anvil-managed status confirmed every candidate serve absent and the
  production router running.
