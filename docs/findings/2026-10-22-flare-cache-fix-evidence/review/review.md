# FLARE-FIX-001 independent review — 2026-10-22

Scope: reviewed local config/allocation and common admission instrumentation against Flare HEAD `47581ccac82660953a5b59cb04b2750d64edc97d` and the preserved pre-existing patch. Read-only source review and CPU checks only; no live endpoint, GPU, container or lifecycle action. Source and harness were authored by other agents. This report lives only in private evidence.

## Recommendation

No blocking source defect found in the reviewed allocation/configuration or admission-counter changes. They are suitable to build for a bounded correctness pilot once candidate/image identity, protected capacity and monitoring gates are resolved. This is not runtime restore-fidelity acceptance, a performance recommendation, promotion, upstream submission approval or a claim that every planned test has run.

The performance campaign remains incomplete. Exact protected tokenization/capacity, one-token contract on both arms, reasoning-work evidence on non-fixed1 lanes, native timing normalization, matched speculative work, actual restore fidelity, continuous telemetry and incarnation identity still need evidence. `geometry.json` itself records `candidate_frozen=false`.

## Source findings

- Default 25% is algebraically identical to the previous `bytes / 4 / row_bytes` sizing for valid layouts. Bounds are checked before subtraction. u128 products prevent intermediate overflow; slab counts are explicitly limited to u32, and region/address arithmetic remains checked. Minimum 128 pages and 2 rows is preserved; pages receive the remainder. At the supplied 32 GiB geometry, the 25% and 75% projections are consistent with 52/7314 and 158/2434 row/page counts respectively.
- Both registered and split-by-rank allocation paths use the same counts routine (`helios/src/offload/slab.rs:243,265,351`; callers in `offload/mod.rs:421`). The share applies after the existing prepared-input reservation. Staging pools, transfer eligibility, cost policy, evictions, pins, checkpoint keys, asynchronous ownership and package serialization were not changed by the allocation delta.
- Native CLI validates 1..99 and forwards explicit settings to peer ranks. Python configuration uses exact int types (rejecting booleans) and rejects 0/100. Omitting the setting emits no new flag; native `None` remains 25. Existing package data does not acquire policy fields or require re-export. This is source-level compatibility evidence, not proof of GPU-loaded package operation.
- The five new admission counters use fixed AtomicU64 fields and do not alter control flow. Their HELP texts accurately distinguish raw/cost/capacity attempts (including retries), admitted/joined fetch waiters, and synchronous/async failure events once per fetch/chain. These denominators must not be combined into a request-level hit rate. Existing transfer-completion and reused-token counters remain necessary.
- `git apply --reverse --check baseline-source/pre-existing.patch` passed against the modified tree; the preserved earlier patch remains applicable intact. `git diff --check` passed.

## Independent runnable checks

Run from `OPERATOR_PATH_d81b073e3d22/flare-local-inference`:

```sh
cargo test --manifest-path helios/Cargo.toml --features runtime-package offload::slab::tests -- --nocapture
cargo test --manifest-path helios/Cargo.toml --features runtime-package offload::memory::tests -- --nocapture
cargo test --manifest-path flare/Cargo.toml cli::tests:: -- --nocapture
cargo test --manifest-path helios/Cargo.toml --features server,runtime-package host_admission_ -- --nocapture
```

Results: slab 15 passed; memory 12 passed and 2 explicitly ignored GPU/package tests; CLI 14 passed; admission metrics 3 passed. Compilation emitted existing unused-code warnings. Without `runtime-package`, the standalone Helios test build fails on existing test references to the gated module; the corrected command above passed. No full-suite claim.

Direct Python assertions against `Configuration.load` passed invalid values 0,100,-1,256,true,1.5,string25; accepted/forwarded 1,25,75,99; omitted setting emitted no flags. System Python lacks pytest and the Flare checkout has no `.venv`; the proposed pytest command was not runnable here. The author may retain separate pytest evidence.

```sh
PYTHONPATH=OPERATOR_PATH_cc25b9dd30c4/anvil-serving python3 OPERATOR_PATH_3961e3d16c94/check.py
```

Synthetic harness checks passed after repair. Main anvil-serving checkout is not sufficient for this import because the retained harness imports trial-only `recipe_secrets`; use the exact trial PYTHONPATH above. A transient syntax error during concurrent harness edits was observed, then resolved before the passing check.

## Harness review findings and gates

