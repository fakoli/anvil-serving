# Frozen runtime selection

Parent owns `../runtime-pins.json`; the harness never creates it. When absent,
run-phase.py retains the original v1 image and registry-a/b paths for old fixtures.
When present, it must contain a full sha256 image digest, source_patch_sha256 and
absolute registry paths in `registries: {a,b}` and `audit_registries: {a,b}`.

Default runs select registries[arm]. `--audit` selects audit_registries[arm] only.
The exact native `--verify-host-cache-transfers` flag must be present once for audit
and absent for default timing. Entrypoint and serve flags are both checked. No
arbitrary expected-image or registry override is accepted.

The wrapper reads the runtime file once, records its exact UTF8 bytes/SHA256 and
source-patch identity in plan.json, and aborts if it changes during periodic status
sampling. Registry bytes remain frozen independently. Actual managed status image
must equal the selected runtime image; managed running proof still verifies the
selected registry/recipe digests and exact container. Audit output is labeled and
never performance-eligible. Legacy registry files are preserved.

Offline checks cover retained real v1 status, explicit synthetic expected image,
missing/invalid runtime pins and timing/audit flag separation. Temporary test pins
are fixtures, not operational v2 placeholders. No model requests/lifecycle occurred.
