# Align workload command manifest options with their sealed CLI contract

Date: 2026-09-05
Status: reproduced; assigned to workload-visibility T015

At integration baseline `21e3db82`, focused `router workloads --help` correctly
lists only output/help options, explicit connection arguments and seven workload
filters. The in-memory command manifest nevertheless advertises the inherited
topology, target, transport, SSH-fallback and model-workload options for both
`router workloads` and `fleet workloads`. The CLI intentionally rejects these
options before resolution; the generated declaration must not advertise them.
This mismatch is independent of the already-known stale checked-in manifest.

Reproduction: compare `python -m anvil_serving.cli router workloads --help`
against `manifest_data()` records for the two workload leaves. `_manifest_records`
unconditionally concatenates inherited options; the CLI's existing offline-leaf
help filters resolution options. No request, environment or live mutation is
needed to observe the mismatch.

Closed implementation boundary for T015: preserve the existing two explicit
workload declarations and sealed handler. Restrict only those two manifest
records' inherited options to output/help, preserving their connection/query
options. Do not change unrelated offline commands, schema version or general
dispatch behavior. Regenerate the checked-in manifest through `write_manifest`,
never by hand. Add exact option-set parity tests for declaration, manifest and
focused help, plus canonical wire/query parity through the already-delivered
router/controller/CLI seams. Final reference-inventory regeneration is T016.
