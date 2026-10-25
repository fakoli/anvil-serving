# Reasoning-limit probe preparation

Prepared October 25, 2026; **not executed**. The [frozen request packet](reasoning-limit-probe.json)
defines two synthetic requests and retained runtime pins. These are prior
observations, not a fresh inspection of the running model. Ignis remains held.

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

## Execution prerequisites

Notify the user before inspecting or sending anything to the running model.
Then verify package/template bytes through the managed owner surface against
the packet. A version string or image label alone does not prove byte parity.

The existing shared route hard-overrides the numeric budget. Sending a caller
field does not bypass it. A separate, explicitly configured managed request
path to the **same endpoint** must be prepared and reviewed before execution;
the offline test does not install that path. Preserve the shared route, model,
provider, sampling, history policy and MTP settings.

Bind a supported raw-response capture path that retains finish reason, nullable
usage, and token IDs or equivalent evidence distinguishing forced closure from
natural closure. Aider's bounded observations alone cannot prove that
distinction. If capture cannot establish it, stop with enforcement unknown.
There is deliberately no claimed ready-to-run command until these managed
path and capture prerequisites are satisfied.

## Run and interpretation

Allow at most two sequential requests, zero retries, 120 seconds per request,
and 300 seconds including closure. Freeze both requests and source identities
before starting. Capture RAM availability/pressure, GPU and cgroup evidence
before and after; verify owned requests have drained. Stop on any packet stop
condition, retaining the failure and unknown fields. Do not add more cases to
make a failing probe pass.

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
