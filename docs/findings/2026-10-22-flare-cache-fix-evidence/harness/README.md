# FLARE-FIX-001 offline harness packet — 2026-10-22

Prepared offline; no model requests, lifecycle or performance acceptance occurred.
`prospective.json` is the authoritative matrix. Candidate row share remains owned
by the geometry decision. Do not call this a completed campaign freeze: its
explicit blockers must be resolved before measured A/B.

## Work and identity

Three pairs, arm order AB, BA, AB. Every lane starts a fresh incarnation; prime and
its associated replay share that incarnation. Fresh small12-original is indices40..51,
prime forward then two forward replays with exact natural bodies. Separate small12
uses fixed1 bodies for the protected latency contract. Use --iteration 1 then --iteration 2 for
the two measured invocations; IDs include iteration, preserving24 distinct attempts. Original reverse/forward use all52 exact
retained bodies by content hash. Fixed-work uses the same messages but a NEW
output contract: max_tokens1, temperature0, raw token IDs/timestamps requested.
This must never be compared directly with natural-output timing.

Per arm/repetition: original reverse104, original forward104, small36,
fixed-work104, protected small12=36, pageheavy24, cold12, decodeC1=1, decodeC4=4:
**425** requests. Across six arm runs: **2,550**, including1,464 original/small
natural requests. The additional fixed1 small12 protects the small-set latency contract.
Correctness, disjoint warmup, tokenization/capacity probes and restoration are extra.

Pageheavy12 and cold12 bytes are frozen and hashed; exact lengths remain unknown.
Configured32768 context comes from the prior recipe, not package maximum. Pageheavy
uses four fixtures each at8000/16000/29000 filler repetitions; these are NOT token
counts. Resolve actual template/tokenizer counts BEFORE either measured arm.
Conservative12*64=768 retained pages assumes each fixture spans a full32768-token
window; it excludes in-flight transfers and does not establish feasibility.
The runner refuses protected lanes until `tokenization_and_capacity_verified`.
No `/tokenize` surface was found in pinned Flare server source. Use package tokenizer
and the exact native chat template offline, or record measured usage in a separate
pilot; do not infer tokens from characters or a generic tokenizer.

## Source-supported output contract

`helios/src/server/request.rs::output_limit` accepts1.
`server/observation.rs::ObservationOptions` accepts token IDs/timestamps for Chat.
`server/http.rs` emits a raw `ObservedToken` SSE chunk before decoded visible text.
Thus first-generated-token client timing is source-supported with raw ID arrival;
existing natural `cache-replay.py` visible `ttft`/`time_to_first_output` are insufficient.
The runner measures client-monotonic arrival minus request submission. Core
`token_timestamps_ns` and client monotonic timestamps have different origins and
MUST NOT be subtracted. First visible answer and reasoning arrival remain separate.
One-token honoring, exact ID equality and actual speculative work remain pilot gates.
A cap of one does not guarantee no additional internal speculative computation.

## Existing command surface and shortest invocations

Use the already selected trial checkout and its Python CLI; no operational wrapper
was added. Lifecycle remains `models recipes load/status/logs/unload`; continuous
engine/GPU/host/cgroup telemetry and managed containment belong to the parent window.
The existing `eval capacity` and `models recipes scheduler-trace` remain supported.
Do not enable detailed scheduler trace in only one measured arm.

From this harness directory, with the established trial Python import environment:

```sh
OPERATOR_PATH_9ba32d4067f1/python check.py
OPERATOR_PATH_9ba32d4067f1/python replay.py --lane correctness --phase measured --output <new-correctness-directory>
OPERATOR_PATH_9ba32d4067f1/python replay.py --lane fixed-work --phase prime --output <new-phase-directory>
OPERATOR_PATH_9ba32d4067f1/python replay.py --lane fixed-work --phase measured --output <new-phase-directory>
python3 analyze.py <normalized-paired-evidence.json>
```

The plain Python interpreter must resolve trial `anvil_serving`, as in the existing
trial environment. The fixture only performs bounded HTTP and retained-body checks;
it does not provide a new user-facing operational path. Existing decode-fixture.py
is reused unchanged forC1/C4 with `--concurrency 1|4 --phase unprofiled --output ...
--api-key-file <protected-reference>`; IDs and native speculative counters must match.
Do not use the old run-phase.py unchanged: its image identity and artifact paths pin
the closed diagnostic campaign.

## Duration prior and stopping

`duration-priors.json` hashes exact retained results. Original104 reverse measured
279.58s; original104 forward290.64s; small phases24.10s and22.71s. Extrapolating the
second small replay at22.71s gives **639.73s/arm**, **21.32min/pair**,
**63.97min across3pairs** for the244 original/small requests only. These are previous
fixture wall times, not new measurements. Fixed1 and pageheavy are new contracts;
no truthful duration follows from natural outputs. Prior decode is roughly5.1–5.5s
C1 and9.7–10.4s C4 core decode, excluding prefill/transport and fresh startup.
The full425 matrix needs a pilot-derived estimate. Nine fresh lane incarnations
per arm mean54 across3pairs; recipe readiness bound240s alone is a possible216min
upper readiness allowance, not a measured startup estimate. Split into bounded
windows and restore the incumbent between them. No new requests after minute70;
90min wall bound includes the full readiness and rollback reserve.

## Offline analysis and oracle contract

