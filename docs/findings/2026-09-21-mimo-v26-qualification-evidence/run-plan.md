# MiMo v2.6 Flash qualification campaign

Authorized: research recipes, download, managed temporary load, tests and benchmarks; restore GLM. Promotion is not authorized.

Ranked objectives: correctness of coding/tool use; preserve useful long context and four-request service; latency/throughput; resource stability. Model card results are external priors.

Initial configurations: pinned upstream SGLang v0.5.20 TP2 MXFP4 no speculation, C1 at 327680 context; C4 only after fit and correctness. If a concrete failure identifies an upstream remedy, one versioned successor per failure with at most three load configurations before reassessing remaining evidence. vLLM MiMo release is second runtime lead. No arbitrary backend combinations. Reserve time and storage for exact GLM restoration before publication.

Baseline and candidate: same deterministic preflight smoke/json/tools/streaming-tools/tool-result/needle at 8192 tokens; quality chat/context/tool/session/intelligence with three repetitions; independently checked coding via existing agentic smoke if available. Capacity scouts 8 requests C1/C4 at 8192 tokens; finalists 100 requests at relevant concurrency only after correctness. Unique prompts, per-request canaries, response_words=128, max_tokens=2048, strict output; thinking disabled capacity controls, enabled quality with explicit budgets. Long context measured separately at 327680 with output reserve, and simultaneous capacity separately from configured maximum. Vision gate required before any replacement qualification.

Containment: candidate RAM ceiling 52 GiB plus 1 MiB swap and 12 GiB host admission reserve initially; stop on host pressure. No co-resident model experiments. Download weights and runtime before quiescing GLM. Pin recipe, weights and engine/image; retain failure logs. No alias changes for trial.

Amendment before capacity requests: thinking enabled and max_tokens=10240 for matched capacity because disabled baseline exposes reasoning and fails JSON. Retain reasoning latency and generated-token distinction; this measures service behavior, not a pure no-reasoning decode kernel.

First repository task: SWE-bench Verified psf__requests-2317, dataset revision c104f840cc67f8b6eec6f759ebc8b2693d585d4a. Selected before either model attempted it; real bytes-method bug, official patch tests. Single task is a coding smoke, never a broad coding score. mini-SWE-agent and grader exact pins are retained in swe-assets.json. Client explicitly co-resident, executed sequentially with capacity cells. SWE preflight passed with truthful host declaration; initial undeclared-host refusal retained.

Launch gates: managed build completed, image-identity.json labels confirm SGLang release revision94602c9c2b7cbdb8efd5c52802dac6a1c180089e and linux/amd64. Recipe uses exact immutable local image ID from pinned FROM. Host /dev/shm45GiB total,768MiB used; ipc=host ignores shm-size flag. Per-process memory hard limit remains enforced by cgroup; free RAM is checked again after GLM unload. Router transition-status proved admitting/ready/exact GLM identity using dedicated declared credential config without shared-env access.

Candidate containment tightened before first load: RAM52GiB, RAM+swap52GiB, effective swap zero. Host swap was nearly full, so no new candidate swap is reserved. Exact GLM rollback remains unchanged with its original1MiBswap ceiling.

Before either model runs the comparative v2 cells: reuse the established September19 GLM APC workload target response_words=16. Keep unique prompts and canaries, strict policy, thinking enabled, max_tokens=10240, nominal8192tokens for scout/C1 and32K for representative finalC4 if fit. The original8request128word baseline scout is retained as a separate workload; it must never be pooled with16word cells. Short visible output supports service TTFT/E2E/request rate, not sustained long-answer decode claims. Source: product docs/findings/2026-09-19-glm53-apc-evidence/summary.json.

Pre-v2 scout output cap reduced to2048 and population1 before expansion: original128word run hit10240tokens on every attempt. Comparative16word populations use the same2048cap only if scout finishes correctly. This avoids committing another broad cell to an untested completion contract.
