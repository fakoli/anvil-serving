# Close bootstrap value-contract ambiguities before implementation

Status: contract resolved; T002 implementation under corrective review
Priority: P1
Date: 2026-09-05

## Reason

Independent preflight of fleet-node-enrollment:T002 found that the approved
PRD described closed manifests and receipts without enumerating every schema,
phase, outcome, protocol representation or root class. It also bounded the
outer install bundle but not nested wheel expansion or entry count. Letting
the executor choose these independently would create incompatible adapters
and permit archive-resource exhaustion despite a small transfer.

## Correction

The PRD now defines exact canonical manifest/receipt fields and enums, identity
grammars, phase consistency, platform pairing and protocol-date bounds. Nested
wheels have separate 16 MiB expansion, 4096-entry, 1024-byte path and 255-byte
component ceilings. Extraction-time containment remains a separate required
recheck; pure path validation never grants a race-free filesystem capability.
Independent review additionally found that cleanup failure after acceptance or
verified rollback was unrepresentable. The final contract adds cleanup-failed
plus a fixed triggering-error field, preserving accepted/rollback truth and the
primary failure rather than erasing either.

No task is removed, no completed task is reopened, and no live configuration,
credential, runtime or workload changes as a result of this design amendment.

## Acceptance

- Independent contract review and named-PRD reapproval pass.
- Public and canonical PRD mirrors match; 15 requirements, 4 features and
  16 tasks remain, with no unresolved decisions.
- T002 implements the exact types and adversarial boundary tests before any
  receiver, transport or installer consumes them.

Contract correction does not prove the bootstrap feature implemented or deployed.
Both independent review passes and delegated reapproval passed. Canonical and
public sources match, and the 15/4/16 partition remains unchanged with zero
unresolved decisions. T002 implementation and its tests remain the next gate.

## Implementation review

The first implementation passed 67 tests, but independent byte-level probes
reproduced five contract gaps before any installer consumed it:

- A nested archive's original NUL-containing name was hidden by ZipInfo's
  normalized filename and survived both bundle construction and validation.
- Windows-forbidden path characters and superscript-number device names were
  accepted by the supposedly cross-platform lexical boundary.
- A regular file and a descendant path could coexist in the archive, creating
  a file/directory collision that would be discovered only during mutation.
- Malformed compressed data raised an uncaught decompressor exception instead
  of the fixed typed invalid-bundle refusal.
- A schema string subclass could raise raw caller-controlled exception text
  during dictionary deserialization.

Closure requires original-name validation, fixed cross-platform name rules,
casefold-aware prefix collision checks, typed decompression failure, exact
dictionary key/schema types, and regressions that fail before each correction.
The broader canonical digest, receipt phase, nested expansion and entry-count
gates remain required. No extraction, installation or live mutation occurred.
