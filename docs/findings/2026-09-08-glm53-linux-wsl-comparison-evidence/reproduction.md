# Reproduction boundary

Use Anvil Serving revision `8abcc5dc75a1b2389210b553120abb76e5864d3a`
with the existing managed model, runtime and controls in
[configuration-and-identity.json](configuration-and-identity.json).
Verify `python3 -m anvil_serving.cli --version` and module source before work.
The two RTX PRO 6000 Blackwell Max-Q GPUs are one exclusive TP2 service.
This campaign uses the existing warm serve; it does not reload or clear caches.

The historical reconstruction starts from the
[public Windows recipe](https://github.com/fakoli/anvil-serving/blob/8abcc5dc75a1b2389210b553120abb76e5864d3a/configs/glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp-recipe.toml).
Native Linux adds `NCCL_P2P_DISABLE=1`; operator endpoint and cache bindings
are private. Preserve the pinned image, patches, model, C1 and 393216 context.
Use managed recipe status/logs for inspection. No load, unload, promotion or
client mutation is required to repeat these measurements against the same serve.

## Historical-style capacity

Use `eval benchmark capacity` with direct loopback endpoint, exact served name,
C1, thinking disabled, context seed0, max256, response_words0, shared prompt
cache, no canaries, max_model_len393216 and n3 for each target4096,120000,262144,
380000. Use n60 at4096 for endurance. The full native artifacts retain controls,
request timings and exact measured prompt/output token counts. The retained
[old/new prompt hashes](prompt-equivalence.json) prove prompt-byte equivalence.
These uncontrolled short-answer runs are descriptive historical comparisons.

## Independent and strict gates

- Direct preflight checks smoke, JSON, needle380000, tools20, long-tools160000,
  streaming tools, tool continuation, Responses, image and OCR. Thinking off,
  forbidden reasoning, visible1024, headroom0. Fixture and expected phrases
  are retained in native preflight JSON and the workload manifest.
- Direct thinking-on diagnostic checks smoke, JSON, tools5, tool continuation
  and Responses with visible2048 + reasoning8192 and required reasoning evidence.
- Strict 4K scout: n3, seed19, max512, response_words128, unique cache,
  request canaries, strict output. It failed; do not proceed to finalists.
- Separate natural-answer diagnostic: n10, seed29, max256, response_words0,
  unique cache and canaries. It also failed compliance; dependent contexts stop.
- Coding-agent-v2 repeats its five deterministic cases three times, thinking
  off, visible2048 and headroom0 with [control proof](thinking-control.json).
- Image corpus repeats six repository cases twice at C1, thinking off, max2048;
  the exact 40-hex OCI build revision is distinct from the image digest.
- Routed preflight uses the declared alias and environment-backed token; repeat
  the disabled gate, retain 380K HTTP413, then separately label the260K probe.
  Enabled gate uses1024 visible+3072 reasoning to fit routed4096.

## Durable jobs

Use `eval benchmark context`, `agentic`, and `swe` managed job surfaces for
submission, status and artifact retrieval. Public native job manifests retain
profile, sanitized topology, pins, stage references and outcomes. Context uses
150 cases across five depths, three case families, five positions and two
repetitions with4096 output headroom. Its native adapter uses default thinking.
Agentic deep runs30 observations; the router clamps the profile's requested
completion budget to4096. Both clients are co-resident endpoint-only clients.

SWE smoke uses an isolated macOS arm64 worker and the exact pinned instance
`django__django-11099`. Harness, environment, dataset and official grader pins
are retained in the [SWE stage](swe/evidence/2-swe.json) and
[asset preparation](swe/evidence/0-assets.json). Router credentials are provided
only through the worker's transient environment. One smoke is not a full-suite
score. Do not launch simultaneous model benchmark jobs for this C1 campaign.

After completion compare managed identity, configuration hashes, router models,
GPU/cache state and bounded owner logs with the starting state, then run a
post-workload smoke. Regenerate graphs and the artifact manifest from retained
native files, inspect privacy, and run repository documentation gates.
