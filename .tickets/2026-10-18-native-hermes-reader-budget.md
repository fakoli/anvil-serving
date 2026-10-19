# Native Hermes catalog readback budget

Status: implementation and native qualification in progress.

Four retained profiles require 24 native config-get processes. Bounded
three-worker execution still serializes eight startup waves across those
profiles. Per-profile concurrency tests do not establish complete fleet
readback latency.

An explicitly enabled native snapshot query reads the same six fields in one
process per profile. The consumer requires the complete versioned result,
rejects duplicate or unexpected fields, required-field absence, native errors
and oversized output, and never substitutes single-key queries after failure.
Legacy readers retain their existing behavior until their approved profile
selects the qualified snapshot-capable launcher.

Qualification must compare the actual native resolver's default, named-profile,
missing-key and redaction behavior, prove process cleanup, and measure every
retained profile together. Then require complete owner readback under its
existing time budget, with every declared target retained. Keep failed packets
immutable and bind the newly qualified reader to a fresh successor.

Operational snapshots always enable native redaction, including when ordinary
interactive CLI settings opt out. Fenced results and journals must omit credential
values while preserving those values in the protected source documents. The
legacy non-fenced apply path can still serialize a changed custom-provider list
into command arguments; that existing privacy debt needs a separate repair.
Snapshot qualification and fenced acceptance do not certify that legacy path.

Deployment identities and raw timing evidence remain in the private operator
repository; machine and account identities are omitted from this public record.