`analyze.py` takes normalized `runs`: lane, repetition1..3, armA/B, prospective
zero-based arm order, acceptance receipts, and every attempted request. Attempts
include case_id, prime/measured phase, ok, body_sha256, prompt_tokens,
output_token_ids, reasoning_tokens, speculative_work{proposed,accepted}, and the
lane metric. Missing/corrupt/unmatched work is incomplete; failures are preserved
in denominators. Median within each repetition is compared; no pooled token tests.
Primary must improve>=5% in EACH pair, protected regression<=3% EACH pair. Every
required receipt must pass, including restore fidelity and incumbent restoration.
Prospective blockers force recommendation false. The analyzer does not manufacture
receipt truth; link original raw responses, telemetry and independent source tests.
Natural lanes retain complete diagnostic counts and byte-identity gates with performance_eligible=false; output matching does not apply to their descriptive timings. Fixed1/protected lanes with unmatched work remain incomplete.

Independent `oracle-fixtures.json` freezes known-answer323, exact JSON and a weather
tool call, with request IDs retained. `oracles.py` evaluates exact expectations
without another model. Existing preflight JSON slicing/key-presence is weaker and
cannot substitute. Numerical restore equality compares recorded uncached token IDs
with restored output; no frozen uncached oracle exists yet. One-token equality is
only a pilot and does not replace recurrent/partial-page/draft numerical source tests.

`check.py` runs positive/negative/corrupted synthetic paired fixtures and fake
in-memory SSE transport. It validates thresholds, failed prime denominators,
missing work, NaN, speculation/output mismatch, over-generation, ID arrival TTFT,
retained response on failure, and exact original corpus hashes/order.

Source limitation: no reasoning_tokens field was found in pinned Flare source.
For fixed1 the parent prospectively accepts exact1 raw ID + usage completion_tokens1
+ matching detailed native speculative counters as sufficient bounded generated work;
missing reasoning breakdown is retained as null, never inferred as zero. Decode1024
still needs counts or exact reasoning-span matching; absence leaves it incomplete.

Readiness bound:9*240=2160s (36min) per arm,54*240=216min over3pairs.
At most one full425-request arm per window until pilot timing supports grouping;
a fullpair's72min candidate readiness bound alone exceeds the70min request cutoff.
An arm leaves at most23.34min after prior base244 work and maximum candidate readiness
for the unmeasured lanes/pilot/drain, BEFORE any larger rollback reserve adjustment.
Split further when the measured pilot cannot meet this bound. Preserve pair ordering
across windows; incumbent restoration occurs between every window.

## Continuous cgroup measurement fixture

`run-phase.py` follows the previous campaign's validation-fixture pattern. It invokes
only managed read-only recipe status and this packet's bounded HTTP child. It reads
registry-a.toml or registry-b.toml, pins the frozen image/container/model revision,
and records5-second UTC/monotonic status samples containing cgroup current/peak/
effective-max/OOM plus host MemAvailable. Identity, restart, registry or containment
failure terminates only its own HTTP child, retaining the failed sample and summary.
The parent supplies an absolute epoch request cutoff. Output directory is new;
HTTP evidence lives under its `http/` subdirectory. No model/service lifecycle exists.

```sh
OPERATOR_PATH_9ba32d4067f1/python run-phase.py --arm a --lane fixed-work --phase prime --output <new-phase-directory> --request-deadline-epoch <cutoff-epoch>
python3 check-phase.py
```

Native first-token latency now derives from aligned engine token_timestamps_ns minus
performance.ingress_ns. Source proves both use observation::monotonic_ns and the same
ORIGIN (`http.rs:121`, `runtime.rs:174-180`, `observation.rs:6-17`). Missing fields,
misaligned token counts or invalid ordering yield null. Client/core clocks are never
subtracted. This native field is additional evidence; primary client TTFT is unchanged.

Cancellation guard: real SIGTERM/SIGINT aborts kill and reap only the owned HTTP child and record ok=false; cancellation during spawn is deferred until child ownership exists. Actual container registry_digest must equal the selected arm registry SHA256, so a same-image wrong arm fails. Offline check-phase.py exercises real signals using a fixture sleep child and fake status, with no model traffic.

Actual receipt schema correction: models recipes status omits digest fields. Before HTTP, the wrapper records models recipes running, selects the exact status container ID, and verifies registry SHA256 plus existing serve_recipes.recipe_digest against its real label fields. Periodic status then holds the same container incarnation. The failed pilot receipt is preserved. check-phase.py now uses actual retained status/running receipts, rather than adding fields to a fake status.

Explicit pageheavy-pilot/cold-pilot aliases reuse exact frozen bodies, bypass only the unresolved tokenization/capacity approval for the purpose of measuring counts, and remain performance_eligible=false. They are outside the acceptance matrix.

SSE compatibility is narrowly documented in sse-model-identity-issue.md: only after exact models alias and verified managed digest proof does replay accept the loaded source revision in SSE model fields. Unproven revisions remain errors. Socket connect/read budgets now track the remaining declared60-second request deadline, removing an unrelated hidden10-second idle cutoff without increasing the request budget.

Restore-fidelity pilot: restore-pilot uses frozen bodies at original indices0,40,51, max_tokens128, temperature0 and otherwise exact fixed1 messages/flags; both phases keep the same order. Parent captures uncached references, applies52-body pressure prime, then requires ALL restored token IDs equal reference and actual host reuse in at least one case. This pilot remains outside the performance matrix. Prospective status/configuration is parent-owned after A/B pilots.

Counter-derived restore pressure control: restore-pressure freezes20 novel one-token cold bodies(indices12..31), same nonmessage controls as cold-bodies.json, outside the performance matrix. Insert after52 fixed1 priming and before128-token restore replay. The plan uses observed2endpoint publications/cold request against32GPU snapshot slots;40publications is a prospective pressure target, and actual GPU eviction/host fetch must still be verified from counters. Parent requires full token equality and actual host fetch in a retained target. No timing-driven fixture selection is implied.
