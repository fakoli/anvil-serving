# Consolidated workload review repairs

Status: reproduced; repair and verification in progress.

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
  credential isolation and polling cleanup. Add independently failing regressions
  and record their command evidence. No package or live deployment is included.
