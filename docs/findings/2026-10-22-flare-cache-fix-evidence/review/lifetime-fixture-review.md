# Lifetime fixture review

2026-10-22. Decision after focused recheck: **GO to apply the corrected proposal and run the focused CPU test after restoration**. Static review only; no patch application, compilation, tests, GPU, HTTP, or lifecycle operations.

Reviewed `lifetime-test-proposal/lifetime-test.patch`, `input.json`, and `COVERAGE.md` against the current Flare scheduler and Worker test helpers. Current `phase_tests.rs` SHA-256 matches input.json: `8ee1033a4c46fec2e767e376fa7b590ec3d3aff585873a5c9eb0bc48df6a3767`. Reviewed patch SHA-256: `abdeb1ff1abdaa9e332fa24492c25c3a4a90d73921461427756b1b1db2dac4d2`.

## Focused correction accepted

The corrected proposal SHA-256 is `43f14e55bd3724c87d9a04c847ee50d2412ed2df056b5826f52118faa4fde149`, independently recomputed and matching input.json. The source file still matches the original input hash above. Recheck was limited to the reported branch error: success now asserts Finishing after Resume fence 2; only failure executes Snapshot fence 4, following its final Prefill. Both branches then use ordinary Worker publication. This clears the sole reported static blocker. Application and execution remain unperformed by this reviewer; a passing focused test is still required.

Prior patch bytes are unavailable after the author's overwrite. The original SHA-256 above was independently observed during the first review and remains historical review evidence; it does not imply that the old patch artifact was preserved. No prior patch was reconstructed.

## Original blocking correction (resolved)

`lifetime-test.patch:122` unconditionally advances a Snapshot graph after both branches. The successful full-prompt restore branch cannot schedule that graph. Resume has zero consumed tokens (`helios/src/scheduler/policy.rs:676–685`), and the mock completion retains consumed_tokens=70 while generating the single requested token (`phase_tests.rs:311–338`). Since consumed_tokens equals prefix_tokens, the completion transitions directly to Finishing (`helios/src/scheduler.rs:2231–2235`). The next `advance(Snapshot, 3, None)` therefore fails inside `planned` at the missing-step unwrap (`phase_tests.rs:303–306`). This is a fixture error, not evidence of a product lifetime failure.

Minimal correction: move `advance(&mut worker, GraphKind::Snapshot, 4, None)` into the failure branch immediately after the final Prefill. Remove the unconditional advance. In the success branch, optionally assert the survivor is Finishing after Resume, then use the existing common `worker.publish()`. Its last use/completed fence is legitimately 2; no artificial extra fence is needed. The cold fallback branch consumes 70 tokens beyond its zero-token prefix and legitimately needs Snapshot fence 4 before publication.

## Other reviewed invariants

The fixture's APIs and ownership accesses exist. Generation drop reaches Worker cancellation; two initial waiters share one FakeStore ticket, and cancellation removes the first waiter while leaving the survivor Inbound. With six pages and three rows, the asserted post-cancel free counts (four pages, two rows), two additional held tickets, row-capacity failure, and full remaining page allocation are consistent with admission reservation accounting. Other ticket destination IDs exclude the protected ticket; successful completion checks the exact restored page chain and row plus one surviving checkpoint pin. Failure removes the ticket, frees its resources, and readmits the survivor with host lookup disabled.

After the correction, ordinary publication/retirement is the appropriate survivor cleanup. Subsequent three two-page admissions force reclamation of the cached page chain, then cancellation returns all six pages and three rows. This exercises ordinary allocator ownership and pin release; it does not substitute a fabricated shutdown drain. The failure branch's terminal snapshot and the success branch's existing restored checkpoint are both subject to this reclamation check.

COVERAGE.md correctly limits the claim to a CPU Worker/scheduler fixture with controlled completion outcomes and mock fences. It does not prove real HTTP cancellation, physical DMA overlap, executor drain, TP2 transport, numerical correctness, or performance. Those limitations must remain in the eventual result. No additional production hook or broader oracle is requested by this review.

Parent may apply the corrected, hash-identified proposal and perform the already proposed focused CPU test after restoration. A passing result must be recorded separately; this static review is not a compile or execution pass.

## Execution receipt review — accepted CPU fixture coverage

Read-only follow-up accepted the recorded focused execution. `lifetime-test-execution/r2/stdout.log` names the exact reviewed test and reports **1 passed, 0 failed, 0 ignored, 803 filtered out** in 0.07 seconds. stderr identifies the executed `flare_helios-2c1079bd13063c63` binary. Its independently recomputed SHA-256 is `b6236ca684f5233c2576b631d349e02098e18c0ac5339eb29258106ef1e59e20`, matching r2/execution.json. The receipt uses `--features server,runtime-package --lib` with the fully qualified test name and `--exact --nocapture`; compilation/execution exited 0. The reviewer did not rerun or rebuild it.

Current phase_tests.rs SHA-256 is `b21df8e9ad32b725b7f5934820441138483b245eeb35479c176f1f701081aa78`. An in-memory reversal of the exact reviewed patch (`43f14e55bd3724c87d9a04c847ee50d2412ed2df056b5826f52118faa4fde149`) reproduces the original source SHA-256 `8ee1033a4c46fec2e767e376fa7b590ec3d3aff585873a5c9eb0bc48df6a3767`. This proves the applied file delta is exactly the reviewed 144-line insertion, with no removed or altered preexisting lines. No source bytes were changed during this verification.

The test body unconditionally loops over `[false, true]` without an early return; its successful completion therefore covers both controlled failure/readmission and success/restore branches, including the survivor completion and reclamation assertions. The log reports one test, not separate branch results; no physical DMA claim follows from it.

The original `--features server` attempt is retained as a compile failure (exit 101): stderr shows runtime-package imports configured out plus the associated missing gpu_transport symbol. Adding the existing runtime-package feature repaired the command without changing the reviewed test or product code. The original failure remains part of the execution record.

Decision: **accept the focused CPU shared-fetch cancellation lifetime test as executed evidence**. Real HTTP disconnect, cancellation overlapping physical DMA, executor drain, TP2 transfer lifetime, numerical equivalence, and performance remain outside this test. This later test-only insertion and new test binary are separate provenance from the already frozen v2 serving image; they must not be described as part of that image's source/build inputs. No v2 image rebuild or validation is claimed by this receipt review.
