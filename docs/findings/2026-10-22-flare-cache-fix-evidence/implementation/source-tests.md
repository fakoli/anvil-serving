# FLARE-FIX-001 source checks — 2026-10-22

Flare source changes are uncommitted in `OPERATOR_PATH_d81b073e3d22/flare-local-inference`. No live GPU, lifecycle, promotion, commit, push, or upstream action was run by this implementation task.

Changed for the row-share implementation: `helios/src/offload/slab.rs`, `helios/src/offload/mod.rs` (only sizing option and startup summary; pre-existing telemetry edits preserved), `helios/src/runtime_package/model.rs` (test literal), `flare/src/cli.rs`, `ignition/cli/config.py`, `tests/cli/test_config.py`, `tests/cli/test_serve.py`, `docs/prefix-cache-offload.md`. Other dirty source files were already owned by telemetry work.

API: `flare run --prefix-cache-row-percent PERCENT`; `ignition run` reads `[prefix_cache] row_percent = N` and forwards the native flag. Integers 1..99; absent means 25. The same widened count calculation is used by registered and split-by-rank pools; minimum 128 pages/2 rows, exact byte budget, and u32/address limits are checked. Native Orbit rank forwarding now carries prefix-cache flags.

Focused checks, all from repo root with `PROTOC=OPERATOR_PATH_b14c926a7f3b/protoc` and `LD_LIBRARY_PATH=OPERATOR_PATH_904a718e9796/x86_64-linux-gnu` for Rust:

- `cargo test --manifest-path helios/Cargo.toml --features runtime-package offload::slab::tests --lib --locked -q` — 15 passed, 0 failed.
- `cargo test --manifest-path flare/Cargo.toml cli::tests --locked -q` — 14 passed, 0 failed.
- `OPERATOR_PATH_373ded3e3909/python -m pytest tests/cli/test_config.py tests/cli/test_serve.py -q -k prefix_cache --disable-warnings` — 2 passed, 0 failed, 111 deselected. The launch test uses an existing cached package fixture to exercise argument forwarding without preparation/FlashInfer.
- `git diff --check` — clean.

`cargo fmt --all --check` was not a usable gate: it reported broad pre-existing formatting differences across unrelated files. No formatting sweep was applied. No end-to-end CUDA transfer or actual two-rank package test is claimed here.

## Two-rank transfer coverage extension

`helios/src/offload/transfer.rs` now runs the existing ignored synthetic two-device byte roundtrip sequentially at explicit 25% and 75% row shares with byte verification enabled, checking equal registered/split counts, the expected row/page tradeoff, exact per-rank page and row bytes, positive audited outbound/inbound copy counts and exact audited bytes for both ranks, zero audit failures, and release after transfer drain. It resolves CPU slab pointers through `Stream::mapped_host_address`. The 25% default-off version was previously run by the parent and passed on two GPUs (`../two-gpu-byte-roundtrip.log`); the updated audited 25%+75% version has **not** been run on GPUs.

CPU compile/discovery check:

An earlier compile/discovery of the default-off 25%+75% version passed. The new audit-enabled fixture requires fresh compile/discovery; the parent owns its two-GPU execution. Use the same PROTOC and LD_LIBRARY_PATH settings above.

When the owner has an isolated, drained two-device window, the opt-in test command is:

`cargo test --manifest-path helios/Cargo.toml --features runtime-package offload::transfer::tests::split_pools_cost_each_device_its_part_and_round_trip_units --lib --locked -- --ignored --exact --test-threads=1`

This fixture uses synthetic k/v planes and row payloads. It proves transport bytes and two-device mapping only. It does not load the retained GLM TP2 package or prove a model Snapshot/Restore state oracle. That stronger test would need explicit normal publisher-profile authorization, both selected devices, actual manifests, a completed Snapshot fence, bounded per-plane/row copies, and Restore followed by an independently checked state/output comparison. The existing package test helper accepts only the fixed `flare-tests` publisher and loads device 0; adapting it without a real GPU check is beyond this bounded extension.

## Opt-in actual-transfer byte verifier

