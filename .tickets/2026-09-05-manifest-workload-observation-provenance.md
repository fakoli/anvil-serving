# Bind manifest workload projection to bounded source observations

Status: source producer locally integrated; projection and batch acceptance pending.

serves.py::status_summary reports configured model and container names plus
Docker state and health, but no source observation or lifecycle timestamps and
no exact served-identity observation. Its docker_state/_docker_ps_lines helpers
also materialize unbounded subprocess output without a deadline. This legacy
status object cannot be relabeled as a bounded fresh workload snapshot or
healthy-identity evidence.

Close an observation producer before the T011 projection: bound manifest input,
preserve per-file configuration observation time, inspect only declared runtime
owners through managed product code with bounded output/deadline, and carry
actual observation/lifecycle timestamps. Establish how duplicate manifest
mirrors, container-name reuse, Compose ownership and unsupported native runtimes
remain distinct. Missing authoritative identity stays observed-running at most;
configuration, health or collection time never fabricates identity/freshness.

Reuse the canonical workload schema, managed selection and existing recipe
producer conventions where their contracts actually match. Keep native IDs,
paths, commands, network identity, mounts and raw errors out of public records.
Do not call the legacy broad status path or turn a failure into an empty idle
source. Record partial sources and keep valid peers. No live operation was run
to identify this code-level gap.

The observation-only reader will consume minimal declared identity and exact
Compose ownership in a separate manifest_workloads module. It will not invoke
load_manifest: that loader checks referenced router files and deliberately
rejects native runtimes. Configured is not a launch-validation verdict.

Runtime capture uses one bounded inspect of only declared Compose names. The
existing child capture discards all stdout on nonzero exit; a missing named
container would therefore erase valid peer rows. An internal opt-in retains
bounded stdout only for fully completed nonzero children, with unavailable
status and no stderr. Timeout/overflow/read/cleanup failure and default callers
remain unchanged. The producer validates each row independently and never
turns missing/error output into absence. No live Docker was run for diagnosis.

Candidate b1796b3d passed 114 focused tests and Ruff after commit, recorded as
EV20DD09A1. Configuration and runtime remain distinct immutable observations;
Compose ownership uses the parsed service, not the display slot name. The
aggregate file budget includes failed reads and one sentinel, stops later
opens after exhaustion, and retains validated peers. Literal runtime fixtures
cover lifecycle timestamps and future-time partiality; pure import tests keep
the diagnostic capture dependency lazy. These are hermetic source gates, not
proof of live runtime identity, workload projection or deployment.
