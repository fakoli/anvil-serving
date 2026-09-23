# September 23 model qualification evidence

The native artifacts preserve successful, failed and partial attempts. This
bundle supports a bounded **no-promotion** decision for GLM Spark and Qwen Flash
Next on two RTX PRO 6000 Blackwell Max-Q GPUs. It is not a full SWE-bench score,
a general model ranking or a long-session reliability guarantee.

## Campaign and decision

- [Machine-readable summary](summary.json): results separated by exact request profile.
- [Artifact manifest](artifact-manifest.json): closed file inventory, ten evidence roles and hashes.
- [Source registry](source-registry.json): dated official and community leads; archive screening is not independent confirmation of every report.
- [Coverage and remaining limits](coverage-and-gaps.md).
- [Failures and durable dispositions](friction-log.md).
- [Restoration receipt](restoration.json).

The user authorized conditional promotion after qualification. Neither
challenger passed the required gates; no alias was changed. Qwen default xhigh
and medium are separate profiles. Native oracle thresholds are preserved in
raw files; the campaign requires all 18 deterministic assertions.

## Reconstruction and workload

- [Incumbent recipe](configuration/baseline-recipe.toml), [GLM Spark](configuration/glm-spark-recipe-r1.toml), [Qwen r1](configuration/qwen-recipe-r1.toml), [Qwen r2](configuration/qwen-recipe-r2.toml).
- [Frozen campaign plan](workload/campaign-plan.md) and [workload manifest](workload/manifest.json).
- [Five-task selection](workload/swe-scout-selection-v1.json): one task per repository selected before outcomes, 60 calls and 16384 completion tokens per call.
- [Long-guide suite](workload/long-output-64k-suite.json): one natural generation, 65536 total completion-token budget, including reasoning.
- [Verified oracle-7 launcher](configuration/launcher-oracle7.json). Earlier launcher and oracle records remain historical evidence.

Sanitized recipe paths and device identities are examples to replace through
managed recipe controls. No recipe is permission to evict an active workload.
Configured maximum context/concurrency is distinct from measured capacity.

## Native evidence groups

`native/` retains native preflight, agentic, multimodal, capacity, quality and
SWE schemas. Each profile in the summary names its exact source files.
Official SWE reports are retained for submitted patches. Unsubmitted attempts
remain visible and count in the all-attempts denominator. Per-instance token
and duration values absent from native SWE evidence remain unknown.

`review/` retains independent critiques, static long-guide screens, startup
limitations, test/source receipts and measurement confounds. These supplement
rather than replace the native results. Marker and syntax checks do not prove
that generated chapters form an integrated working program.

Strict capacity cells use unique prompts and request canaries. Any count,
marker, truncation or completion failure makes that request ineligible for
performance claims. No throughput chart is produced from failed populations.
Small samples do not establish meaningful p99 latency or causal MTP speedup.

## Redaction and provenance

[Sanitization receipts](sanitization-receipts.json) bind original private bytes
to public copies. Real operator paths, hosts, container IDs and GPU UUIDs are replaced with
synthetic examples. Nested source hashes in native artifacts still identify
the original source bytes; the campaign manifest hashes the public copies.
No private credentials or user task/session content is published. Retained
model outputs belong to the synthetic benchmark workload.

Public social copy was not requested, so the publication-summary role is not
applicable. The dated finding and documentation matrix provide the publication.
