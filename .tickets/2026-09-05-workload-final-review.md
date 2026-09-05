# Consolidated workload review repairs

Status: temporal source repair verified; dashboard repair and acceptance pending.

Independent review of 7ef089b6 found two source-contract gaps:

- A benchmark record with updated time 20 seconds after its source time passed
  canonical decoding. A source collected at receipt+30 seconds could carry a
  record at receipt+60 seconds through node/fleet normalization. Allowance was
  applied separately at nested clocks rather than once against receipt.
- The dashboard accepted controller running/failed/success and an arbitrary
  label, while the Python decoder requires a closed semantic combination and
  a label derived from kind. The existing rendering fixture was noncanonical.

Repair the shared validators and dashboard boundary, preserving fixed errors,
trustworthy sibling sources, exact microsecond boundaries, read-only behavior,
credential isolation and polling cleanup. No package or live deployment is included.

The timestamp review was refined against the actual owner contracts: store and
router observations require created <= updated <= source. Recipe/manifest
lifecycle timestamps may come from a different component clock and intentionally
allow up to 30 seconds beyond observation; their existing test explicitly proves
that boundary. The reviewer confirmed this distinction. Both classes still obey
one exact receipt bound for every descendant timestamp; provenance adds no skew.
This is the owner-specific interpretation of T014, not a removal of managed skew
support. The public task wording will be clarified after active claims finish.

The new independent regression file failed 9 cases against the pre-fix source
(3 positive controls passed). After repair it covers 14 ordering, nested decode,
receipt, valid-peer and managed-skew cases. The combined observability, router
workload, manifest, recipe and store-source gate passed 785 tests. Ruff and diff
checks passed. Two canonical fixture scenarios in test_workloads.py were also
made coherent: an explicitly stale managed observation and microsecond sorting
retain lifecycle times no later than their supplied observation; the existing
future test still reaches the future bound. This necessary fixture file was not
in the initial likely-files estimate; no unrelated tests or assertions changed.
