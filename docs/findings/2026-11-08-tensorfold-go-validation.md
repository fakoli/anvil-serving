# TensorFold Go coding validation

**Date:** 2026-11-08

**Scope:** Two focused, sequential C1 Aider/Polyglot Go diagnostics on the
pinned GLM-5.3-Flash EXL3 TensorFold configuration, through the existing
authenticated router on one native Linux inference owner.

**Decision:** Focused `quality` evidence, `no-promotion`. One of two profile
checks passed; this is one unique Go task, not a representative coding score.

<!-- benchmark-result-card/v1 -->
## Result card

> TensorFold passed the selected Go `alphametics` task on its first coding
> attempt with a 32,768-token response cap. The 16,384-token check failed both
> attempts after length-finished responses. This is two bounded profile
> checks on one task, with no causal cap-benefit or performance claim.

| Setup | Recorded value |
|---|---|
| Model | `Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold@76c0b5173166d2795dd48860f45d8224817f894c` |
| Hardware | Two RTX PRO 6000 Blackwell Max-Q 96 GB GPUs; co-resident harness and contained test execution on native Linux |
| Runtime | TensorFold base `f350cd3ea253a042e205746928271f9d8f71a82d`, the author's unchanged 101-patch series, followed by the five ordered [Anvil telemetry patches](https://github.com/fakoli/anvil-serving/blob/8c8ffa146e0e9cd1cc73e08be0db5c9c38be9e8f/configs/runtime-patches/tensorfold/f350cd3e-anviltelemetryv1/README.md); final source tree `e7acc47a4a42ba8ac5fbaf74fff20ed4e7856c17` |
| Image | `sha256:8f5604d13d319adac8dc708b2e8bf8ce54096e181313a2a8de83cbfdd2902b06` |
| Recipe | Existing managed EXL3 4-bpw, dense-q8/BF16-head, TP2, FP8 paged-KV and 4-bit DFlash2 recipe; serving defaults preserved |
| Measurement path | Routed online through `llm.primary`; cache policy preserved, warm state uncontrolled |
| Contract | Configured 327,680-token context and C4 admission; these jobs request C1, max reasoning, temperature 0.6 and top-p 0.95 |
| Evidence | Two completed, independently graded quality jobs; one passed and one failed |
| Decision | No model, route, context, cache-policy or lifecycle change; no promotion |

| Headline observation | Local result | Conditions |
|---|---:|---|
| Passing profile checks | 1 / 2 | One unique Go task, once per cap; targeted sample |
| 16K contained test attempts | 0 / 2 passed | Both HTTP responses ended with `length` |
| 32K contained test attempts | 1 / 1 passed | Native first-attempt grade; no repair attempt |
| Accounted transport responses | 4 / 4 | Four starts, four responses, zero pending or transport errors |

**Why it matters:** The 32K check demonstrates an independently executed Go
solution on this exact configuration. The 16K failure shows that a completed
job can still exhaust a response cap and fail its coding oracle.

**Important caveat:** These are single stochastic checks on a deliberately
selected task. They do not establish broad Go quality or prove that changing
only the cap caused the different outcome.

[Artifact manifest](2026-11-08-tensorfold-go-validation-evidence/artifact-manifest.json)
· [Evidence index](2026-11-08-tensorfold-go-validation-evidence/README.md)
· [Authoritative observations](2026-11-08-tensorfold-go-validation-evidence/case-diagnostics.json)

## Method and fixed controls

The existing `aider-diagnostic-16k-v1` and
`aider-diagnostic-32k-v1` profiles each select one task, permit two edit
attempts and at most twelve transport calls, disable retries and retain a
600-second suite deadline. The response cap is per response, not a pooled
job token allowance. Requested max reasoning is symbolic; no numerical
reasoning reservation is assumed.

The serving recipe retains its configured `high` reasoning default; the
jobs request `max` through the existing router. These labels are not a
measured internal thinking budget. The [configuration record](2026-11-08-tensorfold-go-validation-evidence/configuration.json)
keeps configured policy and requested controls distinct.

