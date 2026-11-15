# TensorFold partitioned KV telemetry v1

Pinned upstream [ashhart/TensorFold](https://github.com/ashhart/TensorFold),
enginef350cd3ea253a042e205746928271f9d8f71a82d and author recipe
0a5297c912e118490b8bb3734d9e20107df7f6f2. Preserve the101 author patches
and the five unchanged Anvil telemetry patches. This series appends the owned
`anvil-kv-partitions.patch`; `manifest.toml` owns order and raw hashes.

The additive v1 extension reads both leader-owned page pools on the engine
thread, never sums TP followers, preserves promises and includes transient
twins as an allocated subset. Unsupported/incomplete/overflowing censuses
remain unavailable. Full-context bounds still require one partition and every
admission-policy input; this patch does not qualify partitioned admission.
See [CONTRACT.md](CONTRACT.md). No scheduling, kernels or model changes.

Private image declarations supply the immutable author/toolchain base image,
output tag and limits. The managed command is
`anvil-serving host docker-image build tensorfold-kv-partitions-v1 --confirm`.
Build checks all101 author hashes, exact preimage/final source trees and every
owned patch, then runs CPU helper, renderer, production-publication and real
sharded-pool tests before compiling native/grammar at the original flags.
No upstream source, model assets or deployment identity is vendored.

Run the portable artifact check:

```sh
python3 -B configs/runtime-patches/tensorfold/f350cd3e-anvilkvpartsv1/test_apply_anvil_patches.py
```

Optional `--engine-source`, `--recipe-source` and `--zig` reconstruct the
source from the exact pins and run the CPU suites. Original inputs remain
untouched. Live sharding off/on qualification and reviewed recipe rollout are
separate gates. Keep the historical unpartitioned catalog and its evidence.
