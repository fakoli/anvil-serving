# r2-B independent closeout review

Decision: **native diagnostic window and recorded incumbent service restoration accepted; final restoration proof closure remains incomplete at review time.** The current `restoration.json` is false because `final-engine-targets.json` is an empty array, so the assembler cannot bind the restored registration target. This is an evidence gap, not evidence that the restored model was unhealthy. Parent was notified. Preserve the empty receipt and failed proof; do not silently replace or report it as passed.

## Verified native execution

Independently totaled all native phase summaries and attempt arrays: **192 priming +233 measured =425 formal targets, plus27 warmups**, with expected=attempted=completed, matching attempt counts and no reported error/missing. Every wrapper is successful, has child_returncode=0, audit=false and no wrapper error. Nine distinct generations and nine distinct candidate container IDs are retained; all lanes have successful managed unload and closure receipts. Last wrapper completion is epoch1792719807.4238472; the outer runner records exit0 at1792719809.2452638. This is distinct from r1-B's unavailable outer exit.

Fresh runner provenance binds the reviewed corrected runner SHA `592c087ebe704e4ea8c4c6d047d3a69a4c5fad2cd95fe590d2db8068bf822b4a` and links `failed-windows/r2-b-pretraffic-1`. The failed window retains exit1, zero formal targets/warmups, its original source, cleanup and restoration closeout (reported8.9487469minutes). It was not erased or counted as a completed repetition. This review does not rerun its previously reviewed failure investigation.

## Readiness and reporter outputs

Each of the nine actual pretraffic readiness proofs matches its five retained raw query responses and its lane generation. Each evaluation precedes the native warmup plan; values satisfy up1, scrape_count>=2, minimum-up1, age0..30seconds and GPU collection freshness1. Fixed-work has two actual scrapes; the other eight have three. Generation-bound online range manifests are retained for all lanes. These are original enforced guards, unlike retrospective-only readiness verification in the earlier r1 windows.

Read the completed reporter outputs and verified all nine proof-manifest references against their small proof-file hashes. Functional=true and monitoring=true for all nine. Independent-oracles, restore-fidelity and causal-chain remain false throughout. The previously reviewed functional scope remains arm-specific strict-three pilot plus formal native execution/accounting; it does not independently rescore formal outputs or discharge numerical acceptance. Full range-corpus analysis was not repeated in this bounded review; monitoring content validation relies on the independently reviewed assembler.

## Recorded service restoration and remaining evidence gap

`restore.json` reports successful managed exact retained-ID restoration. `final-incumbent.json` confirms running container `container-33`, image `sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29`, restart_count0, no OOM-kill and zero memory OOM events. `final-readmit.json` confirms applied/readmitted and expected primary model ready/admitting.

Direct and routed preflight receipts each have seven passing result groups,27 passing observations and all twenty tool indices0..19. The direct served alias and routed llm.primary are correct. Final monitor observations at1792720069.153 and1792720114.165 are45.012seconds apart: up1, scrape counts2 then3, min1, ages8.900/8.912seconds and fresh GPU collection. Both use incumbent generation `65049c53297cbe131326ba201597d26f9378bfe4b5726d8c2952099cd212e118`. The historical restoration proof retains the same generation and second evaluation epoch with all five raw query responses. The accepted second observation is49.7360833minutes from the frozen window start, within90minutes. These establish the recorded service restoration, not continuous future health.

However, the retained target-discovery file is exactly `[]`. Consequently the approved assembler correctly emits `restoration.ok=false` / `restored target generation mismatch`. Parent-review's21checks do not include this target-binding requirement and must not be presented as equivalent to a passed final restoration proof. A separately retained, correctly bound target receipt and explicit disposition can close this narrow evidence gap if obtainable under the parent's ownership; no new framework or model requests are called for. Until then report the service checks passed and proof closure incomplete.

## Narrative additions and limits

The draft's new192-prime/233-measured/27-warmup split agrees with the observed summaries and totals1152priming+1398measured=2550targets over six planned arms, plus162warmups. Its failed-startup and r1-B limitation paragraph accurately preserves the separate operational failure and unknown runner/monitoring receipts. No performance comparisons were made here.

