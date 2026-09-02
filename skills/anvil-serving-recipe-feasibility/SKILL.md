---
name: anvil-serving-recipe-feasibility
description: Bound and eliminate local-model serve recipes with reproducible interval math before expensive downloads or benchmarks. Use for VRAM/RAM/context feasibility, KV-cache and speculative-draft budgeting, quant/runtime shortlists, throughput or time-to-success thresholds, intelligence-loss budgets, and campaigns where unknown serving variables must remain explicit and be updated from evidence.
---

# Anvil Serving Recipe Feasibility

Use deterministic math to shrink a recipe campaign before lifecycle or benchmark
work. Never turn an estimate into a qualification claim or a route promotion.

Read `references/feasibility-contract.md` completely before building or changing
a campaign input.

## Workflow

1. State the operational requirement before inspecting candidates: usable
   prompt tokens, output/reasoning reserve, media/protocol overhead,
   concurrency, hardware capacity, required free headroom, quality-loss ceiling,
   deterministic gates, and speed or successful-tasks-per-hour threshold.
2. Record every quantity as an exact value, interval, or unbounded unknown.
   Include unit, status, source, observed date, and notes. Preserve unknowns;
   never insert a convenient zero.
3. Separate physical capacity from the operational policy envelope. Physical
   reserves include real co-resident allocations. Policy reserves include
   declared driver/desktop headroom and an explicit uncertainty band. A policy
   reserve is a campaign default, not a universal hardware fact: the operator or campaign owner
   may explicitly reduce or waive it, including to zero, for
   a model-only device. Record the default reserve, effective reserve, scope,
   rationale, and the workload telemetry that makes the waiver safe. No
   separate reserve-specific approval is required; promotion authority remains
   a distinct gate.
4. Model target weights, draft weights, projector, recurrent state, graphs,
   workspace, per-sequence state, KV bytes per token, and residency multiplier
   separately. Do not infer GPU allocation from checkpoint bytes alone.
5. Run the bundled calculator:

   ```powershell
   python skills/anvil-serving-recipe-feasibility/scripts/recipe_feasibility.py INPUT.json --format markdown
   ```

6. Interpret classifications narrowly:
   - `proven-infeasible`: optimistic demand exceeds physical capacity.
   - `modeled-infeasible`: the physical failure depends on estimated or assumed
     bounds rather than only measured/confirmed bounds.
   - `policy-infeasible`: it could fit physically, but not inside the declared
     safe operating envelope.
   - `empirically-disqualified`: measured capacity or a deterministic hard gate
     already failed.
   - `requirements-disqualified`: measured quality or speed misses a declared
     threshold.
   - `unresolved`: bounds overlap or a load-bearing variable is unbounded.
   - `benchmark-survivor`: resource math passes; correctness, quality, or speed
     still needs evidence.
   - `math-qualified`: every encoded bound and measured threshold passes. This
     still is not production qualification.
7. Create managed serve recipes only for survivors. Use
   `anvil-serving-llm-qualification` for the real load, preflight, capacity,
   correctness, quality, endurance, restoration, and publication workflow.
8. Feed measured startup allocations, KV capacity, parser results, quality,
   latency, acceptance, and tasks/hour back into the same input. Replace an
   estimate only when the new source and date are recorded; retain the earlier
   artifact as chronological evidence.
9. When startup fails, separate transport initialization, runner capability,
   resident model state, compile/autotune workspace, and KV allocation. A
   pre-KV compile OOM can empirically disqualify the exact recipe and replace
   measured resident/workspace unknowns, but it does not prove the requested
   context or the checkpoint itself physically infeasible.

## Integrity rules

- Disqualify mathematically only when optimistic bounds fail. Label a failure
  caused by headroom or uncertainty policy as `policy-infeasible`, not physical
  impossibility.
- A reserve waiver does not waive workload safety, stability, or correctness.
  Require per-device startup and post-representative-workload telemetry plus no
  OOM, CUDA error, crash, restart, or unexplained request loss. Without that
  evidence, leave the candidate `unresolved` or `unverified`; do not infer
  safety from startup free VRAM alone.
- Require estimated quantities to carry explicit lower and upper bounds. A
  convenient point estimate is not an optimistic or pessimistic bound.
- Use only measured or confirmed behavioral evidence for `math-qualified` or
  `requirements-disqualified`. Estimated/assumed metrics may show a projected
  comparison, but their gate remains unresolved.
- Treat paper feasibility as permission to test, never proof that a runtime can
  allocate or serve the window.
- Keep hard deterministic gates independent. A weighted score cannot compensate
  for malformed tools, failed exact retrieval, parser corruption, OOM, or an
  unsupported modality.
- Compare MTP/speculation to a matched no-speculation control. Credit it only
  when end-to-end or time-to-success improves; acceptance and decode rate are
  diagnostic variables, not substitutes.
- Keep the router serving-engine agnostic. Runtime, artifact, quantization, and
  launch details belong in recipe/evidence metadata, not routing decisions.
- Use Anvil Serving managed lifecycle commands after the math stage. Do not
  operate candidates with ad hoc Docker commands or change a live alias.
- Store sanitized public inputs/results with the dated finding. Put real GPU
  UUIDs, topology values, and unsanitized working evidence in the private
  operator repository.

## Bundled resources

- `scripts/recipe_feasibility.py`: stdlib interval calculator and Markdown/JSON
  report generator.
- `references/feasibility-contract.md`: equations, input schema, variable
  catalog, classification semantics, and update rules.
- `references/qwen38-250k-example.json`: sanitized worked example with measured,
  estimated, assumed, and unknown variables.
