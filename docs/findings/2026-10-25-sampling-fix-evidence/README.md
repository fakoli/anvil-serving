# Sampling-fix evidence

This focused functional kernel diagnostic stopped before candidate model weights or inference. The [authoritative summary](summary.json) records six completed forced cases, three ordinary exact-parity passes, and failure of the fourth ordinary control. Two forced cases remain unrun. Counts come from the pinned order and failure site, not a final success receipt.

- [Dated finding](../2026-10-25-sampling-fix.md)
- [Artifact manifest](artifact-manifest.json) and [reproducible manifest source](artifact-manifest-source.json)
- [Frozen experiment plan](plan.json), [identity](identity.json), [executable source freeze](source-freeze.json), and [build receipt](build.json)
- [Sanitized kernel receipts and assertion](kernel-log-excerpt.json), [case accounting](kernel-cases.json), and [candidate exit status](candidate-status.json)
- [Sources](source-registry.json), [projection provenance](projection-ledger.json), [friction log](friction-log.md), [restoration](restoration.json), and [native closure](closure.json)

The kernel ran on CUDA device 0, one RTX PRO 6000 Blackwell Max-Q GPU. The managed candidate reserved two GPUs for a planned TP2 serve; distributed serving was never measured. This is not a performance comparison, complete kernel acceptance, model-quality result, or proof that the earlier reasoning-boundary failure is fixed. Ignis held; no promotion.

Public files are allowlisted projections of private managed receipts. Operator topology, host/container identities, GPU UUIDs, process PIDs, command arguments and private paths were omitted. Original-private hashes bind source receipts; the manifest and projection ledger bind the sanitized public bytes. The public bundle was assembled after the attempt; the executable source and private plan were frozen beforehand.

Managed rollback restored exact incumbent identity and authenticated router readiness/readmission. The container generation changed as expected. No generation smoke was sent. Host OOM kills did not increase; swap counters rose by 3 input and 5 output pages. The finalized manifest binds the closed public bundle.
