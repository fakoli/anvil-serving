# Xeon model qualification campaign

<!-- benchmark-result-card/v1 -->

| Field | Result |
| --- | --- |
| Date | 2026-10-05 |
| Scope | prospective Qwen replacement and GLM CSF/QAD compatibility scout |
| Measured hardware | two RTX PRO 6000 Blackwell Max-Q cards |
| Baseline / frozen replacement floor | selected GLM r11 DCP1; 4/5 SWE |
| Candidate outcome | Qwen 3/5 rejected; GLM initial max-only SWE 4/5, but fresh-reload A1 repeat 2/5 attempts (2/4 graded) |
| Functional headlines | Qwen medium 25/25; GLM max functional 25/25; GLM max-only Oracle 18/18 |
| Strict C4 headline | baseline, Qwen, and GLM each 0/4 performance-eligible |
| Evidence | [sanitized artifact bundle](artifacts/2026-10-05-xeon-model-qualification/README.md) |

This campaign rejects both evaluated replacements and retains selected GLM r11
DCP1. Managed restoration completed with direct 25/25, routed 7/7, and a fresh
Pi read-tool turn returning an unseen fixture value; routes and manifest hashes
were unchanged. It does not establish a hardware speed result, long-context
capacity, or a qualified replacement.

## Public reconstruction

All probes used a sanitized direct-endpoint reference. The published evidence
retains only its endpoint class and served-model identity, not an address.
“Configured” context and C4 are setup values, not measured long-context or
concurrent-capacity results.

| Cell | Engine / quant / KV | TP / DCP / APC / speculation | Context / output / images / max batched tokens | Containment and reasoning |
| --- | --- | --- | --- | --- |
| Baseline `baseline-local-tier-r11` | pinned GLM EXL3 4-bpw / FP8 DS-MLA KV | TP2 / DCP1 / APC enabled / no speculation | C4; 327,680 context; 65,536 configured output; 8 images, 0 video; max batched tokens 2,048 | 52 GiB RAM + 1 MiB swap; request controls are retained separately in each native artifact |
| Qwen `candidate-local-tier-r1` | vLLM `00a33e…` / ModelOpt mixed / FP8 KV | TP2 / DCP1 / APC enabled in recipe / no speculation | C4; 262,144 context; 65,536 configured output; 8 images, 0 video; max batched tokens 6,019 | 52 GiB RAM + 1 MiB swap; 51.58 GiB/rank loaded; medium functional profile |
| GLM `glm53-csf-qad-tp2-dcp2-c4-327k-r1` | vLLM `00a33e…` / NVFP4-CSF with MXFP8 attention/shared experts / FP8 KV | TP2 / DCP2 / APC enabled in recipe / no speculation | C4; 327,680 configured; recipe output cap 65,536; 8 images, 0 video; max batched tokens 4,096 | 52 GiB memory + 1 MiB swap; default HIGH, corrected Oracle/SWE uses `thinking_mode=default` + `reasoning_effort=max` |

The [configuration record](artifacts/2026-10-05-xeon-model-qualification/configuration.json) retains all three exact model revisions, image digests, and configured limits. Completion caps used by individual probes can be smaller than the configured output ceiling. These values do not establish a measured full-window envelope.

The baseline reconstruction is from the selected
[r11 DCP1 finding](2026-09-23-glm-dcp1-qualification.md); the Qwen and GLM
rows are from their linked startup identities. The common runtime image and the
candidate revisions are pinned in the [source registry](artifacts/2026-10-05-xeon-model-qualification/source-registry.json).

The [research dispositions](artifacts/2026-10-05-xeon-model-qualification/research-dispositions.json)
retain candidate leads and exclusions. Artificial Analysis values are hosted/API
priors, not local quantization or hardware evidence. The pinned runtime's
host-RAM cache documentation conflicts with launcher behavior that limits
recurrent restore to text; VRAM-only cache does not resolve that multimodal
boundary.

## Retained results

The evaluated Qwen replacement profile,
`local-inference-lab/Qwen3.8-Flash-Next-NVFP4@6909a5bed089a48fa07e956d3915af2537de9368`,
passed disabled preflight 2/2, medium preflight 25/25, and Oracle 18/18, then
was rejected at **3/5** against the frozen r11 4/5 primary coding floor. Its
strict C4 128-word cell completed **0/4** performance-eligible requests.
Those results reject that evaluated profile; they do not make a family-wide
claim about Qwen.

The GLM candidate was
`local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD` at revision
`4f90b74165cb1542cca35730b7dfa4d550bcf0ce`, using the pinned runtime image
`sha256:6008f020c23065bef71390194f968af9f5ebbedf57807b1659f876632e4a1067`.
The startup record shows TP2/DCP2, C4, 327,680 configured tokens, disabled
speculation, and no OOM event. It is compatibility evidence only; 327,680 and
C4 are configured values, not measured envelope claims.

GLM disabled-thinking preflight passed 2/2 and the pinned HIGH profile
preflight passed 25/25. The first Oracle run passed 17/18; its only failure
returned a Markdown fence where structured-edit required JSON. The strict C4
128-word cell completed **0/4** performance-eligible requests, so it supplies
no throughput or speed result.

