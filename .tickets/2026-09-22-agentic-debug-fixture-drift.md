# Agentic debug-loop fixture drifts after extra calls

Status: open.

The deterministic debug-loop scenario expects exactly four calls: unit tests,
read `calc.py`, edit `calc.py`, then unit tests. Its result fixture is
selected by global observed call index in
`anvil_serving/benchmarking/suite_runner.py` lines 417-432 and 613-620.
An extra read therefore consumes a later scripted lifecycle result; a read or
edit can receive `TESTS-PASS`, and later calls become unexpected.

The protocol definition and exact expected sequence are in
`anvil_serving/benchmarking/agentic.py` lines 192-215 and strict scorer in
lines 254-282. Add a regression that makes an extra call produce a
tool-appropriate deterministic result and a strict protocol failure without
advancing the fixture lifecycle. Preserve the existing exact-sequence test.

This issue invalidates the two affected Qwen campaign traces as strict
replacement evidence. It does not establish a general model coding defect.

## Ordered planning terms score false negatives

The paired GLM planning response visibly used numbered **Inspect**, **Patch**,
and **Test** headings in that order, but `score_agentic_trace` uses the first
`str.find` result for each ordered term. An incidental “tests” substring in
the Inspect bullet is therefore found before Patch and causes a false negative.

Add a positive regression for the captured heading order with incidental
earlier substrings, and a negative regression where the visible headings are
actually out of order. Preserve exact protocol validation separately. Until
then, the raw 16/18 GLM and Qwen outcomes cannot rank broad planning/coding
quality or determine a replacement.
