# Define Ignis concurrent admission and JSON_OBJECT behavior

Status: open.

The retained Ignis C8 preflight passed 6 of 13 checks and returned seven
immediate 503 responses; serial C1 passed 6 of 6. Separately, 24 SWE
engine-full retry notices recovered, and JSON_OBJECT returned 400
`unsupported_value`. These are distinct observations and retries do not close
either contract gap.

Expose a declared admission contract with bounded retry guidance and a
supported JSON_OBJECT capability contract. Do not convert an immediate 503
into silent fallback or infer that recovered SWE retries explain empty patches.

Acceptance: deterministic fixtures distinguish C1 success, C8 admission
refusal, and recovered retry observations; return a stable machine-readable
admission reason; and either accept JSON_OBJECT under its declared contract or
reject it before dispatch with an explicit supported-values error. No test may
count retries as quality or SWE completion.
