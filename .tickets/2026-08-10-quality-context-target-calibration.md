# Context target generation can underfill and become non-monotonic

**Status:** Open

**Priority:** P1 before claiming a prompt above 300K

## Problem

The quality benchmark context generator accepts a target token count, but the
API-reported prompt size can be far below that target and is not monotonic near
the model limit. A replay may therefore be labeled as a successful near-limit
request even though it exercised materially fewer prompt tokens.

This is a benchmark-integrity defect. A requested or clamped target is not a
measurement of the request the model actually processed.

## Reproduction evidence

On the same 131,072-token DeepSeek r33 control and high-reasoning context lane:

| Requested target | Harness clamp | API-reported prompt tokens | HTTP result |
|---:|---:|---:|---|
| 117,500 | 117,500 | 87,207 | 200 |
| 118,500 | 118,500 | 118,830 | 200 |
| 119,500 | 119,500 | 119,503 | 200 |
| 120,000 replay | 119,808 | 88,939 | 200 |

The original 120,000-target attempt returned HTTP 400. The replay's HTTP 200
did not reproduce the same actual prompt size and therefore cannot clear that
failure. The model endpoint remained healthy and later passed 119,503 actual
prompt tokens, so the data currently points to target calibration as a
separate defect rather than a persistent model failure.

## Required behavior

1. Context construction must be deterministic for a fixed model tokenizer,
   seed, target, and output headroom.
2. Actual API-reported prompt tokens must be retained and used for every
   capacity gate and published context claim.
3. A benchmark must fail closed when actual prompt tokens fall outside an
   explicit tolerance around the declared target.
4. Calibration across increasing targets must either remain monotonic or use
   a bounded adaptive retry that records every attempted target and actual
   size.
5. Replays intended to classify an earlier HTTP failure must reproduce a
   comparable actual prompt size; matching only the requested target is not
   sufficient.

## Acceptance

- Add hermetic tokenizer/generator tests that reproduce an underfilled target
  and prove fail-closed behavior.
- Add a monotonic target ladder test around the output-headroom clamp.
- Record target, clamp, actual prompt tokens, tolerance, seed, and generator
  revision in the raw artifact.
- Reject a nominal `>300K` pass unless the API reports more than 300,000 prompt
  tokens and the response preserves the declared output headroom.
- Run the focused benchmark tests, Ruff, and the full suite.
