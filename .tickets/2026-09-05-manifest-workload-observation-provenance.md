# Bind manifest workload projection to bounded source observations

Status: source contract gap identified before workload-visibility:T011.

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