The original explicit enabled-thinking plus `max` request failed before
inference because those controls conflict. A corrected, separate Oracle run
uses `thinking_mode=default` with `reasoning_effort=max`, which derives the
pinned API's effective enabled-thinking behavior and passed **18/18**. The
same max controls passed functional preflight **25/25** and the single five-task
SWE scout resolved **4/5**; only Django failed. That meets r11's absolute
replacement floor, but differing reasoning controls prevent a quality-improvement
or causal comparison claim.

The candidate passed four exact eight-image requests. It also passed 4/4
context probes at 247,617–247,638 actual input tokens with a 65,536-token
maximum, and 4/4 near-full probes at 316,766–316,774 actual input tokens with
an 8,192-token maximum. These are acceptance results, not generated-output
lengths or a full 327,680-token request. An offered-C4 long summary passed 4/4
with own canaries, no foreign output, and terminal completion at 165,903–165,908
actual input tokens (nominal 201,000). It does not establish simultaneous
occupancy or speed.

Independent review locked a fresh-reload ABBA comparison: candidate A1,
incumbent B1/B2, candidate A2. Every period required Oracle 18/18 and SWE at
least 4/5; its sole comparative metric would have been SWE resolved tasks per
agent-stage hour, excluding grader and preparation time, with paired and
aggregate improvement of at least 10%. A1 failed the quality stop: of five
attempts, four were graded and only Sphinx and Xarray resolved; Django and
Pylint graded false, and Sympy reached the 60-step limit ungraded. The native
graded resolve rate is 2/4 = 0.5, and the campaign result is 2/5 attempts,
below the 4/5 floor. The earlier 4/5 scout remains retained but does not show a
reliable floor. B1, B2, and A2 did not run, so no comparative speed result
exists.

During matched candidate A1 SWE work, the upgraded host exposed PCIe Gen5 x16
on both cards under actual model load (100% GPU utilization, 292.24/286.35 W
under 300 W limits, and 72/74 °C). This [point-in-time
observation](artifacts/2026-10-05-xeon-model-qualification/hardware-loaded-link.json)
is not PCIe bandwidth, comparative speed, or thermal-peak evidence.

## Closure status

- Exact r11 restoration is [complete](artifacts/2026-10-05-xeon-model-qualification/restoration.json):
  its pinned model, image, recipe, and registry were restored; direct 25/25,
  routed 7/7, and a fresh Pi `anvil/llm.primary` read-tool turn passed. Both
  router tiers were ready and admitting, their router/model-manifest hashes
  were unchanged, and only restored-model GPU workers remained.
- Independent review now reviews the retained failure and closure evidence; it
  does not reopen candidate advancement.
- Strict-C4 remediation, the explicit served-name recipe flag, and the
  host-RAM-cache trial are unexecuted leads. They are not authorized follow-on
  changes from this campaign.
- This finding authorizes no model, route, lifecycle, or hardware change.

## Evidence

- [GLM startup identity](artifacts/2026-10-05-xeon-model-qualification/glm-startup-identity.json)
  and [ready status](artifacts/2026-10-05-xeon-model-qualification/glm-ready-status.json)
- [GLM disabled](artifacts/2026-10-05-xeon-model-qualification/glm-preflight-disabled.json)
  and [HIGH](artifacts/2026-10-05-xeon-model-qualification/glm-preflight-enabled.json)
  preflights
- [GLM original Oracle evidence](artifacts/2026-10-05-xeon-model-qualification/glm-agentic-artifact.json),
  [strict capacity evidence](artifacts/2026-10-05-xeon-model-qualification/glm-capacity-c4-strict128.json),
  and [initial repair plan](artifacts/2026-10-05-xeon-model-qualification/glm-max-repair-plan.json)
- [GLM corrected max-only Oracle](artifacts/2026-10-05-xeon-model-qualification/glm-max2-agentic-artifact.json)
  and the [single five-task workload manifest](artifacts/2026-10-05-xeon-model-qualification/workload-manifest.json)
- [Qwen coding evidence](artifacts/2026-10-05-xeon-model-qualification/qwen-swe-artifact.json)
  and [strict capacity evidence](artifacts/2026-10-05-xeon-model-qualification/qwen-capacity-c4-strict128.json)
- [Baseline functional](artifacts/2026-10-05-xeon-model-qualification/baseline-preflight.json)
  and [strict capacity](artifacts/2026-10-05-xeon-model-qualification/baseline-capacity-c4-strict128.json)
- Candidate sources: [Qwen revision](https://huggingface.co/local-inference-lab/Qwen3.8-Flash-Next-NVFP4/tree/6909a5bed089a48fa07e956d3915af2537de9368),
  [GLM revision](https://huggingface.co/local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD/tree/4f90b74165cb1542cca35730b7dfa4d550bcf0ce),
  and [pinned runtime source](https://github.com/local-inference-lab/blackwell-llm-docker/blob/c411dbf93f296de0aa6ad04c932df9103dfbee46/runtime/profiles/glm53-flash.yaml).
