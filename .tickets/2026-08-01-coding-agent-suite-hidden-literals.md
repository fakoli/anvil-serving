# Remove hidden literal requirements from coding-agent diagnostics

**Observed:** 2026-08-01

## Problem

The first DeepSeek V4 Flash 0731 coding-agent diagnostic used deterministic
string checks whose exact literals were not fully stated in the prompts. The
PowerShell task accepted only `Resolve-Path` even though two attempts correctly
used `[System.IO.Path]::GetFullPath()` to resolve absolute paths. The TP2 task
expected `dual-gpu-exclusive`, the word `offline`, and a phrase containing both
`not` and `unified`, but only described those semantics in prose.

This produced false negatives and made the diagnostic partially test guessing
the validator rather than following the stated operator contract.

## Resolution

Preserve the original suite and failed run as immutable evidence. Add a v2
suite whose prompts explicitly require every literal used by its deterministic
checks. Do not weaken the validators after observing an answer, and do not
overwrite the original artifact.

## Acceptance

- Every deterministic literal is stated in the corresponding prompt.
- The original failed artifact and source suite remain available.
- The corrected suite receives a distinct filename and config ID.
- Repeated results are reported separately from the original diagnostic.
