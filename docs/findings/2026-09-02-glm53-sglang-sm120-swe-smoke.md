# GLM-5.3-Flash SWE-bench smoke

**Date:** 2026-09-02

**Decision:** the repository-agent smoke passed and adds bounded evidence
without changing the published benchmark guidance

**Evidence classes:** `functional`, bounded `quality`, benchmark-infrastructure
qualification

## Outcome

The exact GLM-5.3-Flash subject resolved `django__django-11099` under the
pinned official SWE-bench grader. The fixed smoke attempted one instance,
graded one, and resolved one. Mini-SWE-agent made 11 routed model requests; its
agent stage completed in 34.216 seconds and official grading completed in
29.076 seconds. The durable job finished successfully in 63.495 seconds from
worker start to terminal evidence.

This is a valid end-to-end repository-agent result: the isolated worker,
Linux/amd64 evaluation container, routed model requests, patch submission, and
official grader all completed. It is one fixed smoke instance, not a claim of
100% on SWE-bench Verified or a representative comparison with another model.
The retained trajectory did not expose normalized token totals or a comparable
per-instance agent duration, so neither is inferred.

## Exact subject and topology

- Model:
  `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO@c3cbb9891b67c741bcbf6b176dd7af9265b069db`
- Served identity:
  `glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp`
- Runtime image digest:
  `sha256:0c0637959c3931829f05154087bbefd2c50003fb9b2010200ce0ec82f4d71a53`
- SGLang source revision:
  `4c2c169b53dbf362f0cd95111f4ae275cd0167c1`
- Recipe: ModelOpt W4A16/NVFP4 K32 experts, FP8 KV, adaptive EAGLE,
  TP=2, 393,216 configured tokens, C1, 4,096 maximum output, thinking enabled
- Model hardware: two RTX PRO 6000 Blackwell Max-Q cards in exclusive
  TP=2 over PCIe without NVLink
- Worker: isolated macOS arm64 host with the official Linux/amd64 evaluation
  image under Docker Desktop emulation

The worker hosted no model. It owned the harness environment, evaluation
container, official grader, durable job state, and evidence while all model
requests traversed the declared routed endpoint contract.

## Harness identity and preflight

The smoke pinned mini-SWE-agent
`a83fcae82d2a08f0ee0c688f9d137b3566c097f8`, official SWE-bench
`f7bbbb2ccdf479001d6467c9e34af59e44a840f9`, and SWE-bench Verified dataset
`c104f840cc67f8b6eec6f759ebc8b2693d585d4a`. The managed worker created an
isolated CPython 3.14.6 environment for those exact source checkouts and
recorded 113 resolved packages under inventory hash
`b4ad822012fce20c5c4ee851a71d68920361f61fd6586edc56a85c338d355b52`.
The instance image resolved to repository digest
`sha256:cca302934edd881cc4f1e4647a389a25cdaa3c02bfa33667a4204934a09c4fb1`.

Preflight passed worker isolation, macOS/arm64 compatibility, 117,047,164,928
free bytes, container capability, owned-output writability, harness assets, and
endpoint identity. The submitted spec, asset stage, preflight, SWE stage, and
terminal result are independently hash-addressed in the public summary.

## Fix-forward sequence

Setup failures before the retained run were infrastructure evidence and were
not counted as model attempts:

1. The worker first lacked a usable endpoint credential. The existing
   worker-side credential was corrected without publishing or embedding it.
2. The pinned mini-SWE-agent and official grader source trees were present, but
   their Python dependencies were absent from Anvil Serving's intentionally
   stdlib-only environment. The managed asset step now creates a separate
   revision-bound venv and verifies its resolved package inventory on reuse.
3. A launch-service PATH did not include Docker Desktop's CLI. Detached workers
   now resolve the standard macOS Docker locations and record the absolute
   executable in the immutable plan.
4. The initial venv safety check treated the standard Unix interpreter symlink
   as an escape. It now accepts only the canonical venv executable and requires
   its target to match the exact interpreter that created the environment.
5. Mini-SWE-agent silently loaded a user-global `.env` containing stale
   container settings. Managed runs now use a run-owned empty global-config
   directory, preserving explicit benchmark inputs while excluding ambient
   user state.
6. A zero-exit mini-SWE-agent batch can still contain an instance with no
   trajectory. The worker now preserves that agent-stage failure while sending
   available predictions to the official grader and recording any partial
   grading, rather than misclassifying all instances as test failures.

The retained run followed these corrections and completed both agent and
official-grader stages.

## Decision and limits

The result adds bounded repository-agent evidence to the direct, long-context,
coding, media, endurance, routed, and real-client evidence already published
for this model. It does not expand concurrency beyond C1, prove the full 393K
window inside an SWE trajectory, or replace a stratified scout.

## Public evidence

- [Sanitized machine-readable summary](2026-09-02-glm53-sglang-sm120-swe-smoke-evidence/summary.json)

The public artifact omits credentials, the private endpoint, machine-local
paths, the issue text, trajectory messages, and model response bodies. Private
operator evidence retains the digest-bound source artifacts.
