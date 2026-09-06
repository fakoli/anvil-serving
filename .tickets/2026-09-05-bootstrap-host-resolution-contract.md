# Close host-owned bootstrap resolution before implementation

Status: implemented source candidate; consolidated acceptance pending.

The resource-owner resolver cannot safely infer a native bootstrap runtime from
model ownership. The original PRD also left cross-OS path parsing and public
plan projection implicit. Close these before the bounded implementation:
explicit native runtime, controller-first transport, mandatory authenticated
node binding, safe metadata-only output and unchanged legacy snapshot digests.

The fleet PRD now includes exact fields, refusal codes, pure path rules and
the tests/test_targets.py resolver gate. No installation or live operation is
authorized by the pure resolver; final acceptance remains batch-level.

Implementation 65a5243f passed the 416-test topology/target/defaults gate and
Ruff after commit, submitted as EV9AAC70EB and integrated locally. It does not
install a controller or establish live node identity.
