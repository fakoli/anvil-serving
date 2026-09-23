# GLM runtime recipe notebook

## Baseline and question
The two historical incidents share long decode plus fresh prefill: about 175K+32K and 201K+47K tokens, batch budget 2048, TP2/EP2/DCP2, APC enabled, no speculation. Confirmed illegal access/Xid31; fault origin remains unproved. Driver now 615.71.09, so prior crash evidence is not a matched current-driver control.

Starting recipe: baseline-recipe.toml, SHA-256 7c13c5251170faf06d1a20978dd82b789898bb8f56c067442672abced6d24105. Restored model/container identity unchanged; no new route promotion. Current requests are synthetic, not the user's original task content.

## Attempt records
Live trials begin only after the skill and runner are independently checked. Each entry will reference immutable scenario and native result files, exact change, observations, limits and next decision.

## 2026-09-23 baseline correctness preflight

Same running container and recipe; driver 615.71.09. Thinking-disabled request policy passed smoke, 4K nominal needle, and 20/20 tool calls, but JSON failed with extra prose. The retained synthetic response placed reasoning-like analysis in visible content before the JSON object. This is a request-policy/parser hypothesis, not a CUDA crash. No settings changed. Preserve baseline-preflight.json; next isolate the reasoning flag with JSON-only default/enabled controls before mixed-load trials.

### Request-policy control

JSON-only enabled control passed, then enabled smoke/JSON/retrieval/tools all passed (4/4 tools; separate reasoning required). Request-policy delta only; no container, recipe, route or client change. Stability scenarios will use enabled thinking, and retain this policy difference from the disabled first attempt. No crash explanation follows from this quality finding.

### Request-policy repetitions

Alternating JSON-only controls retained under json-policy-*.json: disabled repeats 2 and 3 failed; enabled repeats 2 and 3 passed. Together with the first pair this is disabled 0/3 vs enabled 3/3 (plus a separate enabled full-preflight pass). Deterministic temperature/cache reuse limits generalization; this establishes the observed request-policy failure for this fixture, not broad model quality. No deployment settings changed.

## First live runner qualification

Independent code reviewer accepted executable commit 609032f2; docs-only revision 13d5bdb0. Small 4K/1K overlap completed all runtime, token-usage, client-overlap and retrieval gates, with managed identity matched before/after. This qualifies the local tokenizer/stream adapter for the next bounded trial; no scheduler-batch or long-context stability claim. Artifact baseline-small.json.

## Serial control completed

{"trial": "baseline-175k-serial", "status": "completed", "tokens": {"anchor": 174798, "contender": 31820}, "outputs": {"anchor": {"completion_tokens": 4096, "prompt_tokens": 174798, "total_tokens": 178894}, "contender": {"completion_tokens": 220, "prompt_tokens": 31820, "total_tokens": 32040}}}
All runtime/token/retrieval gates passed. Client overlap was intentionally absent. No root-cause closure.

## 2026-09-23 16:57:18Z — crash reproduced

The 175K serial control passed, but the 175K/32K overlapping control crashed on driver 615.71.09. Scheduler dump proves 1 decode + 2047 prefill, anchor computed 174807/output10, fresh prompt31816, KV14.96%, no spec or MM. Both GPUs logged Xid31 WRITE/PDE faults. Stack matches the prior DCP top-k path. The client artifact says protocol_error because the first runner omitted SSE error-frame details; owner/kernel correlation is retained separately in crash-175k-correlation.json. No later context or batch experiment ran.

Exact baseline was recreated after preserving logs/kernel/status. A new single-delta DCP1 candidate is under independent review; batch1024 is prepared but deferred because source evidence implicates the DCP merge. Base Git revision is discoverable in an external fork, but the downstream kpool addition is absent there, so source attestation is partial. No raw Docker fallback was used.

## Recovery 1 acceptance

Exact baseline recipe recreated (new container ID redacted:sha256:a49ddaf8d2bb114fb7e65fac204b28377454a332da8adb546543176c725a3b3b). Managed load, direct enabled smoke/JSON and authenticated llm.primary smoke/JSON passed. No route/client/driver change. Recovery does not resolve the crash. Startup log retained.

## DCP1 diagnostic candidate

Independent follow-up accepted the trial after SSE-ID validation and recipe metadata corrections; executable d063fdbf freezes those changes. Exact recipe digest561d548b4124a98493c88c4145405f40ed25c747bde9ddf9a17696fe8a96661d, registry cfbc4dc4f4f7e8a73590e21cf95e7586172aab67dc4a892a51ca45adc77607f4. Router idle0 before the managed baseline unload. Candidate load started17:22UTC. Effective startup logs prove DCP1; runtime normalizes the now-unused DCP communication backend to ag_rs despite the retained requested a2a flag. TP2, EP2, FP8 MLA KV, APC, chunked-prefill, batch2048, C4, context327680 and no speculation retained. Shared compiled-cache volume retained; this is not cold-compile performance evidence. CUDA core dumps deferred for this mitigation trial, so first-invalid-access attribution remains unresolved. No reverse-parent recrash is required for this known GPU fault.

### Small-trial budget error retained

DCP1 preflight smoke/JSON/needle/tools passed with thinking enabled. The first small overlap used an accidental512-token anchor cap rather than baseline2048. It returned reasoning deltas but no visible answer; contender retrieval passed. Owner logs and kernel checks show no new GPU/engine fault and metrics are idle afterward. Reasoning-budget exhaustion is plausible; the old runner discarded completed response metadata on validation failure, so no exact finish/usage claim can be made for this attempt. Artifact dcp1-small.json remains immutable. Runner now retains returned bounded results before validation; real CLI regression protects that evidence. Corrected dcp1-small-matched-scenario.json restores the original2048/1024 caps; no175Krequest before its pass. Observed startup capacity825268tokens exceeds the conservative213100-token repro envelope.

## First DCP1 crash-shape replay passed

Matched small gates passed, then174800/31815 actual input overlap passed runtime, managed identity, usage, retrieval and client-overlap gates;4096-token anchor cap, terminal length. No new kernel Xid/OOM. This is one bounded mitigation observation, no first-kernel attribution, stability rate, natural-output-quality or performance claim. Native dcp1-175k-overlap.json retains request IDs. Parent and changed scenarios differ only in endpoint/config identities; random synthetic canaries yield slight actual-token differences.

## Campaign close

DCP1 completed all three matched 175K/32K overlaps with fresh prefixes. The 201K time guard refused admission (139.46 seconds available, 145 required), so zero 201K requests ran. Candidate unloaded; original recipe reloaded. Direct and authenticated routed smoke/JSON passed. Final managed identity, unchanged router file, exclusive mode/owners and candidate absence verified in restoration.json. No production promotion or client change. The diagnostic campaign is closed; larger context, cache churn, modalities and client quality remain untested.
