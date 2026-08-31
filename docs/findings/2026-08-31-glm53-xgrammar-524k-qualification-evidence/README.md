# GLM-5.3-Flash 524K xgrammar qualification evidence

These artifacts support the
[dated finding](../2026-08-31-glm53-xgrammar-524k-qualification.md). They are
sanitized, portable evidence from the dual RTX PRO 6000 Max-Q TP=2/DCP=2
qualification. Private router URLs, host identities, GPU UUIDs, credentials,
and real-client traces are intentionally excluded.

## Feasibility

- [`feasibility-input.json`](feasibility-input.json) records sourced and
  explicit-unknown fit inputs.
- [`feasibility-result.md`](feasibility-result.md) records the benchmark-survivor
  decision and rejected assumptions.

## Matched no-speculation control

- [`nospec-preflight-text-tools-250k.json`](nospec-preflight-text-tools-250k.json)
- [`nospec-vision-ocr.json`](nospec-vision-ocr.json)
- [`nospec-c1-ctx4096-r5.json`](nospec-c1-ctx4096-r5.json)
- [`nospec-c1-ctx240000-r5.json`](nospec-c1-ctx240000-r5.json)
- [`nospec-c2-ctx250000-max8192-r2.json`](nospec-c2-ctx250000-max8192-r2.json)

## Corrected DFlash2 K5 candidate

- [`dflash2-preflight-text-tools-250k.json`](dflash2-preflight-text-tools-250k.json)
- [`dflash2-vision-ocr.json`](dflash2-vision-ocr.json)
- [`dflash2-quality-all-high-r3.json`](dflash2-quality-all-high-r3.json)
- [`dflash2-c1-ctx4096-r5.json`](dflash2-c1-ctx4096-r5.json)
- [`dflash2-c1-ctx240000-r5.json`](dflash2-c1-ctx240000-r5.json)
- [`dflash2-c1-ctx240000-r5-repeat2.json`](dflash2-c1-ctx240000-r5-repeat2.json)
- [`dflash2-c2-ctx250000-max8192-r2.json`](dflash2-c2-ctx250000-max8192-r2.json)

The capacity artifacts use `capacity-v4-reasoning`. Decode values include
reasoning completion tokens where the API reports them and are not raw
server-side token timestamps. The C2 artifacts prove bounded concurrent
completion, not a clean output-rate comparison, because response lengths and
reasoning behavior varied.
