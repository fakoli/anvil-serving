# Trial friction

1. Initial offline tests refused writable workspace/interpreter setup before tests ran. New isolated worktree and existing dev venv passed15/15.
2. Draft download with uncached SGLang0.5.22 failed during cache-inspection image preparation timeout, before downloader began. Current primary stayed running. Use cached0.5.19 image for public artifact download and separately prepare exact0.5.22 base through managed image build. No engine rejection inferred.

3. Startup confirms compact draft mode but allocates a 7.84GiB physical draft KV pool. The planning assumption of only per-request window-sized physical allocation was incorrect; retain actual allocator telemetry before any long-context capacity claim.
4. Copied runner offline fixture referenced two historical failure artifacts not copied initially; one fixture-file lookup failed (9 other tests passed). Added immutable fixtures under telemetry/fixtures and adjusted only their lookup. No live scored benchmark dispatched.

5. Initial native stability used installed historical response-wait oracle, whose header-to-first-delta interval was65 microseconds. Correct runtime/retrieval but coverage incomplete retained. Reuse already independently reviewed clean b15823d stability source with contender-stream-active/v1, new source-binding receipt and new outputs. No recipe change or retry-to-pass. Durable invariant: bind stability CLI to accepted oracle revision, not PATH installer.
6. Short summary scouts cannot establish controlled long-decode speed. Separate frozen finalist plan adds exactly one32K C2 strict128word/canary six-request cell per arm, independently reviewed; retain strict misses without retry.

7. Generic evidence inspector does not accept multimodal-benchmark-evidence/v1; retained refusals and native multimodal corpus hashes/population/deterministic assertions in images-native-validation.json instead. No model failure inferred.
8. First no-spec8K C1 lacked pre-cell streaming counter series because its initial preflight was nonstreaming. Native3/3 passed; telemetry counter coveragefailed. Exclude speed; retain evidence; independently reviewed suffix resume runs only the other five frozen cells. Original full-arm-only runner refused a remaining-row queue before traffic; receipt-bound private benchmark wrapper reuses unchanged per-cell runner.