Both jobs select `go/exercises/practice/alphametics`. Aider is pinned to
`5dc9490bb35f9729ef2c95d00a19ccd30c26339c` and Polyglot to
`7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f`. The selected independent
oracle image is
`sha256:174d85341f24c4c6d3f41793a3a92d2d3626a86e8797151db5dcab8696645b58`;
offline preparation rechecks failing starters and passing reference solutions
without building, pulling or retagging that image.

These are ordinary managed jobs, with an explicit review between them.
Fresh managed serve and route checks, worker termination and engine drain
are checked separately. They do not acquire managed-campaign activation
custody. Shared user traffic may coexist, so engine totals cannot be
attributed exclusively to these jobs.

## Results

| Response cap, including reasoning | Independent task grade | Tests and response observations |
|---|---:|---|
| 16,384 tokens | 0 / 1 | Both contained test attempts failed, without timeout; both responses ended with `length`, each reporting 16,384 completion tokens and 16,384 reasoning tokens |
| 32,768 tokens | 1 / 1 | First contained test passed, without timeout; the two transport responses ended with `stop`, reporting 32,745 and 1,296 completion tokens |

The [16K result](2026-11-08-tensorfold-go-validation-evidence/go-16384/aider/result.json)
retains response prompt counts of 1,133 and 1,465 tokens. Each response
reports zero derived non-reasoning tokens. The unchanged starter solution
failed `go test ./...` twice. The job used two transport calls in 143.762
seconds of its 600-second budget, which was not exhausted.

The [32K result](2026-11-08-tensorfold-go-validation-evidence/go-32768/aider/result.json)
retains a passing first-attempt grade. Its first response reports 1,133 prompt,
32,745 completion and 31,024 reasoning tokens; its second reports 2,399 prompt,
1,296 completion and 755 reasoning tokens. Derived non-reasoning counts are
1,721 and 541, respectively; independently measured visible-token counts
remain unknown. Neither response reached the configured cap. The job used
two transport calls in 148.777 seconds, with no whole-job budget exhaustion.

Transport calls are not coding attempts. In the 32K event stream, the second
request starts while the first contained test is running; the native grader
retains one passing test and no repair attempt. Both responses are included
in request accounting, without treating the second as another graded task.
These buffered intervals are retained for budget diagnosis, not speed ranking.

## Failures and caveats

The first submitted 16K job failed during asset preparation, before model
traffic: the ordinary worker repeated a successful standalone preparation
under the same run ID, and its starter-task copy collided with the existing
directory. The worker exited, produced no request-event stream and left the
observed engine completion counter unchanged. The retained failed setup is
excluded from model grades. A reviewed plan amendment uses fresh run IDs and
lets each worker perform its one preparation pass, with the same pinned
oracle, adapter and budgets. The
[repeat-preparation defect](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-11-08-aider-selected-oracle-prepare-submit.md)
remains open; avoiding the path collision does not fix the product defect.

Historical Go checks used the direct backend, while this check uses the
existing authenticated router. The path difference, small targeted sample
and uncontrolled cache state prevent a matched performance comparison or a
causal claim about the response cap.

Server completion-token usage includes reasoning. Derived non-reasoning
tokens are completion minus reasoning when both values are available; that
quantity is not an independently measured visible-token count. A `length`
finish is reported as a response-generation limit separately from the
unchanged independent coding grade and whole-job budget exhaustion.

Native Aider's 16K `num_exhausted_context_windows=2` label is preserved as
source data. The actual prompt-plus-completion totals are 17,517 and 17,849
tokens; the label does not establish exhaustion of the configured 327,680-token
context. Its zero token totals and missing reasoning-effort field also do
not override the complete transport-usage observations and pinned requested
controls. The 32K native grader counts its scored response, while transport
accounting includes both requests.

## Evidence boundary

This checks two executions of one deliberately selected Go task. It does
not qualify general Go coding, full-context concurrency, broad model
quality or a serving-performance comparison. The earlier frozen comparison
remains a separate 12-cell, 84-request record with incomplete 17-family
metric coverage. No telemetry speedup is attributed to this validation.
