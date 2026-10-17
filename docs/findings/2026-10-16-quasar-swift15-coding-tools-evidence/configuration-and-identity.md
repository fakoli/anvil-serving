# Configuration and identity

Measured hardware: one RTX 5090, 32,607 MiB, direct local endpoint, one candidate
at a time. The cache-transfer host performed no inference and is excluded.

| Identity | Pinned value |
| --- | --- |
| QUASAR repository | `wallawalla47/Qwen3.8-27B-Quasar-NVFP4-NInferV3` |
| QUASAR revision | `fd3d06cea9df58a54d5051aac9545a4a01a922db` |
| Swift15 repository | `kaushikvira/Qwen3.8-27B-swift15-nvfp4full-dflash2-NInfer-v3` |
| Swift15 revision | `ff891a1130adfcf46bf745e06f0fa3dcde2e8c82` |
| NInfer image | `anvil-ninfer@sha256:59df48dd99f0d177b73de57ba88eb6457ed7e4b0c5771c45e8976adcd9f5ef67` |
| NInfer source | `bace20dc70249eed6402b66d4852c6c3f9612905` |
| Live collection harness | Package **1.5.0**, Python **3.14.8**; installed source revision unavailable |
| Live source fingerprint | `8842145699f43e59e2649d27e662c09125e1c75f88dff27196cbc232d4677720` |
| Helper/publication worktree | Package **1.2.1**, base `2b99e3353eb2e5a2813195af85050b1da0754332`, dirty |

The 1.2.1 worktree revision and its tests are not asserted as provenance for the
installed 1.5.0 collection. [Retained harness source-file hashes](native/harness-provenance.json).

[QUASAR recipe](recipes/quasar-text32k.toml) and
[Swift15 recipe](recipes/swift15-text32k.toml) reconstruct the full managed inputs.
Only ports and the cache-volume label are synthetic; [lineage](redaction-lineage.json)
records every change. As-run prequalification labels were preserved. Binary and
artifact SHA checks, model paths, flags and limits are unchanged.

Both profiles used vision off, 32,768-token per-request context, configured C4,
request C1, shared KV 131,072, K8V4, DFlash2 K7, host KV 8,192 MiB, eight host
state slots, 40 GiB RAM limits without extra swap, and an 8 GiB host reserve.
Coding requests matched medium reasoning, temperature 0.6, top-p 0.95, and the
4,096 + 8,192 combined completion cap. No fixed seed was sent; cache warmth and
background activity were not fully controlled. QUASAR's artifact precision differs
from Swift15; it is not wholly NVFP4 or lossless.
