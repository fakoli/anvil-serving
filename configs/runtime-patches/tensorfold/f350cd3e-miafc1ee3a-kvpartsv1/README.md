# Mia recipe update with Anvil telemetry

This catalog reconstructs TensorFold `f350cd3ea253a042e205746928271f9d8f71a82d`
with all 110 ordered engine patches from Mia's recipe
[`fc1ee3a5381031e2357662b46c2fb44b20190429`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-2x-RTX-PRO-6000-TensorFold/tree/fc1ee3a5381031e2357662b46c2fb44b20190429),
then applies the six Anvil telemetry patches in manifest order.

The retained `f350cd3e-anvilkvpartsv1` catalog remains the prior reconstruction.
Four owned patches are unchanged. The KV ABI and partition patches are rebased
around Mia's new `checkpoint_digest` fields; both checkpoint identity and the
existing telemetry contract are preserved. The author tree, ordered patch
hashes, owned manifest and final tree are checked before compilation.

The source update includes the tool-call guard and rejection diagnostics,
truncated tool-call handling, additional EOS tokens, 32-row verification windows,
KV-pool reply caps, prompt logprobs and build identity. Mia also removed the
two 4,096-row prefill patches, restoring 2,048-row chunks. L2 prefetch and
thinking-off splitting remain optional controls. No weight or drafter update
is part of this catalog.

## Reconstruct and check

Run `python3 test_apply_anvil_patches.py` in this directory to check owned bytes.
Its `--engine-source`, `--recipe-source` and optional `--zig` arguments reconstruct
from local Git objects in a disposable directory, reject source/hash/order drift,
exercise the Dockerfile's pre-compilation checks and run telemetry host tests.
Use Zig 0.17.0. The script does not start containers or perform inference.

The Dockerfile uses the declared retained toolchain/codec image, fetches the exact
engine and recipe revisions, verifies the reconstructed tree, and compiles the
native server and grammar library for SM120 with two build jobs. It preserves
Mia's notices and credits alongside the inherited engine/dependency licenses.
Its build identifier includes the final patched source tree. Use the supported
`anvil-serving host docker-image build` command with a private build declaration;
CPU, RAM, timeout, image digest and deployment paths belong there.

## Deployment gates

Host tests and image construction do not qualify the GPU runtime. Before a
managed switch, verify direct and routed tools, streaming, reasoning, structured
output, image input, concurrency, long prompts, telemetry and cache restart
behavior under the intended recipe. Keep a separate candidate cache volume:
32-row verification windows change the persistent cache layout identity.
The KV-pool cap can shorten a reply; test the effective limit and length finish
behavior rather than assuming the configured context fits every request.

Set `TF_EXTRA_EOS` explicitly in the candidate recipe if adopting Mia's
`<|assistant|>` stop token. Treat `TF_TOOL_CALL_GUARD=1` as a tested behavior
change; keep raw tool-envelope debug logging off. Leave L2 prefetch and
thinking-off splitting disabled unless separately qualified. Preserve the
previous immutable image, recipe and cache for restoration. This source catalog
does not change a running serve, route or promotion decision.
