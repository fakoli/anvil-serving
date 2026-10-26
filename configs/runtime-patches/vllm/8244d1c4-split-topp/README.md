# Pinned split top-p regression candidate

This inert build context backports only the production change from
[vLLM PR 58231](https://github.com/vllm-project/vllm/pull/58231), commit
[`0a1276e5e3d9000488e7590cc0957edd709f2152`](https://github.com/vllm-project/vllm/commit/0a1276e5e3d9000488e7590cc0957edd709f2152),
onto vLLM `8244d1c44348ae8a5bbfc9f1b607021652283ed4`, then adds an independent,
default-off dispatch receipt. Upstream was observed merged on 2026-10-25.
`split-topp.patch` preserves the exact upstream production hunks (their line
numbers apply with a one-line offset); the larger PR's other changes are excluded.

The single-stage image derives from
`ghcr.io/local-inference-lab/vllm:release-10152026@sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29`.
Build steps have no network access and install no dependencies. Every copied
file is hash checked before execution. The base must already provide `python3`,
`patch`, and `sha256sum`; mismatched source or unavailable tools fail the build.
The image retains its inherited entrypoint and command.

| Source state | SHA-256 |
| --- | --- |
| Original | `0b5fc337058e22bc28a8530ccec904d6ccd89b9548b02955c657ac429b1669d2` |
| Upstream fix only | `af4e82ffd0fa8fc3b2ab7493c589d0c64c336d7b135e082575329670cc610c90` |
| Fix plus trace | `2b5305c406d8495e515f048ce0322c7ae5946a49e5f27bd5bbbf30a1e1fea0e6` |

Offline verification from this directory uses the retained original file:

```sh
python3 verify_patch.py --source /path/to/pinned/vllm/v1/sample/ops/topk_topp_triton.py
```

The check verifies both patch stages and compiles the original, replacement, and
helper files without importing torch or accessing a GPU. It is not a kernel test.

## Managed candidate startup gate

Select `['python3', '/opt/anvil-sampling/launch.py', 'vllm', 'serve']` as the
candidate recipe's entrypoint; retain the original model path and serve arguments.
The wrapper runs `python3 /opt/anvil-sampling/kernel_regression.py` in a child
process with a 180-second timeout, then execs `/usr/local/bin/lil-entrypoint` with
the forwarded arguments only after success. Failure or timeout prevents weight
loading. The child exits before serving and releases its CUDA context.
Use managed recipe load/status/logs/unload for the authorized trial.

The regression imports the exact original source saved at
`/opt/anvil-sampling/baseline.py` and the installed patched module. On CUDA device
0, it checks eight forced-logit cases: batches 4/64, logits `1e9`/`1e9 / 0.6`,
top-p 0.95, vocabulary/token pairs 131072/13 and 154856/154842. Each must show
original all-masked rows, NaN probabilities, and argmax 0; patched rows must retain
exactly the forced token with finite probabilities. Four seeded ordinary-logit
controls require exact original/patched output equality. Peak PyTorch tensor
allocation must remain below 1 GiB; this excludes CUDA context/compiler overhead.
This verifies kernel behavior, not model quality or the end-to-end sampling path.

## Optional dispatch receipts

Set only `ANVIL_SAMPLING_TRACE=1` in the candidate recipe to enable tracing.
The patch checks at most 256 noncapturing split calls and emits at most eight
`ANVIL_SAMPLING_TRACE` lines per process. Each check follows the split stats,
step, and mask dispatches and CUDA synchronization. A receipt requires a
post-mask row maximum of at least `1e8`, matching the synthetic forced-logit
condition. Ordinary startup calls consume checks but cannot consume events.
Fields contain only numeric metadata: event/check numbers, PID, row count,
vocabulary size, top-k presence, split count, step rounds, completion flag,
forced-row count, and minimum/maximum finite-token counts among those rows.
`token_154842_finite_rows` counts forced rows retaining that token, or is -1
when the vocabulary is too small. No logit values, generated tokens, prompts,
arguments, or environment contents are recorded. The regression subprocess and
serving worker have separate check/event budgets.

Receipts cover the actual Python dispatch path for CUDA batches up to 64.
They are not profiler traces: they cannot establish per-row work when a kernel
returns early, map a launch to a user request, or measure latency. CUDA graph
capture is skipped to avoid illegal synchronization, and graph replay does not
emit Python receipts. Startup calls can exhaust the 256-check window; absence
of later receipts does not prove absence of kernel execution. An all-masked row
does not meet the post-mask condition and cannot emit a receipt. This is candidate
forcing evidence, not independent proof of why a logit was forced. Enabled tracing
synchronizes and reduces tensors for at most 256 checks, stopping after eight
events, and must stay off for performance comparisons.

No image build, GPU test, serving change, qualification, or promotion is implied
by the offline artifact check.
