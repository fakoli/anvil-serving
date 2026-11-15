# Verify isolated child imports during source validation

Status: open

Running the test wrapper from a clean checkout does not guarantee that a child
started with `sys.executable -I -m anvil_serving...` imports that checkout. A
shared interpreter with another editable installation can split parent and child
source identities. During qualification validation, a separate bootstrap failure
was traced to a 118-byte temporary Unix socket path exceeding the client's
100-byte limit. It failed during client construction, before the reader probe;
the earlier reader-probe and fixture-interpreter hypotheses were disproven.
The focused test passed with a short, owner-only temporary root (91-byte socket
path). The split-source environment is a separate confirmed validation
confound, not the cause of that failure or proof of a product regression.

Current disposition: use a disposable environment installed from the exact
candidate tree and verify ordinary and isolated imports before focused and full
suite validation. Use a short owner-only temporary root for socket tests and
retain the pathname-length diagnosis alongside the failed run. Do not change shared installations or
directory permissions to make a validation copy pass.

Potential follow-up: the existing test wrapper can reject an isolated import
that resolves outside the selected candidate installation. Keep any hardening
in the existing wrapper; do not add another test launcher.

Operator-state isolation is a separate requirement. An unset
`ANVIL_SERVING_HOME` can resolve through the user's configured home to live
operator state. A mocked lifecycle unit test then encountered the real
promotion-role lock during an authorized restore. An owner-only temporary
operator home made all three focused cases pass without touching or bypassing
the live lock. Use that isolated home for the remaining full-suite run. Any
wrapper hardening should cover source, temporary socket paths, and operator
state together; do not change live state to accommodate a test.
