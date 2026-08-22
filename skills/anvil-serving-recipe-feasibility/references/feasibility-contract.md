# Recipe feasibility contract

## Contents

1. Principles
2. Equations
3. Variable records
4. Input schema
5. Classifications
6. Evidence feedback

## 1. Principles

The calculator is a pruning tool. It proves that some candidates cannot meet a
declared requirement, or that they do not fit a declared operating policy. It
does not prove runtime correctness, model quality, stability, or production
fitness.

Use nonnegative intervals. An exact value is the degenerate interval `[x, x]`.
An unknown value has a missing lower or upper bound. Arithmetic propagates
unknown bounds instead of replacing them with zero.

Use five evidence statuses:

- `measured`: captured locally from the exact artifact/runtime/hardware.
- `confirmed`: exact upstream artifact metadata or specification.
- `estimated`: derived from relevant evidence, with stated bounds.
- `assumed`: an explicit campaign policy or planning value.
- `unknown`: not yet bounded.

## 2. Equations

Required resident tokens:

```text
T_req = prompt + output_reserve + media + protocol + other_token_overhead
```

Physical and policy capacity:

```text
V_physical_available = V_physical - sum(physical_reserves)
V_policy_available   = V_physical - sum(all_reserves)
```

Candidate demand at the required operating point:

```text
V_resident = sum(resident_components)
V_sequence = concurrency * sum(per_sequence_components)
V_token    = T_req * kv_token_multiplier * sum(per_token_components)
V_demand   = V_resident + V_sequence + V_token
```

`kv_token_multiplier` is explicit because concurrency, prefix sharing, draft
layout, scheduler policy, and multimodal expansion can change the number of
resident token copies. For a single independent sequence it is normally 1.

When all required bounds exist, the calculator also reports:

```text
T_max = floor((V_available - V_resident - V_sequence)
              / (kv_token_multiplier * bytes_per_token))
```

The simplified linear `T_max` is invalid when the runtime changes workspace,
graph capture, recurrent state, offload, or KV layout with context. Represent
those effects as intervals or separate candidates.

Quality and speed:

```text
relative_quality_loss = 1 - candidate_quality / reference_quality
warm_e2e_gain         = 1 - candidate_warm_e2e / no_spec_warm_e2e
tasks_per_hour_ratio  = candidate_tasks_per_hour / reference_tasks_per_hour
```

Keep deterministic retrieval, JSON, tools, modality, identity, and stability as
hard pass rates. Do not average them into the quality score.

## 3. Variable records

Every numeric leaf used by the calculator is an object:

```json
{
  "value": 258192,
  "unit": "tokens",
  "status": "assumed",
  "source": "campaign requirement",
  "observed_at": "2026-08-21",
  "notes": "250K prompt plus 8192 output tokens"
}
```

Replace `value` with `min` and/or `max` for an interval. Leave a bound absent
when it is genuinely unknown. `source` is required for every non-unknown
record; an unknown record must explain the missing measurement in `notes`.
An `estimated` record must provide explicit `min` and `max`; the calculator
rejects an estimated point value because it cannot establish an optimistic or
pessimistic bound.

Common variables to track even when unknown:

- physical VRAM and RAM;
- driver/desktop headroom, co-resident allocations, allocator uncertainty;
- target/draft/projector resident allocation and checkpoint bytes;
- runtime fixed buffers, graphs, workspace, recurrent state, multimodal buffers;
- target/draft/other bytes per token and residency multiplier;
- context limit and measured stable context;
- system-RAM offload, mmap working set, pinned host memory, WSL2 limit;
- prompt/decode throughput, TTFT, queue time, tool time, power;
- MTP acceptance and verifier/draft work;
- deterministic pass rates, quality score, task completion, retries, tasks/hour.

The current calculator evaluates VRAM, context, deterministic correctness,
relative quality, warm E2E gain, and tasks/hour. Retain additional variables in
the input even when they are not yet evaluated; they remain visible in the
ledger for later model revisions.

## 4. Input schema

Top-level shape:

```json
{
  "schema": "anvil-serving.recipe-feasibility-input/v1",
  "campaign": "name",
  "requirements": {
    "tokens": {"prompt": {}, "output_reserve": {}},
    "concurrency": {},
    "physical_vram_bytes": {},
    "vram_reserves": {
      "co_resident": {"kind": "physical", "variable": {}},
      "headroom": {"kind": "policy", "variable": {}}
    },
    "thresholds": {
      "min_deterministic_pass_rate": {},
      "max_relative_quality_loss": {},
      "min_warm_e2e_gain": {},
      "min_tasks_per_hour_ratio": {}
    }
  },
  "candidates": [
    {
      "id": "candidate-id",
      "runtime_context_limit_tokens": {},
      "measured_max_stable_context_tokens": {},
      "resident_components": {"target_weights": {}, "runtime_fixed": {}},
      "per_sequence_components": {},
      "per_token_components": {"target_kv": {}, "draft_kv": {}},
      "kv_token_multiplier": {},
      "metrics": {},
      "hard_failures": []
    }
  ]
}
```

Token components are any named variables under `requirements.tokens`; the
calculator sums them. Reserve `kind` is `physical` or `policy`. Physical
reserves affect both physical and policy availability; policy reserves affect
only the policy envelope.

Known metric names are:

- `deterministic_pass_rate`
- `quality_score`
- `reference_quality_score`
- `warm_e2e_seconds`
- `no_spec_warm_e2e_seconds`
- `successful_tasks_per_hour`
- `reference_tasks_per_hour`

Missing metrics remain unresolved. `hard_failures` contains measured failures
such as OOM, malformed tools, parser corruption, or unsupported vision.
Behavioral metrics with `estimated`, `assumed`, or `unknown` status may produce
a projected comparison for planning, but cannot pass or fail a qualification
gate. Only `measured` or `confirmed` behavioral evidence can support
`requirements-disqualified` or `math-qualified`.

## 5. Classifications

Apply precedence in this order:

1. `empirically-disqualified`: hard failure or measured stable context below
   the requirement.
2. `proven-infeasible`: measured/confirmed optimistic demand exceeds optimistic physical capacity,
   or the confirmed runtime context limit is too small.
3. `modeled-infeasible`: a physical or context failure depends on estimated or
   assumed bounds.
4. `policy-infeasible`: optimistic demand fits physically but exceeds the
   declared safe policy capacity.
5. `requirements-disqualified`: measured correctness, quality, or speed misses
   a declared threshold.
6. `unresolved`: context or memory bounds overlap, or a load-bearing bound is
   absent.
7. `benchmark-survivor`: context and policy-memory bounds pass, while one or
   more behavioral axes still need evidence.
8. `math-qualified`: every encoded resource bound and behavioral threshold
   passes. Production promotion still requires the qualification workflow and
   a human gate.

Only rules 1 and 2 support an unconditional technical rejection. Rule 3 is a
conditional rejection under the encoded planning bounds. Rule 4 is a rejection
under the campaign's explicit operating policy. Label both qualifications in
the result instead of presenting either as a hardware fact.

## 6. Evidence feedback

Preserve the input and result together. After a managed serve attempt:

1. Add exact artifact/runtime/hardware identity outside the numeric variables.
2. Replace or narrow runtime, KV, workspace, and stable-context intervals from
   startup logs and measurements.
3. Add the observed date and evidence path to each updated variable.
4. Re-run the same calculator and compare classifications.
5. Never overwrite the earlier input/result artifact; create a new dated
   revision so the change in knowledge is auditable.

Use the LLM qualification skill for real serving evidence and the benchmark
documentation skill when the result changes a published shortlist or finding.
