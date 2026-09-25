# Frozen Swift-1.5 NInfer qualification plan

The launch profile is the managed 262,144-token, K8V4, DFlash2 K=7,
vision-enabled, thinking-budget-16,384, concurrency-4, 450 W RTX 5090
configuration. Host KV is 2 GiB and Host State is four slots. The model
artifact and NInfer source are pinned in the public recipe. The final served
profile will use the baked runtime image's immutable ID once its build passes.
No individual launch setting will be ramped as an experimental search.

1. **Direct scout before promotion:** exact artifact/runtime identity, health,
   smoke, JSON, tools, streaming, tool-result continuation, Responses, image,
   OCR, four-request tool admission, and one near-limit retrieval. Retain all
   failed attempts. The completed source-build scout passed these gates; repeat
   minimal identity and correctness checks on the baked runtime.
2. **Promotion and harness convergence:** capture the authoritative installed
   router and rollback profile; switch only the selected secondary alias after
   independent host identity verification; check direct and routed identity,
   readiness, reasoning, vision, limits, and real client-shaped requests.
   Reconcile every declared Hermes, Pi, OpenClaw, and WebUI installation from
   one authenticated router snapshot, including repeat no-change checks.
3. **Capacity after promotion:** use the exact promoted profile and native
   `capacity-v3` evidence. Run unique-cache controlled-output and canary cells
   at 4K C1/C4 (100 measured requests each), 32K C1/C4 (at least 30 each),
   128K C1/C4 (at least 10 each), and a near-limit C1 retrieval/capacity cell
   with actual prompt tokens, output headroom, and a small repeated population.
   Preserve failures and do not describe p99 as stable without an adequate
   population. A four-request full-262K claim is not implied by C4 at shorter
   contexts or the shared 282,112-token device pool.
4. **Quality after promotion:** run the native repeated chat, tool, session,
   and intelligence suites at the deployment reasoning policy, followed by
   deterministic agentic and pinned SWE cases. Keep strict-format, visible
   answer, reasoning exhaustion, and protocol results separate.
5. **Vision and context after promotion:** run the hashed native multimodal
   image/OCR corpus and durable context depth/retrieval jobs. Include the
   near-limit actual-token point, distinct cache states, and long-tool probes.
6. **Restoration and publication:** verify the promoted serve, router alias,
   every declared harness, 450 W cap, exact image/runtime identity, and
   observability publication. Record the final raw native artifacts and a
   ten-role manifest, then publish the dated finding, run catalog, model
   dossier, and measured hardware page. Preserve external IFBench, GSM8K,
   decode, and prefill figures only as publisher priors unless their exact
   workloads are independently reproduced.

The benchmark order follows the user's request: establish the full known
profile and route first, then measure that profile without a launch-setting
sweep. If a hard gate fails, stop claims for that configuration and retain the
failure rather than calling an incomplete cell a pass.

## SWE worker amendment — 2026-09-25 11:35 UTC

The initial `scout` submission failed before model execution because the
portable spec lacked the profile's required explicit selection. The successful
rerun froze the same five issues used in the earlier local coding lane:
`django__django-11099`, `pytest-dev__pytest-10051`,
`scikit-learn__scikit-learn-10297`, `psf__requests-1142`, and
`sympy__sympy-11618`. It used one isolated macOS worker, default thinking,
mini-SWE-agent `a83fcae82d2a08f0ee0c688f9d137b3566c097f8`, official
SWE-bench `f7bbbb2ccdf479001d6467c9e34af59e44a840f9`, and the pinned
Verified dataset `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`. A second
pre-model attempt exposed a worker-interpreter mismatch; the third submission
used the pinned harness interpreter. No model profile or case selection was
changed in response to a scored result.
