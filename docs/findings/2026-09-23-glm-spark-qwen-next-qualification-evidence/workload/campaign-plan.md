# Next model qualification, 2026-09-23

User authorizes research on Chrome X/Reddit and Hugging Face, bounded candidate lifecycle, settings changes, independent qualification and promotion, followed by PR/review/merge. No reboot. Existing Pi watch remains paused.

Priorities: coding intelligence and completed-task reliability; stable long generation and mixed prefill/decode; useful context with output headroom; eight-image support; simultaneous throughput. Retain incumbent if no challenger qualifies. No hidden fallback.

Baseline: GLM EXL3 r10, 327680 context, C4, eight images, no speculation, APC. Recipe snapshot and live state attached. Driver 615.71.09 on both Max-Q GPUs; post-reboot baseline is separate from historical 595-driver benchmarks and Xid31 crashes.

Shortlist: pinned LIL GLM Spark TP2 without MTP or external cache first; investigate supported fixes or historical stable controls if it fails. MiMo Flash NVFP4 TP2 next only if feasibility permits useful context/vision; current TP2 publication demonstrates merely 12288 context. Qwen Flash Next advances to a bounded candidate after exact SM120 dual-MaxQ TP2 evidence was found in LIL issue808 and the pinned benchmark recipe.

Hard gates: exact identity; all deterministic protocol/media/coding checks pass; allowed finish reasons with visible answers; no OOM/restart/GPU faults/parser corruption/unexplained loss. Keep synthetic protocol adherence separate from executed coding correctness. The historical fixed-index fixture defect is corrected in current source; current agentic fixtures still assess synthetic protocol adherence, not general executed coding quality.

Search stages: baseline direct preflight; matched quality and independent executable coding tasks; naturally long outputs crossing 12K and 16K (record actual generated length); fresh and retained-history tools/images; context targets 32K/128K/256K followed by larger supported targets with 65536 output reserve; C1/C2/C4 mixed prefill/decode. Configured and advertised context are never measured capacity. Eight-image contract must be tested before replacement.

Comparison: promote only a fully passing frozen candidate with no coding-oracle regression and a measured benefit: at least 10% completed tasks/hour, or at least 25% larger successful context/simultaneous capacity with <=10% latency cost on the common workload. Report uncertainty and failed configurations; a faster short counting stream is insufficient. Use model-native sampling/thinking for the intended role and identical task/resource budgets, with separate matched-control cells for causal tuning.

Budget: no user time/token limit. Bound individual probes and downloads; allow at most two evidence-supported repairs per distinct failure before reassessing priority. Preserve budget for frozen final gates, restoration/promotion, client convergence and publication. Do not retry unchanged failures for a passing score. Candidate feasibility and 12GiB OS reserve plus explicit RAM/swap containment precede loading.

Offline harness: current product eb8ed5c4bf4acc7e40cbfced8ae817ea088239a7, 57 agentic/suite tests passed; this does not erase known fixed-index agentic limitations. Initial environment failure was missing pytest/worktree permission, resolved in local Python3.12 venv.

## Feasibility disposition
Calculator r0 is unresolved for exact runtime allocations, not a physical rejection. Qwen has exact-checkpoint/runtime dual-MaxQ external startup and mixed-request evidence; GLM Spark has hardware-matched preset but missing no-MTP peak evidence. User-authorized bounded first-load diagnosis will close resource unknowns before scored qualification; do not label either paper-fit or locally qualified. Hard container RAM/swap and host reserve are mandatory. Qwen PLE CPU offload is separate from GPU-only prefix caching and must be included in host-memory evidence.

## Controls and baseline scout results
Direct scout requests use thinking enabled, temperature1.0/top_p.95 for matched diagnostics. The deployed Pi route forces temperature0.6, max reasoning effort and thinking_token_budget49152; direct diagnostics therefore do not reproduce the complete Pi request contract. The final candidate comparison must include intended-route controls, and any model-specific override changes must be explicit. The 32768-token long-output failure is a budgeted direct diagnostic, not evidence of a Pi session crash or proven degeneration. Baseline eight-image corpus4/4, revised protocol oracle5 18/18, needle255795input tokens and long-tool118293input tokens passed after driver615 reboot.

## Updated evidence and next controls
Oracle6 explicitly discloses strict fixture contracts with unchanged scorer. Qwen18/18; incumbent paired rerun pending. Qwen direct needle412589 actual input tokens passed with111699 tokens of model window unconsumed (no65K generation performed in that request); this is retrieval evidence, not full-context coding or simultaneous capacity. Natural64K guide finished23150visible tokens without observed degeneration, but word-count and static integration shortcomings remain; neither long-guide artifact qualifies coding intelligence. Keep independently executed SWE/real coding tasks as distinct gates. No route has changed.

## Frozen coding scout selection
Five unseen Verified tasks selected on2026-09-23T11:25:24Z by fixed hash seed with one task per repository, using only instanceID/repo metadata. Selection manifest `swe-scout-selection-v1.json` fixes IDs before any candidate outcomes. All comparators use scout profile60calls/16384percall; count all failures and report elapsed agent time independently of grading. The prior1case Requests smoke remains separate.
