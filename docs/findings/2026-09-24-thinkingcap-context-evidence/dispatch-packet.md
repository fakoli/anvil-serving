# Bounded context-run assignment

Campaign: 2026-09-24-thinkingcap-context. Objective: verify actual near-128K inputs with independent deterministic assertions on the pinned local RTX 5090 candidate.

The benchmark runner owns the private context/agentic job specifications, durable job evidence, and its designated public export subtrees. The root agent owns lifecycle and all other inference. There is one inference lease. The rejected no-MTP branch stopped after its failed quality gate. The current MTP/UVA3 branch passed root function, vision, reasoning, quality 30/30 and strict capacity 100/100 before the second lease handoff to the runner for its own scout, full context and agentic jobs.

Use the existing private evaluation topology, the new campaign job root/database, and CLI 1.2.1 from the operational checkout. Do not mutate a serve, cache, route, alias, deployed topology, old evidence bundle, or root-authored publication. Require native health/model identity and functional/vision gates before the scout. Stop on deterministic failure, identity drift, OOM, CUDA error, restart, or missing evidence.

Return native status, exact observed prompt/output counts and finish reasons, deterministic pass/fail counts, and evidence paths. Do not treat configured context or startup cache capacity as successful request coverage. The evaluated model must never grade itself.

The current MTP/UVA3 scout passed 2/2 at actual 32,658 and 125,838 input tokens. Require all 60 full-matrix requests correct, completed and normally stopped; the native curve's default 80% floor alone is insufficient for this campaign. Only then run the two synthetic agentic cases. Root may perform documentation checks concurrently, so context duration is diagnostic and is not substituted for the isolated short-output capacity population.

Root resumed coordination of the still-running durable full-context job at 06:07 UTC. The worker continues unchanged. The benchmark runner must receive a fresh explicit handoff before any further inference; a completed agent turn does not release or complete the durable benchmark job.

The assignment is closed. Full context passed 60/60, all normal stop; the final synthetic agentic gate passed 2/2. The inference lease returned to root for ending health and identity checks, which passed. The authorized 128K direct candidate is retained and the exact qualified 32K rollback remains available. Independent review accepted bounded retention; no route or client alias was promoted.
