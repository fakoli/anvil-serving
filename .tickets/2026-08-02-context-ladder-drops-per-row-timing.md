# Context ladder drops per-row timing evidence

## Status

Fixed and verified locally.

## Observed

The quality context ladder retained `ttft_ms`, `e2e_ms`, output tokens, and raw
usage for each target, but kept first-output latency, effective prefill rate,
generation duration, decode rate, and inter-token latency only in aggregate
percentiles. That made a successful multi-size artifact insufficient for a
per-context benchmark table.

## Impact

Operators could not publish the requested TTFT, prefill, and generation metrics
for each context size from one canonical artifact. Reconstructing values from
aggregate percentiles would be inaccurate, especially for reasoning models
whose first reasoning delta precedes the first visible content token.

## Fix

Reuse the existing publication-safe `result_timing` calculation when recording
each successful context row. Preserve prompt-token identity, output-token
source, first-output latency, generation durations, effective prefill and decode
rates, inter-token latency, and reasoning/content chunk counts.

## Acceptance

- A focused regression test proves the per-context row retains each metric.
- Existing benchmark tests remain green.
- The live DeepSeek context ladder is rerun and the replacement artifact has
  complete per-context timing fields.

## Verification

- `python -m pytest tests/test_benchmark_context_evidence.py tests/test_benchmark.py -q`
  passed 157 tests.
- Ruff passed for the changed runner and focused regression test.
- `quality-r16-b12x-dspark5-context-ladder-low-r2.json` completed all three
  targets and retains first-output, TTFT, generation, effective-prefill, decode,
  inter-token, and reasoning/content chunk fields on each row.
- A second warmed ladder plus 250 ms hardware sampling correlated per-context
  VRAM, power, clocks, and utilization with the three request rows.
