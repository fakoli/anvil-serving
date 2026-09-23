# Runtime stability and configuration experiments

Use `anvil-serving eval benchmark stability` to recreate a long decode plus a
new request. It collects diagnostic evidence, not a throughput ranking. Run
the exact managed model through preflight before this test; preserve managed
logs, kernel faults and the starting recipe alongside the trial.

```text
anvil-serving eval benchmark stability --scenario scenario.json --output trial.json
anvil-serving eval benchmark stability --scenario scenario.json --output trial.json --confirm
```

The first command previews offline. The second sends requests to the declared
endpoint and writes a new artifact; existing trial files are never replaced.
Lifecycle stays with `models recipes` or `serves`. The runner does not restart,
reconfigure or promote a model. Use an authorized maintenance window for a
known-crashing workload. Treat artifacts as private until reviewed for release.

## Scenario

Save the following as `scenario.json`, replacing the model and immutable
configuration identities with verified values. The command checks the local managed container before and after every round:
container ID, running state, image content ID, bound port and served name, plus
recipe/registry/revision labels. Labels establish managed provenance, not proof
that every engine setting took effect; retain startup logs separately. Remote
endpoints are unsupported by this initial adapter.
The initial adapter requires vLLM's `/tokenize` chat API at the parent of the
declared `/v1` base. Tokenization mismatch fails workload coverage.

```json
{
  "schema": "anvil-serving.stability-scenario/v1",
  "base_url": "http://127.0.0.1:8001/v1",
  "model": "example-served-model",
  "managed_container": "example-model",
  "configuration": {
    "label": "baseline",
    "recipe_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "registry_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
    "image_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "model_revision": "2222222222222222222222222222222222222222"
  },
  "context_limit": 327680,
  "anchor_tokens": 175000,
  "contender_tokens": 32000,
  "anchor_max_tokens": 2048,
  "contender_max_tokens": 256,
  "rounds": 3,
  "mode": "overlap",
  "prefix_mode": "unique",
  "timeout_seconds": 600,
  "run_timeout_seconds": 1800,
  "chat_template_kwargs": {"enable_thinking": false},
  "temperature": 0.0
}
```

Optional `api_key_env` names a credential environment variable. Never put the
credential itself into the scenario. Optional `reasoning_effort` and
`chat_template_kwargs` must match the tested deployment policy. Compare a
thinking-disabled diagnostic separately from production reasoning quality.

`serial` waits for the anchor to complete; `overlap` releases the contender
after the anchor's first generated text or reasoning delta. A terminal event
is not a decode trigger. Coverage also requires anchor output while the
contender has returned HTTP headers but is awaiting its first output. Correlate engine logs to establish
that a specific scheduler batch occurred; client timings do not prove it.

`unique` changes prefixes each round. `repeat` reuses each role's prompt within
the run. Both begin with a run-specific salt. Cache hit, eviction and reuse
counters must be collected separately; matching prompts are not cache proof.
The runner calibrates synthetic input to within 0.5 percent or eight tokens
of the target and checks actual response usage against that count. It keeps
three known retrieval values and asks the anchor for a natural long guide.
Early EOS may miss overlap; it is incomplete coverage, not a successful soak.

## Evidence contract

`anvil-serving.stability/v1` is additive to existing native benchmark schemas.
It retains scenario/hash, declared configuration and managed identity observations,
UTC boundaries, local monotonic request/output timing, prompt hashes and
token counts, request IDs when supplied, finish reasons, usage, bounded output,
partial failure evidence, and per-round coverage/retrieval/runtime results.
Generated visible text is capped at 8192 characters; full-output coherence and
repetition claims require a separate complete-output capture and oracle.

The artifact is checkpointed before requests and after their completion; an
interrupted process may leave `running` evidence. Never count it as completed.
The CLI runs an isolated client process with a hard total deadline, terminating
its process tree on expiry and retaining the latest checkpoint as a client
timeout. Socket timeouts and cooperative stream checks are supplementary; a
partial line cannot extend the total deadline. Cleanup can add a few seconds.
Direct Python `run()` callers own cancellation; use the CLI for bounded trials.
The preview counts all possible HTTP calls: at most sixteen tokenization and
two generation requests per round, plus one model-list read. Tokenizer prompts
are capped at 24 MiB of filler, metadata at 16 MiB, and each SSE line at 1 MiB. An
already in-flight companion may finish after another request fails; no new
round starts. A client timeout does not prove the server stopped computing.
Verify managed activity before recovering or starting another experiment.

Runtime completion, token/overlap coverage, and literal retrieval are separate
booleans, with explicit failure classes for protocol, transport, HTTP, client
timeout, upstream stream errors, absent visible answers, retrieval and missing
coverage. Completed response metadata remains retained even when answer
validation fails. A valid terminal reasoning-only response is
`semantic_output_absent`, not malformed protocol; its usage and finish reason
help distinguish output-budget exhaustion from an engine error. Stream IDs
must remain exact and unchanged within a request. These do not identify a model crash
without owning-engine evidence. Missing or failed coverage blocks a completed scenario. `length` is
an allowed diagnostic finish reason; it does not establish natural completion.
`scheduler_overlap` stays `not_measured`, and `performance_eligible` stays
false. Neither this artifact nor an ordinary throughput result resolves an
incident that its workload did not exercise.

Follow the [qualification investigation workflow](https://github.com/fakoli/anvil-serving/blob/main/skills/anvil-serving-llm-qualification/references/runtime-investigation.md)
for parent/changed/reverse-parent trials, bounded envelope searches, detailed
recipe notes and restoration. Use the existing [campaign publication contract](repeatable-campaigns.md)
for the full identity, raw evidence, failed attempts, tested limits and public
recipe reconstruction. Preserve each failed artifact under its own identity.
