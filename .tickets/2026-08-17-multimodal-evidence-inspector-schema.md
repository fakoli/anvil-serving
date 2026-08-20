# Multimodal benchmark artifacts are not recognized by `evidence show`

## Observed

On 2026-08-17, `anvil-serving eval benchmark multimodal` produced a valid
`multimodal-benchmark-evidence/v1` artifact with 30/30 passing attempts.
`anvil-serving eval benchmark evidence show ARTIFACT --json` then rejected the
file as unrecognized benchmark evidence.

## Impact

The qualification workflow requires normalized artifact inspection before
publication. Operators must currently inspect multimodal artifacts directly,
which creates an avoidable schema-specific branch in the evidence workflow.

## Acceptance

1. `evidence show` recognizes `multimodal-benchmark-evidence/v1`.
2. The normalized result reports exact model/runtime identity, corpus hash,
   request/pass counts, thinking and finish-reason policy, and modality/case
   latency aggregates.
3. Failed attempts and identity mismatches remain fail-closed.
4. Tests cover valid, failed, malformed, and private-path publication cases.