Numerical acceptance remains failed; diagnostic_only=true, performance_eligible=false and promoted=false remain mandatory. Only this review note was written. No live query, lifecycle, GPU/model traffic, source/code change, build or performance analysis was performed.

## Supplemental registration closure — accepted

Independently validated `formal/r2-b/restoration-registration-refresh.json`, observed at epoch1792720425.0440102. It contains exactly one engine target. Its generation equals the final-incumbent-generation file, closeout, final-monitor-sync registration and accepted monitor/history generation: `65049c53297cbe131326ba201597d26f9378bfe4b5726d8c2952099cd212e118`. The sync record binds the same retained container ID and image; the target model equals the sync model and the metrics path contains that exact generation.

This closes the narrow missing registration-binding evidence as a **supplemental independent disposition**. The supplemental capture is54.9174002minutes after the window start, with35.0825998minutes remaining before the original90-minute deadline. The earlier service/preflight/monitor restoration was verified at49.7360833minutes; complete supplemental evidence closure is at54.9174002minutes. Do not backdate the later registration capture or claim it was present in the earlier empty response.

Original `final-engine-targets.json` remains `[]`, and the original `restoration.json` correctly remains false for that original input. No assembler or original proof was modified. Report both: original machine proof false due missing target receipt; later independently verified matching registration closes the evidence gap within the same window. This does not assert continuous health between observations or replace any failed numerical gate.

### Proposed future root verification correction

**GO for the proposed minimal bounded discovery loop**, not an execution/implementation pass. Preserve the original helper source first. At most six attempts,15seconds between unsuccessful attempts; persist each raw target response and observation epoch before validation, along with errors on failed retrieval/parse. Use the expected generation already bound to the restored container/image, not a generation learned from the discovery response. Accept only a single target carrying that exact generation; keep the existing model/identity checks. Write the canonical final-engine-targets receipt only after this match and never overwrite a preexisting attempt's canonical receipt.

Retain the helper's bounded request timeout and original wall deadline: stop when either budget is exhausted, do not sleep after the sixth attempt, and fail visibly after six unsuccessful attempts. Keep all unsuccessful responses. This is a bounded registration-discovery check, not a serving restart, monitoring resync, model retry or proof-oracle relaxation. No new helper implementation was reviewed or run in this follow-up.

## Implemented discovery correction — focused GO

Compared `formal/root-operation-capture.json` against preserved `review/serial-runner-history/root-operation-capture.before-discovery-fix.52cb265e…json`. Only the discovery block within sources.verify_restoration changed; the other captured operations are unchanged. The extracted `review/restoration-discovery-block.py` exactly equals that source substring and parses successfully. Capture SHA recorded by the offline check is `72031ed5af9a58ee3305c0664bae0bf7ef3f8a38b4961bce608a6f122b640028`; block SHA is `2cc398c578867786baa09f30198e8ca5d6d881ea1a862da5d9e8e6fb58344908`.

**GO for this correction in a fresh root-owned restoration verification.** It retains each UTF-8 discovery response before parsing/validation, records retrieval/parse exceptions, requires exactly one matching generation/model target, and creates the canonical target file exclusively only after a match. Expected generation comes from the exact-container/image-bound sync record. Six failed observations raise visibly; there are at most five15-second sleeps, each guarded against the wall deadline. Retrieval retains its15-second timeout; a matching response that returns after the deadline is rejected before canonical publication. This bounds retries and accepted evidence time, not a hard process-kill deadline. Existing attempt paths and canonical files are refused under the single-owner fresh-output contract.

Read the independent fake-I/O check and its execution receipt `review/check-restoration-discovery-result.json`: all six checks pass, covering empty-then-match, six wrong-generation attempts, malformed raw JSON retention, retrieval-error retention, retry deadline and already-expired deadline. The check executes the exact source block with fake network/time and temporary files. I did not rerun the separately assigned check or execute the operational helper.

No material blocker found in this narrow correction. It changes no proof assembler, readiness threshold, workload, source image, lifecycle selection or failed numerical gate. Original r2-B empty target/false proof and its supplemental registration disposition remain unchanged.
