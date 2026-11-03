# Configuration reconstruction and privacy boundary

Measured hardware: one NVIDIA GeForce RTX 5090, 32 GB class, on Windows with
Docker Desktop's Linux VM. Workloads ran through direct online endpoints,
one active candidate at a time. No second GPU is measured or inferred.
Managed Anvil recipes controlled every candidate; this publication starts no serve.

The campaign launcher was Anvil Serving 1.5.0 plus three local fixes for served
identity, explicit tokenizer protocols and compact context-summary packaging.
Those fixes are documented campaign provenance; this documentation change does
not deploy them or claim they are the current product's implementation.

Exact model/variant identifiers, immutable weight/draft/projector fingerprints,
container images, container IDs and private recipe locations are withheld.
The following is a **sanitized configuration description, not an executable or
independently reproducible recipe**. The missing public identity bounds every claim.

## Ignis configurations

Ignis is the serving runtime, not the model name. The tested private candidate
used Ignis 0.8.0 with a private model artifact. Its compression format and auxiliary tensors remain private.
The pinned release/source contract is in [sources](source-registry.json).

| Setting | Matched 32K budget pair | Large vision profile |
|---|---|---|
| Context, configured | 32,768 tokens | 262,144 tokens |
| Shared KV pool, configured | 65,536 token slots | 524,288 token slots |
| GPU budget, configured | 24 GiB | 24 GiB |
| Decode lanes, configured | 4 | 4 |
| Tested primary request load | Sequential C1 | Sequential C1 |
| Speculation | DFlash2, 7 drafts | DFlash2, 7 drafts |
| Thinking budget | 1,024 or 4,096 | 4,096 |
| Loop guard | off | off |
| Implicit model switching | disabled | disabled |
| Vision | disabled | enabled |
| Default total output cap | 12,288 tokens | 12,288 tokens |
| Host RAM cap | 16 GiB | 16 GiB |
| Additional swap | none | none |
| Host reserve | 8 GiB | 8 GiB |

The two 32K recipes changed only the reasoning-budget control plus descriptive
labels. They were separate loads. The larger recipe changed context, KV and
vision together. Neither experiment isolates a speculative speedup.

Coding and tool-use used enabled reasoning; retrieval, images and short-output
capacity used disabled reasoning. These are separate measurement policies.
The original disabled scout and earlier enabled scout had different controls
and are not matched controls for every later recipe.

Four lanes do not prove four full windows. The larger shared pool represents
524,288 / 262,144 = **two nominal sequence equivalents before other reserves**.
This is arithmetic on configured slots, not measured simultaneous capacity.
KV numeric format was not separately verified in the retained public inputs;
do not infer it from a model's weight format.

## Other candidate and reference profiles

| Public label | Configuration and boundary |
|---|---|
| QAT text candidate | NInfer, NVFP 4 weights, 163,840 context/KV slots, K 8/V 4 KV, C1, speculation disabled, vision disabled, reasoning disabled; 24 GiB RAM cap. Paired vision-enabled launch failed. |
| Coding candidate | NInfer, mixed-precision weights, 131,072 configured context, DFlash2, text-only, C1. Fresh coding recheck is a new process; earlier same-recipe context/tool/stability evidence stays separate. |
| Restored reference | NInfer, NVFP 4/mixed-precision weights, INT 8 KV, 163,840 configured context/KV, MTP 3, C1, vision enabled, launch-time reasoning disabled. Existing uncapped host-memory policy preserved. |
| 32K capacity reference/control | Separate 32K reference and QAT no-speculation profiles. These are not the restored 160K reference. |

All coding profiles used the same seven tasks, three repetitions, temperature 0
and 12,288 total completion cap. The 4,096 visible-answer plus 8,192 reasoning
headroom fields are allocation metadata, not independently enforced channel caps.
Prompt prefix reuse was observed; these are not cold-only measurements.