1. Initially `small12` required 24 unique measured IDs but emitted 12 IDs per replay; fixed with explicit iteration IDs and two invocations. The repaired final harness synthetic checks passed.
2. Initially every lane required matched output token IDs, including retained stochastic original bodies that never requested token IDs. The harness author separated descriptive original lanes from matched performance lanes and retained a separate original small-set lane; the protected small lane now uses the fixed-one-token corpus. This changes the protected workload prospectively and must stay labeled, with no timing comparison to earlier natural-output results.
3. Reasoning-token count is not supplied by the pinned native API. The parent prospectively declared a fixed1-only exception before live measurement: exactly one raw generated ID, completion usage exactly one, and full matched native speculative counters establish bounded work, while the missing reasoning count remains null. The final analyzer implements this narrow exception. Non-fixed1 lanes, notably decode, still require explicit reasoning evidence and remain incomplete without it. Do not fabricate zero or silently generalize this exception.
4. Client TTFT uses the first raw-ID SSE arrival on the client monotonic clock, correctly separate from visible-answer and reasoning arrivals. Native first-token latency must use engine `token_timestamps_ns - performance.ingress_ns` from the same engine origin. Raw events preserve these inputs; a derived field and clock validation are still needed if required by the acceptance packet.
5. Strict/json/tool/restore validators are independent exact checks. One-token equality alone does not establish recurrent, partial-page or draft state fidelity; the plan explicitly requires those fixtures. The final runner now accepts tool_calls termination only for bodies with tools, assembles fragmented function arguments, preserves request/tool IDs, and invokes the exact semantic oracle. Fake SSE tool tests passed. This resolves the earlier tool-runner integration gap.
6. Three matched repetitions, fixed AB/BA/AB order, >=5% primary gain in every pair and <=3% protected regression in every pair are implemented. Failed/missing/duplicate runs prevent acceptance; all failures remain in the report. Boolean acceptance receipts still require external source evidence, not author assertion.

## Explicit coverage limits

No live correctness pilot or performance request was made by this reviewer. GPU/package memory tests were ignored, not passed. Tuned allocation at TP1/2/4 with uneven valid rank geometry, actual mapped physical accounting, real asynchronous cancellation/drain and GPU continuation fidelity remain unverified by this review. Disk/io_uring/composite execution was not run; the live memory-only scope cannot establish general disk compatibility. No new package/schema change was observed, but actual package load is still a pilot gate. No source or harness files were modified by the reviewer.

Final harness review: total prospective denominator is now 425 requests per arm/repetition, 2,550 over three pairs, before pilot/correctness/warmup/restoration. The final speculative-counter completeness issue is resolved: all four families, identical per-family label support, identical snapshot keys and nondecreasing values are now required. Missing-family and inconsistent-label negative checks passed independently. Page-heavy/cold are explicitly fixed1 contracts. The final synthetic harness check passed after these edits; no remaining implementation blocker was identified in the reviewed harness. Prospective/live gates above remain open.

## Subsequent bounded wrapper and broad-test review

Parent-supplied broad offload log inspected: `offload-tests-memlock256m.log` ends with **118 passed, 0 failed, 8 ignored**, serial execution in 1.97 seconds. Earlier parallel/serial runs under default 8 MiB memlock retained io_uring ENOMEM failures (100/18/8 and 112/6/8). The successful run used a test-process-only 256 MiB memlock bound, then dropped to the normal uid. This extends CPU disk/composite validation; it does not convert eight ignored GPU tests into passes or prove live CUDA transfer fidelity. The reviewer inspected the result log and did not independently rerun the elevated test command.

Reviewed `harness/run-phase.py` and `check-phase.py`: fixed source-built image digest, container/model/revision, zero restart count, stable incarnation, immutable registry bytes during the phase, cgroup effective memory limit and zero OOM events are checked with bounded managed status calls. Both registry files inspected: baseline uses omitted/default25%, candidate adds75%; loaded startup geometry remains necessary to bind those files to actual allocation. Host MemAvailable is sampled, not independently thresholded by the wrapper. Status timeout is4seconds and sampling period5seconds; safety intervention therefore has bounded sampling delay, not instantaneous detection. The wrapper controls only its own HTTP subprocess.

