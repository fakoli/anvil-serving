# GPU marker-trace evidence

This directory contains the sanitized evidence bundle for the dated finding.
The native benchmark artifacts remain authoritative; the files below provide a
consistent campaign-level index.

## Campaign boundary

- **Campaign ID:** `2026-10-26-marker-gpu-trace`
- **Capability:** focused text-serving GPU marker provenance diagnostic
- **Repository revision:** `626f6d8abe8f987a49e09ceab24651684e28ef4f` (clean executable source)
- **Evidence class:** `functional`
- **Outcome:** diagnostic inconclusive; operational restoration passed;
  functional acceptance incomplete
- **Decision labels:** `no-promotion`
- **Promotion boundary:** diagnostic evidence does not authorize promotion

The frozen question is whether each accepted second reasoning-close marker was
budget-forced on its exact verification row or sampled without a budget force.
The experiment preserves model, model revision, launch settings, MTP3, GPU
allocation, TP2/DCP2, configured C4 capacity, request controls, and the prior
split-top-p patch. Synchronized tracing excludes every performance claim.
The final image directly composes the split-top-p and trace patches from the
original immutable runtime image; the prior patched image is retained only as a
reference comparison identity.

## Common campaign artifacts

- [`artifact-manifest.json`](artifact-manifest.json) - role ledger, native
  schemas, file hashes, and explicit gaps
- [`source-registry.json`](source-registry.json) - dated source provenance and
  decision impact
- [`summary.json`](summary.json) - bounded machine-readable outcome and
  decision
- [`friction-log.md`](friction-log.md) - failures, workarounds, ambiguity, and
  recurring manual steps
- [`restoration.json`](restoration.json) - starting/ending state and post-run
  verification, or the reason restoration was not applicable

## Workload and plan

One invocation permits at most two C1 requests, zero retries, a 120-second
request timeout, and a 300-second request window. The first case uses a
64-token reasoning budget, 512-token completion cap, maximum reasoning effort,
temperature 0.6, and top-p 0.95. The second case is allowed only if the first
naturally closes below the boundary and answers correctly. See the frozen
[plan](plan.json), [source freeze](source-freeze.json), [build](build.json),
[candidate attestation](candidate-attestation.json), and independent
[pre-live review](independent-pre-live-review.json).

## Raw run evidence

The actual-CUDA preweight marker selftest and the frozen sampling gate passed
before weights. The only request returned the correct answer and one closing
marker at the 64-token boundary. Both TP ranks recorded a corresponding budget
store, but the trace recorded 76 committed tokens while the native API returned
the first 75, ending at its stop token. The frozen analyzer therefore returned
`unknown`. No second marker reproduced, so the target second-marker question
was not exercised. Exact operational restoration passed. Strict JSON failed
before and after, so functional acceptance remains incomplete; the two smokes
do not establish a quality regression.

Retained live evidence includes the [request](request.json),
[response](response.json), [native probe result](probe-result.json), frozen
[analyzer result](trace-result.json), complete public-safe
[worker records](serving-worker-traces.json), replayable
[worker log](serving-worker-traces.log), [startup receipts](startup-receipts.log), derived
[trace summary](marker-trace-summary.json), independent
[post-probe review](independent-live-review.json), and candidate runtime
inspections [before](runtime-candidate-before.json) and
[after](runtime-candidate-after.json).

## Decision and publication

The dated finding, final manifest, authoritative summary and projection ledger
bind this closed packet. No compact platform copy was requested, so the
publication-summary role is not applicable. The finding preserves the frozen
`unknown` analyzer verdict, distinguishes the observed first-close store from
the unexercised second-close question, and avoids
counterfactual, performance, broader model/KV, or resolution claims. Router
custody is restored, but router configuration byte parity is not claimed
because mounted bytes were not captured before the trial.

Restoration evidence: [restoration](restoration.json),
[closure](closure.json), incumbent runtime [before](runtime-before.json) and
[restored](runtime-restored.json), [router custody](router-restored.json),
[router transition](router-transition-restored.json), [managed mode](mode-restored.json),
[resources](resources-final.json), strict JSON smokes [before](baseline-smoke.json)
and [after](restoration-smoke.json), and the independent
[restoration review](independent-restoration-review.json).

From the repository root, replay the frozen analyzer with:

```bash
python3 configs/runtime-patches/vllm/8244d1c4-marker-trace/analyze.py --logs docs/findings/2026-10-26-marker-gpu-trace-evidence/serving-worker-traces.log --startup-logs docs/findings/2026-10-26-marker-gpu-trace-evidence/startup-receipts.log --response docs/findings/2026-10-26-marker-gpu-trace-evidence/response.json --output /tmp/marker-trace-replay.json
```

The expected exit code is 1 and the expected status is `unknown` because the
trace contains one token after the API-visible stop boundary. The replay hashes
the sanitized response and logs, so its artifact hashes differ from the frozen
native analyzer receipt while its gate outcome is unchanged. The first public
[replay receipt](public-replay-result.json) failed earlier at missing
`prompt_token_ids`; it is retained rather than overwritten. The corrected
[replay receipt](public-replay-result-2.json) reaches the frozen sequence-mismatch
error.
