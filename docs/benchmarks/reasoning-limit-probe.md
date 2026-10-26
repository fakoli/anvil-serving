# Isolated reasoning-limit probe

The [frozen request packet](reasoning-limit-probe.json), prepared October 25,
2026, defines two synthetic requests and retained runtime pins. Its historical
authorization and execution fields remain unchanged. Current execution requires
explicit `--confirm`, fresh runtime evidence and the operator checks below.
Ignis remains held.

The earlier GLM diagnostic exhausted total completion caps of 16,384 and
32,768 tokens below the configured reasoning budget of 49,152. It therefore
did not exercise numeric reasoning-budget enforcement. Prompt-level
`reasoning_effort=max` is a separate control.

## Offline preparation

The packet fixes total output at 512 tokens and reasoning at 64, with C1,
temperature 0.6, top-p 0.95 and the retained MTP3 configuration. The first
request is a small arithmetic puzzle with an independent answer oracle. One
predeclared harder puzzle is permitted only if the first naturally finishes
reasoning below 64 tokens and produces a coherent answer. Natural completion
below the boundary leaves enforcement **untested**.

`tests/router/test_reasoning_limit_probe.py` loads this packet and captures the
real router/relay body's bytes using an in-process transport. It verifies
that an isolated tier's configured budget reaches the selected endpoint, that
the shared tier would still impose 49,152, and that neither configuration is
mutated by the request. This test opens no sockets. It does not prove engine
enforcement or authorize running the packet.

## Repeatable commands

First collect read-only runtime and resource evidence for the exact managed
recipe container (this runs a fixed CPU tokenizer inspection, never inference):

```bash
anvil-serving eval reasoning-limit-inspect --registry recipes.toml --container candidate --plan docs/benchmarks/reasoning-limit-probe.json
```

The inspection requires the pinned GLM recipe, verifies its owner/digest and
immutable container identity before and after capture, and returns source and
template hashes plus tokenizer markers. Compare **every** source hash with the
pinned source before traffic. It uses the engine's patched HF tokenizer path,
with local files only. Raw command arguments, arbitrary environment variables
and mount paths are excluded. RAM availability/pressure, swap activity, GPU
usage, cgroup memory and engine running/waiting gauges accompany the capture.

Record `router_config_sha256`, the exact `expected_model`, and nonempty integer
arrays `start`, `end`, `forced_end` in a private `boundary.json`. The command
currently supports identical natural and forced closing markers only.
Preview without network or writes:

```bash
anvil-serving eval reasoning-limit-probe --router-config router.toml --plan docs/benchmarks/reasoning-limit-probe.json --boundary-config boundary.json --output probe-evidence
```

After independently checking the fresh inspection and source parity, retain a
private execution copy of the plan with `execution_authorized=true` and
`live_identity_verified=true`. Record its hash together with the inspection and
source-parity hashes in the operator's verification record. Confirmed execution
requires that reviewed copy; it refuses the historical false/false packet.
Run the command with `--plan execution-plan.json --confirm`. Those fields record
the operator's verification; the request runner does not validate that separate
verification record or claim automatic source attestation. Its `executed` status
and zero exit code mean transport execution completed, not that the boundary or
answer gate passed; inspect `boundary`, `oracle`, and `response_anomalies`.

The evidence directory must be new. Request starts, exact outbound JSON, bounded
raw responses and terminal records are retained with owner-only permissions.
No shared router configuration is changed: only an in-memory tier copy gets the
64-token budget and token-ID capture controls. The selected endpoint and its
credential reference remain the same. This direct diagnostic does not share the
running router's admission counters. It checks the exact served identity and
zero running/waiting occupancy immediately before each request. This is an idle
observation, not an exclusive scheduler lease; run only during a quiet window.
Stop if occupancy or memory pressure makes the canary inappropriate.

## Execution prerequisites

Notify the user before inspecting or sending anything to the running model.
Then verify package/template bytes through the managed owner surface against
the packet. A version string or image label alone does not prove byte parity.

The existing shared route hard-overrides the numeric budget. Sending a caller
field does not bypass it. A separate, explicitly configured managed request
path to the **same endpoint** must be prepared and reviewed before execution;
the offline test does not install that path. Preserve the shared route, model,
provider, sampling, history policy and MTP settings.

Capture includes finish reason, nullable native usage, prompt token IDs and full
generated token IDs. The prompt opens the reasoning marker, so classification
uses the concatenated prompt and output. Exactly 64 tokens after the complete
opening marker followed by the complete closing marker is **boundary-consistent**
evidence. The API has no forced-versus-natural provenance flag; even that result
does not directly prove which kernel branch emitted the marker. Missing IDs,
partial/repeated markers or a later close leave enforcement unestablished.

## Run and interpretation

Allow at most two sequential requests, zero retries, 120 seconds per request,
and 300 seconds including closure. Freeze both requests and source identities
before starting. Capture RAM availability/pressure, GPU and cgroup evidence
before and after; verify owned requests have drained. Stop on any packet stop
condition, retaining the failure and unknown fields. Do not add more cases to
make a failing probe pass. Repeat the inspection after the requests and compare
identity/configuration, memory/OOM counters and engine running/waiting gauges.
Client socket closure is separate from engine drain; the runner leaves the
latter unknown until that owner-side evidence is supplied.

Report boundary enforcement and visible-answer correctness separately. A
correct puzzle answer does not prove forced closure; a forced boundary does
not guarantee a correct answer. Preserve marker counts and transition timing
privately; publish only necessary sanitized evidence. No quality or performance
conclusion follows from this two-request canary.

The pinned fork's forced-logit sampling path warrants specific inspection:
[upstream PR 58231](https://github.com/vllm-project/vllm/pull/58231) fixes a
split-top-p rounding defect, while the inspected
[fork kernel](https://github.com/local-inference-lab/vllm/blob/8244d1c44348ae8a5bbfc9f1b607021652283ed4/vllm/v1/sample/ops/topk_topp_triton.py)
retains the older comparison. This is a source-level risk, not a demonstrated
local failure or an explanation for runs below the 49,152 boundary. Preserve
the current runtime until applicability and behavior are measured.

Only after this gate passes should a separate coding comparison hold total
output at 16K and compare an 8K reasoning budget against the original control,
with the same task oracle, two-attempt policy and sampling. Changing effort is
a separate experiment. No Ignis run or promotion is part of this packet.