Native first-token normalization now uses `token_timestamps_ns[0] - performance.ingress_ns` from Helios `server::observation::monotonic_ns` at HTTP ingress and observed token emission. It checks integer/nonnegative ordered clocks, enqueue ordering and ID/timestamp alignment, returning null for unavailable/invalid evidence. Client TTFT remains separate. Prompt accounting still requires prefill + GPU reused + host reused = usage prompt tokens. The updated synthetic replay and wrapper checks passed.

One wrapper blocker found: KeyboardInterrupt/SIGTERM originally bypassed child cleanup and could record success. A correction and actual cancellation-path check were requested before pilot requests. Resolved: SIGINT/SIGTERM now enter a caught interruption path; cleanup runs unconditionally, ignores repeated signals during bounded child terminate/kill, and records failure. The reviewer independently delivered real SIGINT and SIGTERM while main used fake managed status and owned a real sleeping subprocess. Both returned failure, retained the interruption summary and reaped the child with return code -15. No HTTP/service was contacted. Conditional wrapper approval for the bounded correctness pilot; other live prerequisites remain parent-owned.

Geometry clarification from subsequent parent pilot startup: baseline reported52rows/7314pages, logical record bytes34,356,232,192, physical mapped `pool.bytes()`34,359,738,368 (32GiB). Existing CUDA per-rank mapping granularity accounts for the difference. The sizing proof concerns logical record capacity; physical mapped size must still be observed. This selected baseline fits the32GiB budget; no universal physical no-rounding-overhead claim is made. Parent will correct the misleading `allocated_bytes` label in geometry evidence. Reviewer did not observe the live startup directly.

## Real-schema identity correction and final pilot review

The initial wrapper’s status-digest expectation was wrong: actual managed `recipes status` responses do not expose registry/recipe digests. The first pilot attempt failed before HTTP; retain `pilot-a-correctness/status.jsonl`. The reviewer’s earlier synthetic validation did not detect this schema mismatch and is superseded for digest binding by this real-receipt check.

The repaired wrapper obtains initial managed status, then managed `recipes running` inventory. It selects exactly the same container ID and verifies the registry SHA256 and the existing canonical `recipe_digest(recipe)` against the inventory’s actual digest fields, plus image/start/model/revision identity. It retains `running-proof.json`. Subsequent status samples require the same container/incarnation and unchanged registry bytes, without invented status fields. Independently reran `python3 harness/check-phase.py`: real retained receipts, wrong registry/recipe/ID rejection, cgroup failure checks, and actual SIGTERM/SIGINT fixture-child reaping all passed.

Pilot aliases `pageheavy-pilot`/`cold-pilot` were independently checked to return the exact frozen bodies; ordinary protected lanes remain blocked and summaries remain performance-ineligible. The bypass is technically the combined `tokenization_and_capacity_verified` status, so it is not itself capacity proof: parent must keep the independently checked physical/context ceiling and containment constraints. Conditional GO for the bounded correctness/tokenization pilot only. No reviewer live request or lifecycle action occurred.

## Source-backed SSE identity and request-budget compatibility

Reviewed the retained first SSE event and pinned source flow: `flare/src/main.rs:192` changes `served_names`, `server/models.rs:30` advertises that alias, while `server/http.rs:327` and raw observed-token serialization use `model.config.model_id` (the loaded revision here). The narrow harness exception still checks the exact `/v1/models` alias and accepts only that alias or the exact revision from the wrapper’s persisted verified running proof. It validates served identity, registry bytes/hash and recipe proof linkage; it does not accept arbitrary revision-like strings or change the engine.

The hidden10second urllib timeout was replaced with remaining declared request budget (at most60seconds), refreshed for each socket read. The outer wrapper cutoff remains active. Focused `check.py` passed retained-real-SSE rejection without proof, exact revision acceptance with proof, corrupt proof rejection, and connect/read remaining-budget checks. Conditional GO for the three bounded correctness requests. This approval does not treat prior failed/cancelled receipts as passes and does not establish performance acceptance.

## Sampling discriminator after baseline pilot

See [sampling-discriminator.md](sampling-discriminator.md) and [sampling-discriminator.json](sampling-discriminator.json). Actual package clamps temperature0 to approximately0.95, fixes top_k=-1, and already defaults seed0. Two consecutive full GPU hits differed, so host fetch is not necessary for baseline sampled-output instability. Its root cause remains unresolved. Exact sampled-ID acceptance remains failed; TP2 byte transport verification and explicitly unqualified diagnostic collection are distinct from numerical qualification. No further repeat-output retries recommended.
