# Exclusive mode entry remains running after failed target is restored

## Status

Open - observed during the DeepSeek r16 TP2 qualification campaign.

## Symptom

`serves mode enter ... --preserve-on-failure --confirm` preserved the exited
target and restored the declared split stack, and a separate managed
`serves mode status` reported `mode=split` with no exclusive owner. The original
mode-entry process nevertheless remained running without additional output and
had to be terminated after the restored state was independently confirmed.

## Impact

An unattended campaign can appear hung after recovery has completed. Operators
cannot distinguish a still-running rollback from a completed rollback whose
parent process failed to return, and may wait for the full target startup
timeout or terminate a transaction before state is actually safe.

## Required fix

- Add a regression test in which target startup exits after a long model-load
  phase, preservation succeeds, and the split restore group becomes healthy.
- Ensure the mode-entry command returns nonzero immediately after preservation
  and restoration complete; no target-health or child-process waiter may remain.
- Include the terminal target state, preservation result, restore result, and
  elapsed phase timings in the structured result.
- Prove that a failed restore remains blocking and is never mislabeled complete.

## Evidence

Observed 2026-08-02 with target
`tp2-deepseek-v4-flash-0731-r16-b12x-dspark5-128k`. The target exited at the KV
admission gate, the failed container was retained, and the independent mode
status reported split mode with no exclusive owner before the original process
was terminated.