Uncommitted diagnostic patch touches `helios/src/offload/transfer.rs`, `helios/src/offload/mod.rs`, `helios/src/runtime_package/model.rs` (test literal), `flare/src/cli.rs`, `ignition/cli/config.py`, `tests/cli/test_config.py`, `tests/cli/test_serve.py`, and `docs/prefix-cache-offload.md`. Pre-existing telemetry and slab edits remain. No GPU or lifecycle operation was performed by this task.

Native `--verify-host-cache-transfers` and Ignition `[prefix_cache] verify_transfers = true` default off. The flag reaches both native rank options and Python launch; the offload preparation carries it to per-rank workers. Each worker allocates one 16 MiB pinned readback and one 16 MiB ordinary expected buffer when enabled. A TP2 single-tier store therefore adds exactly 64 MiB host scratch outside retention; mixed memory+disk is rejected when verification is requested because it has two transfer sets. Each original batch's sources are snapshotted, the unchanged production `copy_batch` runs, and completed destinations are compared exactly. A verification failure follows the existing drain and combined-rank error path. Enabled completed jobs emit bounded rank/direction/page-row-presence/checked-bytes/checked-copies/failures lines; no payload or address is logged. A successful pilot must have positive outbound and inbound checked bytes for rank 0 and rank 1, including jobs with pages and row, and zero failures. Disabled startup explicitly reports verification off. This diagnostic changes transfer timing and does not prove numerical equivalence to uncached model computation.

Focused checks with the same PROTOC/LD_LIBRARY_PATH settings above:

- `cargo test --manifest-path helios/Cargo.toml --features runtime-package offload::transfer::verification_tests --lib --locked -q` — 1 passed (intentional corrupted-byte mismatch), 0 failed.
- `cargo test --manifest-path helios/Cargo.toml --features runtime-package offload::tests::memory_headroom_accounts_for_the_actual_host_staging_geometry --lib --locked -q` — 1 passed, 0 failed (includes 64 MiB TP2 bound and mixed-tier headroom rejection).
- `cargo test --manifest-path helios/Cargo.toml --features runtime-package offload::tests::transfer_verification_rejects_composite_before_cuda_setup --lib --locked -q` — 1 passed, 0 failed.
- `cargo test --manifest-path flare/Cargo.toml cli::tests --locked -q` — 14 passed, 0 failed, including default-off and rank forwarding.
- `OPERATOR_PATH_373ded3e3909/python -m pytest tests/cli/test_config.py tests/cli/test_serve.py -q -k prefix_cache --disable-warnings` — 2 passed, 0 failed, 111 deselected.
- `git diff --check` — clean.

One broader CPU attempt, `cargo test --manifest-path helios/Cargo.toml --features runtime-package offload:: --lib --locked -q`, compiled after the final verifier edit but exited with 102 passed, 18 failed, 8 ignored. All 18 failures were disk/composite tests that could not create `io_uring` (`Cannot allocate memory`, OS error 12) in the concurrent test run; no verifier, config, or headroom test failed. The broad suite was not retried or treated as acceptance. No actual package transfer has been verified here; the parent owns any bounded GPU pilot.

Independent review identified that the ignored two-device fixture had still used the default-off transfer constructor. It now enables verification and asserts per-rank outbound/inbound audited bytes and copies at both 25% and 75%, with zero failures. `Transfers::verification_stats()` holds one counter set per actual rank reply and is absent when the flag is off; these are diagnostic counters, not `StoreStats` or composite-aggregated metrics. CPU compilation was repeated once after the fixture change:

`cargo test --manifest-path helios/Cargo.toml --features runtime-package --lib --locked --no-run` — success, 10.25 s. Fresh executable `OPERATOR_PATH_c9e0b9bd05e9/flare_helios-db6a0d3e7eef93eb`, SHA-256 `94b83483bde5fd94083a36955ace5ca1c69f8ba188ff152a63d13a11aedfa50f`. Test discovery confirms the ignored two-device test and negative verifier test are in this binary. The parent owns running the ignored test on GPUs; no result is claimed here.

From that exact binary, `offload::transfer::verification_tests::detects_corruption_without_exposing_payload --exact` and `offload::tests::transfer_verification_rejects_composite_before_cuda_setup --exact` each passed (1/1). The host pointer and bidirectional live-copy assertions remain inside the ignored two-device test and have not been exercised here.
