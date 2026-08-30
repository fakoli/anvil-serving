# Media cancellation examples omit the required backend URL

**Status:** Resolved 2026-08-30

## Problem

The dedicated Anvil Media guide showed preview and confirmed cancellation
commands without `--backend-url`. The parser requires that option for both
paths, so copying either example stopped at argument validation instead of
entering the documented cancellation journey.

An independent GPT-5.5/xhigh adversarial review found the defect on the
`1.0.0` candidate at revision
`7fea8c8bccce04ebb8702302e5c2f806e78d79c9`.

## Acceptance

- Both cancellation examples supply a generic loopback backend URL.
- Preview and confirmed examples retain the same principal and job identity.
- The documented commands pass parser validation up to the expected
  operator-state lookup.

## Resolution

Both examples now include `--backend-url http://127.0.0.1:8188` and retain the
same explicit preview/apply flow.
