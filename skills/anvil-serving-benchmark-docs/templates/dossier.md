# MODEL OR CAPABILITY

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** CURRENT, FORMER, HISTORICAL, OR UNQUALIFIED ROLE.
    - **Selected or best-qualified configuration:** MODEL, RUNTIME,
      QUANTIZATION, TOPOLOGY, CONTEXT, AND CONCURRENCY.
    - **Measured hardware:** GPU OR CPU CLASS AND INTERCONNECT; distinguish
      measured devices from protected, co-resident, or topology-only devices.
    - **Evidence:** EVIDENCE LABELS plus the most decision-relevant measured
      result and its conditions.
    - **Decision:** DECISION LABEL and the exact promotion boundary.
    - **Important limitation:** MOST DECISION-RELEVANT FAILURE, GAP, OR
      NON-COMPARABILITY BOUNDARY.
    - **Review dates:** evidence included through YYYY-MM-DD; dossier reviewed
      YYYY-MM-DD.

If a field is absent from retained evidence, say
**Not recorded in retained evidence**. Do not infer or run a live benchmark
solely to fill the snapshot.

[Open the retained public configuration](../configurations.md#ANCHOR) or jump to the
[decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

If the managed recipe is operator-private or contains private topology, use a
sanitized public reconstruction page for this link. Never publish a private
recipe path, endpoint, host identity, or secret-bearing command.

### Review narrative

Preserve the detailed reasoning behind the current status. For multiple
decision-relevant reviews, use one descriptive `#### YYYY-MM-DD — OUTCOME`
subsection per review beneath this heading. Keep the full narrative visible and
end each subsection with an explicit **Outcome:** sentence. A compact dossier
may keep one short narrative here without adding empty dated subsections.

## Immutable identity

Group identity by measured configuration family when more than one checkpoint
or runtime was tested.

### CONFIGURATION FAMILY

- **Model:** `REPOSITORY@REVISION` and served name.
- **Runtime:** engine revision and immutable image digest.
- **Artifacts:** draft, adapter, tokenizer, template, or projector revisions.
- **License:** upstream license and any use restriction relevant to reuse.

Record **Not retained** or **Unknown** for a required identity that the evidence
does not contain. Missing identity limits qualification; it never becomes a
best-effort revision guess.

## Tested hardware and topology

- **Measured:** exact hardware class, count, topology, and interconnect.
- **Protected or co-resident:** hardware present but not measured, when any.
- **Execution mode:** split, exclusive, same-host, remote worker, or offline.
- **Comparability boundary:** why another lane or hardware class is not a
  direct comparison.

## Engine, quantization, KV, context, and concurrency recipe

Link the tracked recipe or reconstruction path before summarizing it. Use one
subsection per materially different lane; do not blend controls, promoted
profiles, and rejected experiments into one paragraph.

### CONFIGURATION OR CONTROL

- **Engine and image:** ENGINE, REVISION, AND DIGEST.
- **Weights and KV:** QUANTIZATION AND KV/STATE FORMAT.
- **Topology:** TP, DCP, DEVICE COUNT, AND INTERCONNECT ASSUMPTION.
- **Contract:** CONTEXT, OUTPUT RESERVE, CONCURRENCY, MODALITY, AND REASONING.
- **Runtime controls:** BATCHING, CACHE, SPECULATION, MEMORY, AND TRANSPORT.
- **Recipe:** [tracked public recipe or sanitized reconstruction](RECIPE-OR-CONFIGURATION-LINK).

## Evidence by measurement class

Use one subsection per configuration or evidence class. Preserve detailed
narrative below the four labels; these labels are navigation aids, not a
replacement for methods, metrics, or failure analysis.

### CONFIGURATION OR MEASUREMENT CLASS

- **Status:** evidence label and completion state.
- **Measured:** exact metrics with context, concurrency, sample count, and
  statistic.
- **Limits:** failure, missing comparison, untested boundary, or
  non-comparability statement.
- **Evidence:** [dated finding](FINDING-LINK) and retained raw artifacts.

Detailed narrative explaining the controls, interpretation, and why the result
does or does not change the decision.

## Decision and promotion state

!!! warning "Promotion remains human-gated"

    A configuration, load, health check, benchmark, qualification, or
    documentation update does not authorize a serve, route, or client-catalog
    change.

### Retained or selected

- **CONFIGURATION:** decision label, appropriate use, and current blocking
  gate or rollback role.

### Rejected, superseded, or incomplete

- **CONFIGURATION:** exact rejection/supersession reason and what evidence
  would be required to reconsider it.

## Failures and gotchas

Group failures by cause. Keep material warnings visible, and give every bullet
a bold lead label so readers can scan without losing the narrative.

### Evidence and interpretation limits

- **LIMIT:** detailed impact and evidence boundary.

### Runtime, topology, or integration limits

- **LIMIT:** detailed failure, workaround, and remaining uncertainty.

Add configuration-specific subsections when they make a long list easier to
navigate. Omit empty groups rather than filling them with invented text.

## Dated run history

- [YYYY-MM-DD — descriptive result](RELATIVE-FINDING-LINK)

Keep every decision-relevant finding reachable. When the review narrative
already links each finding and the archive is long, a visible or collapsible
complete evidence index is acceptable; never hide the only path to raw
evidence.
