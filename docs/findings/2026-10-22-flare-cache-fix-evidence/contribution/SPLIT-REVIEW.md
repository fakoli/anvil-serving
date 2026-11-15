# Sequential review units

These are presentation units derived from retained snapshots. They do not change code, constitute a submission, or establish tests beyond the existing campaign evidence.

1. `flare-fix-001-row-share-admission.patch` applies to the saved telemetry baseline. It adds the configurable row share, default 25%, and its admission, configuration, metrics, docs, and tests.
2. `flare-fix-001-transfer-audit.patch` applies after unit 1. It adds the opt-in transfer-byte verifier, scratch accounting, and related docs/tests.

The exact replay starts at `47581ccac82660953a5b59cb04b2750d64edc97d` plus `../baseline-source/pre-existing.patch` and the unchanged `../baseline-source/scheduler_trace.rs`. Applying both review patches in order reproduced the reviewed v2 snapshot byte-for-byte. The retained v1/v2 candidate patches each include that pre-existing tracked telemetry delta, so they were reconstructed directly from the base commit when deriving this chain.

The split packages remain dependent on the saved telemetry/scheduler-trace baseline. They are not standalone clean-upstream patches. Existing evidence is campaign-level only: source/build checks are in `../implementation-validation.json`, and transfer-audit evidence is in the recorded audit artifacts. No test was rerun and no result is attributed independently to either presentation unit.
