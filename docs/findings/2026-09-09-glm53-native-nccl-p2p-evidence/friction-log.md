# Campaign friction

- The initial 128-word baseline scout failed exact word count (254 codewords and one extra). It is excluded from performance claims. Before comparative cells, both arms were set to 32 words with the same 512-token cap and strict gate.
- The baseline 380k target capacity cell returned 33 codewords rather than 32. Canary and finish reason passed; no GPU failure was observed. Exclude its timing from performance comparisons and retain the artifact. Matched exact needle/tool assertions cover high-context correctness.
- A quality preview rejected preflight/v2 as formal control-evidence/v1 before sending requests. The accepted quality plan truthfully records requested_unverified thinking control.
- Candidate 380k capacity reproduced the baseline 33-versus-32 word-count failure with identical prompt/output token counts; excluded from performance claims.
- Candidate marker quality halted qualification: one of two PowerShell-plan responses lacked the literal word "absolute", while containing Resolve-Path, GetFullPath and workspace containment checks. Independent review requested before any decision; the raw failure remains unchanged.
- The rebuild snapshot audit caught its stale manifest checksum after the selected recipe changed. Updating only that selected checksum made the 327-file audit and 52 focused tests pass.
