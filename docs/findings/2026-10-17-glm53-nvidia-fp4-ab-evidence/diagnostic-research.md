# Candidate context-miss diagnostic research

This note records bounded fault-isolation research for the candidate's initial
long-context needle miss. It does not identify a known runtime defect, alter a
gate result, or qualify a configuration change.

## Pinned inputs

The evaluated checkpoint is
[`nvidia/GLM-5.3-Flash-FP4-Local` at `76348ddb2be2e35e39ecfc00e75dc07703f4f3d5`](https://huggingface.co/nvidia/GLM-5.3-Flash-FP4-Local/blob/76348ddb2be2e35e39ecfc00e75dc07703f4f3d5/README.md).
NVIDIA's RTX PRO 6000 recipe explicitly uses the Local Inference Lab image,
FP8 KV cache, B12X, DCP2, and MTP3. The candidate preserved those controls,
with the campaign's bounded context and concurrency settings.

The OCI build-source revision is
[`local-inference-lab/blackwell-llm-docker@6a833b6`](https://github.com/local-inference-lab/blackwell-llm-docker/commit/6a833b698f0b41a9c12922343553eee419b9f465),
not the vLLM runtime repository. Its change is a formatting-only launcher
change and records no behavior change. The running engine is separately
identified in protected startup evidence as `v0.1.dev21830+g8244d1c44`.

The public [startup observation](feasibility-startup-observation.json) binds
the checkpoint, image digest, configuration, and protected-native evidence
hashes. It records startup behavior only; it is not a request-correctness or
long-context-quality result.

## What the miss does and does not show

The initial context suite used `temperature=1.0`, `top_p=0.95`, and one
repetition. Sampling can therefore produce a different continuation on a
repeat without any configuration difference. The deterministic fixture check
established that the expected needle was constructed once and that the stopped
response denied it; it does not turn one sampled miss into proof of a runtime
fault.

MTP3 is a reasonable *conditional* fault-isolation variable. The runtime logs
warn that more than one speculative token may lower draft acceptance. That is
a performance/acceptance warning, not evidence that standard rejection
sampling corrupts a target response. A future MTP-off arm would be a
single-concept experiment: remove the speculative configuration while holding
the checkpoint, image, TP2, DCP2, B12X, FP8 KV, context, template, and request
controls fixed. A single arm or response would still not prove MTP caused a
different outcome, particularly under the sampled context policy.

FP8 KV is a separate plausibility lead: the runtime states that FP8 KV may
reduce accuracy without suitable scaling. It is not selected for this
diagnostic because changing KV precision changes capacity and other feasibility
controls. The startup observation reports 82.73 GiB model-loading memory and
7.12 GiB explicitly reserved FP8 KV per device; doubling the latter is only a
rough capacity concern, not a mathematical proof that BF16 KV cannot start or
run on this host.

DCP2 and B12X sparse attention remain unproven alternatives. Protected startup
evidence records successful DCP communication and the intended B12X prefill
path, without a transport or attention failure. The same evidence notes a
sparse-only prefill route, but no source here connects it to the observed
needle miss. Host embedding controls are not a likely explanation for this
text-only request; the observed draft-embedding limitation applies to external
multimodal embeddings.

Several recipe environment names were reported as unknown by the generic vLLM
environment registry. That does not establish that this specialized runtime
ignored them, so it is not treated as a defect lead.

## Finding

No primary source or pinned-build record establishes a cause-matched known bug
for the initial miss. Preserve the original result. If a later accepted
candidate result fails a target context case, an MTP-off arm is a bounded
fault-isolation experiment rather than a causal conclusion or promotion basis.
