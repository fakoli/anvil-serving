# Container configurations

These are the exact container contracts behind the most useful retained
benchmark results: pinned model revisions, image identities, cache mounts,
environment controls, engine flags, context, concurrency, and speculative
decoding controls. The full TOML below is included directly from the tracked
recipe files, so this page changes when the source recipe changes.

!!! important "Managed operation and independent reconstruction"

    Anvil Serving operators should use the managed `models recipes` and
    `serves` lifecycle instead of raw Docker. The field mapping on this page is
    also provided for readers using another container runner. In that case,
    you own device selection, cache preparation, health checks, teardown,
    authentication, and resource isolation; reproducing a container does not
    reproduce a benchmark result or authorize a route change.

## Configuration index

| Configuration family | Recorded hardware and runtime | Served envelope | Exact source |
|---|---|---|---|
| GLM-5.3-Flash EXL3 K3 + DFlash2 K5 and matched no-spec control | 2× RTX PRO 6000 Blackwell Max-Q, WSL2, vLLM-derived local image, TP=2/DCP=2 | 524,288 tokens, max 16 sequences, up to 16 images, no video | [Open pair](#glm53-524k) |
| GLM-5.3-Flash ormandj W4A16/NVFP4 SGLang adaptive MTP and control | 2× RTX PRO 6000 Blackwell Max-Q, WSL2, digest-pinned SGLang rc14, TP=2 | current 393,216-token C1 lane; 245,760-token fallback, 131K A/B, and 499K negative controls retained | [Open family](#glm53-sglang-sm120) |
| Qwen3.8 Flash Next RadixArk NVFP4 MTP3 and matched no-spec control | 2× RTX PRO 6000 Blackwell Max-Q, WSL2, digest-pinned SGLang with hash-gated SM120 patching, TP=2 | 262,144 tokens, one running request | [Open pair](#qwen38-flash-next) |
| Qwen3.8 27B official FP8 SGLang MTP3 multimodal and no-spec campaign control | 1× RTX PRO 6000 Blackwell Max-Q, WSL2, digest-pinned SGLang, TP=1 | 393,216 tokens, one running request, CPU media transport | [Open pair](#qwen38-27b-official-fp8) |
| Qwen3.8 27B RadixArk NVFP4 multimodal | 1× RTX 5090, digest-pinned SGLang, TP=1 | 131,072 tokens, one request, up to eight images or two videos | [Open configuration](#qwen38-27b-radixark-rtx5090) |
| GLM-5.3-Flash EXL3 K3 + DFlash2 K5, same-model rollback | 2× RTX PRO 6000 Blackwell Max-Q, WSL2, Purtell-derived vLLM image, TP=2/DCP=2 | 1,048,576 tokens, max 16 sequences, image/OCR | [Open inventory](#additional-retained-families) |
| DeepSeek V4 Flash 0731 Infernal Invocation r18 DSpark K5 and no-spec control | 2× RTX PRO 6000 Blackwell Max-Q, WSL2, custom r18 vLLM image, TP=2/DCP=1 | 1,048,576 tokens, max 8 sequences, text | [Open inventory](#additional-retained-families) |

Decision terms such as `current`, `rollback`, and `no-promotion` describe the
latest published evidence decision, not an operator's live deployment.

## Translate the recipe to a container runtime

Anvil Serving's recipe loader turns these fields into a container invocation.
An independent runner can preserve the same contract with this mapping:

| Recipe field | Container-runtime meaning |
|---|---|
| `recipe.download.repo` + `revision` | Fetch this exact model snapshot. A branch name or latest revision is not equivalent. |
| `recipe.download.volume` | Named model-cache volume, mounted at `/root/.cache/huggingface` by Anvil Serving. Populate the exact snapshot before an offline load when `require_complete_cache = true`. |
| `recipe.download.require_complete_cache` | When `true`, Anvil Serving also injects `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. An independent invocation must add both values even when they are absent from `recipe.serve.env`. |
| `recipe.hardware` | Hardware prerequisite and topology assumption, not a portable device selector. Select local devices that satisfy the GPU count, architecture, VRAM, and TP/DCP shape; never copy a host UUID from someone else's machine. |
| `recipe.serve.image` | Exact container image. Preserve `@sha256:` when present. A local image name requires the recorded build assets and a digest check before it is equivalent. |
| `entrypoint` | Replace the image entrypoint with the listed executable and arguments. Some recipes apply guarded source changes before launching the server. |
| `model_flag`, `model_path`, `model_env` | Supply the immutable in-container snapshot either as a command flag, positional argument, or environment value, exactly as declared. |
| `named_volumes` | Additional `type=volume` mounts for generated kernels, runtime caches, or temporary state. These do not replace the separate Hugging Face model-cache mount. |
| `env` | Container environment variables. Preserve allocator, NCCL, WSL2, feature-transport, parser, and cache controls; they are part of the measured configuration. |
| `flags` | Server arguments after the image entrypoint. Preserve quoting for JSON-valued flags and keep context, KV dtype, concurrency, batching, parsers, and speculation together. |
| `port` | Container and loopback host port. Anvil Serving publishes it on `127.0.0.1`; do not turn an internal `--host 0.0.0.0` into an unauthenticated LAN or internet bind. |
| `ipc`, `shm_size`, `ulimits` | Container IPC, shared-memory, and process-limit settings. Omitting them can change startup or long-context behavior. |
| `status`, `note`, `fit`, `sources` | Evidence and provenance metadata. They do not become container arguments, but they define the known limits of reuse. |

### Assemble the equivalent container without Anvil Serving

You need only Docker, a TOML reader, and access to the declared model and image
artifacts. Work from a local copy of one full snippet below:

1. Pull or build `recipe.serve.image` and verify its digest. Stop if a local or
   custom image cannot be recreated from the linked assets.
2. Create `recipe.download.volume`, populate the exact
   `recipe.download.repo@revision` snapshot, and mount it at
   `/root/.cache/huggingface`. When `require_complete_cache = true`, also add
   `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. Create every
   `named_volumes` source separately.
3. Select local GPUs matching `recipe.hardware` and the declared TP/DCP shape.
   Substitute your own `<GPU_UUID_A>` and optional `<GPU_UUID_B>`; no public
   recipe supplies them for you.
4. Build the container argument vector in this order: runtime controls,
   environment, cache mounts, named mounts, loopback port, IPC/shared-memory/
   ulimits, entrypoint, image, model selector, then engine flags.
5. Inspect that argument vector before launch. Confirm the model revision,
   image digest, mounts, GPU count, loopback publication, and served name, then
   start it with no unrelated GPU workload sharing the declared devices.

The shell-neutral layout is:

```text
docker run --name <LOCAL_CONTAINER_NAME>
  --gpus <GPU_REQUEST>
  -e CUDA_VISIBLE_DEVICES=<GPU_UUID_A>[,<GPU_UUID_B>]
  -e <EACH recipe.serve.env ENTRY>
  [-e <recipe.serve.model_env>=<recipe.model> WHEN model_env is present]
  [-e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 WHEN require_complete_cache=true]
  -v <recipe.download.volume>:/root/.cache/huggingface
  --mount type=volume,source=<NAMED_VOLUME>,target=<ABSOLUTE_TARGET>[,readonly]
  -p 127.0.0.1:<recipe.serve.port>:<recipe.serve.port>
  --ipc <recipe.serve.ipc>
  --shm-size <recipe.serve.shm_size>
  --ulimit <EACH recipe.serve.ulimits ENTRY>
  --entrypoint <recipe.serve.entrypoint[0]>
  <recipe.serve.image>
  <REMAINING ENTRYPOINT ITEMS>
  <recipe.serve.model_flag> <recipe.serve.model_path>
  <TOKENIZED recipe.serve.flags ENTRIES>
```

For one device, `<GPU_REQUEST>` is the argument
`device=<GPU_UUID_A>`. For two devices, Docker must receive the literal
argument `"device=<GPU_UUID_A>,<GPU_UUID_B>"`, including the inner double
quotes, because its `--gpus` parser treats commas as capability-request
separators. Escape or quote that literal for the shell or container API you
use; shell syntax is not portable across PowerShell, Bash, and structured argv
calls.

Omit only fields absent from that recipe. The explicit device selection also
becomes `CUDA_VISIBLE_DEVICES`, unless the recipe opts into a validated
container-relative numeric index pin. When `model_env` is present, pass the
model through that environment variable and omit the model argument.
Without `model_flag` or `model_env`, append `model_path` (or `recipe.model`) as
the positional model argument. Preserve each JSON-valued flag as one argument.
The recipes with embedded multi-line entrypoints are intentionally not emitted
as copy-paste commands here: PowerShell, Bash, Compose, and container APIs quote
those values differently. The checked-in TOML plus this ordered mapping is the
complete, shell-independent reconstruction contract.

The model cache and each named volume must exist independently. Volume names
are convenient labels, not downloadable artifacts. Generated kernels, JIT
caches, or temporary volumes may start empty only when the image can recreate
their contents; the pinned model snapshot may not.

## Reuse boundaries

!!! warning "Hardware, images, and licenses are part of the result"

    These configurations were measured on `sm_120` Blackwell GPUs. The
    dual-card recipes assume separate PCIe devices without NVLink and the
    recorded WSL2/NCCL controls; TP=2 is sharded capacity, not unified memory.
    Another GPU, native Linux, another CUDA/driver build, or a different GPU
    count is a new configuration requiring fresh evidence.

    The rollback GLM 524K image is a local Anvil-derived build. Recreate it from
    the tracked
    [Dockerfile](https://github.com/fakoli/anvil-serving/blob/main/configs/runtime-patches/vllm/487ecf187-xgrammar-spec-reasoning-end/Dockerfile)
    and
    [xgrammar patch](https://github.com/fakoli/anvil-serving/blob/main/configs/runtime-patches/vllm/487ecf187-xgrammar-spec-reasoning-end/xgrammar-spec-reasoning-end.patch),
    then verify the recipe's image digest. The Qwen3.8 Flash Next pair uses an
    upstream image but performs exact, hash-gated source edits in its recorded
    entrypoint. The DeepSeek r18 image is a community-derived runtime whose
    launcher scripts are embedded in that image. Those recipes are not
    standalone when the exact image or required build inputs are unavailable.

    DFlash2 is licensed CC BY-NC-ND 4.0 and remains limited to evaluation and
    noncommercial use without separate permission. Model weights, images, and
    other dependencies retain their own upstream licenses; this recipe index
    grants no additional rights.

The public recipes intentionally omit physical GPU UUIDs, private host
addresses, credentials, and active route assignments. Supply those locally;
do not add them to a public reproduction report.

## GLM-5.3-Flash 524K DFlash2 K5 and control { #glm53-524k }

The selected arm and its no-speculation control pin target revision
`319d66a8b53092b491f698440ecea781e4ddd4e4`, the same local image digest,
TP=2/DCP=2, FP8 DS-MLA target KV, 524,288-token context, batch-2,048 scheduler,
max-seq-16 admission, WSL2/NCCL controls, parsers, and image/OCR envelope. The
candidate additionally loads DFlash2 revision
`dc77ff1c99eeb2df044ee3d4f0094eb033fee410` with five draft tokens and BF16
draft KV. Both use `vllm-hfcache` for model snapshots and
`glm53-purtell-k3-dflash2-vllm-cache` for `/root/.cache`.

[Result and evidence](../findings/2026-08-31-glm53-xgrammar-524k-qualification.md)
· [Model dossier](models/glm53-flash.md)

??? example "DFlash2 K5 — full tracked recipe"

    ```toml
    --8<-- "configs/glm53-flash-purtell-k3-dflash2-k5-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml"
    ```

??? example "No speculation — full tracked control"

    ```toml
    --8<-- "configs/glm53-flash-purtell-k3-nospec-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml"
    ```

## GLM-5.3-Flash SGLang SM120 adaptive MTP and control { #glm53-sglang-sm120 }

This family pins ormandj checkpoint revision
`c3cbb9891b67c741bcbf6b176dd7af9265b069db` and rc14 image digest
`0c0637959c3931829f05154087bbefd2c50003fb9b2010200ce0ec82f4d71a53`.
The qualified WSL2 translation uses TP=2, FP8 KV, explicit thinking control,
image/OCR, and hash-gated source/template patches. The 131K matched pair
isolates adaptive EAGLE. The human-approved 393,216/C1 adaptive profile is the
published current contract after an explicit model-only reserve waiver; the
245,760/C1 profile remains the conservative verified fallback. The 499K/C4
profile is rejected and the 499K/C1 profile remains unverified.

[Qualification evidence](../findings/2026-09-02-glm53-sglang-sm120-qualification.md)
· [Promotion](../findings/2026-09-02-glm53-sglang-sm120-393k-promotion.md)
· [Model dossier](models/glm53-flash.md)

??? example "Adaptive MTP, 393,216 tokens — current tracked recipe"

    ```toml
    --8<-- "configs/glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp-recipe.toml"
    ```

??? example "Adaptive MTP, 245,760 tokens — conservative fallback"

    ```toml
    --8<-- "configs/glm53-flash-ormandj-sglang-sm120-tp2-240k-c1-adaptive-mtp-recipe.toml"
    ```

??? example "Matched no-speculation 131K control"

    ```toml
    --8<-- "configs/glm53-flash-ormandj-sglang-sm120-tp2-131k-nospec-recipe.toml"
    ```

Additional retained recipes:
[131K adaptive](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-131k-adaptive-mtp-recipe.toml),
[499K/C1 unverified](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-499k-c1-adaptive-mtp-recipe.toml), and
[499K/C4 rejected](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-499k-adaptive-mtp-recipe.toml).

## Qwen3.8 Flash Next MTP3 and control { #qwen38-flash-next }

This otherwise-matched pair pins RadixArk revision
`7b719225242aacd3dbd3f9407468c2ee9a9d2594`, SGLang image digest
`59f06adce6f91401adf443bd168d45fdb2044d77671fd591c7c57a29d851cbae`,
TP=2, BF16 KV/recurrent state, 262,144-token context, concurrency one, 0.80
static memory, CPU-offload exclusions, and the same guarded SM120 QSA fast-path
entrypoint. The MTP arm adds NEXTN steps/top-k/draft tokens `3/1/4`.

These files declare `vllm-hfcache` for the model snapshot and no additional
named runtime volume. The entrypoint source hashes are part of the
configuration: a newer SGLang file that fails either hash check is not an
equivalent run.

[Performance and promotion record](../findings/2026-08-26-qwen38-flash-next-qsa-fast-mtp3-promotion.md)
· [Vision qualification](../findings/2026-08-26-qwen38-flash-next-vision-promotion.md)
· [Model dossier](models/qwen38-flash-next.md)

??? example "MTP3 — full tracked recipe"

    ```toml
    --8<-- "configs/qwen38-flash-next-radixark-nvfp4-sglang-sm120-qsa-fast-tp2-262k-mtp3-recipe.toml"
    ```

??? example "No speculation — full tracked control"

    ```toml
    --8<-- "configs/qwen38-flash-next-radixark-nvfp4-sglang-sm120-qsa-fast-tp2-262k-nospec-recipe.toml"
    ```

## Qwen3.8 27B official FP8 SGLang { #qwen38-27b-official-fp8 }

The reusable multimodal profile pins official model revision
`017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`, SGLang digest
`506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`,
TP=1, 393,216-token context, concurrency one, FP8 E4M3 KV, FlashInfer
attention, 2K chunks, disabled prefix cache, thinking disabled by default, and
the `qwen38-fp8-sglang-cache` runtime volume. The multimodal profile uses CPU
feature transport, five GDN states, and EAGLE `3/1/4`; the no-spec campaign
control is language-only with one GDN state.

Because media transport, modality, and GDN capacity also differ, these two
files are not by themselves a one-variable speculation A/B. Use the dated
finding for the exact matched measurement arms rather than deriving a speedup
from these reusable endpoint definitions.

[MTP and multimodal qualification](../findings/2026-08-15-qwen38-27b-sglang-mtp-multimodal-qualification.md)
· [Video expansion](../findings/2026-08-16-qwen38-27b-video-router.md)
· [Model dossier](models/qwen38-27b.md)

??? example "MTP3 multimodal with CPU feature transport — full tracked recipe"

    ```toml
    --8<-- "configs/qwen38-27b-official-fp8-sglang-tp1-393k-mtp3-mm-cpu-recipe.toml"
    ```

??? example "No-speculation text control — full tracked recipe"

    ```toml
    --8<-- "configs/qwen38-27b-official-fp8-sglang-tp1-393k-control-recipe.toml"
    ```

## Qwen3.8 27B RadixArk RTX 5090 128K multimodal { #qwen38-27b-radixark-rtx5090 }

This direct-only profile pins RadixArk revision
`554ebba9b5f1b79dc11246341960360e6ef05ef4` and SGLang digest
`506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`
for one 32 GB RTX 5090. It uses TP=1, 131,072-token context, concurrency one,
FP8 E4M3 KV, CPU media transport, no speculation, eight-image/two-video
limits, `vllm-hfcache`, and the
`qwen38-radixark-nvfp4-sglang-cache` runtime volume.

The image reference records both a repository tag and digest. Preserve the
digest; the tag alone is mutable, and availability of that repository/tag is
external to this project. This recipe does not include router admission or a
promotion contract.

[128K qualification](../findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md)
· [Model dossier](models/qwen38-27b.md)

??? example "Full tracked recipe"

    ```toml
    --8<-- "configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm-recipe.toml"
    ```

## Additional retained families { #additional-retained-families }

These remain useful exact sources, but expanding every TOML file here would
make the quick-reproduction path harder to scan.

| Family | What must travel with it | Recipe sources | Evidence |
|---|---|---|---|
| GLM-5.3-Flash EXL3 K3 + DFlash2 K5, 1M same-model rollback | Purtell-derived digest-pinned image; target and DFlash2 snapshots; `vllm-hfcache`; `glm53-purtell-k3-dflash2-vllm-cache`; WSL2 TP=2/DCP=2 controls; DFlash2 noncommercial license boundary | [K5 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml) | [1M optimization](../findings/2026-08-30-glm53-k3-dflash2-1m-optimization.md) |
| DeepSeek V4 Flash 0731 Infernal Invocation r18, 1M | Custom digest-pinned `voipmonitor/vllm` image with embedded wrapper scripts; exact model snapshot; `deepseek-v4-0731-r16-hfcache`; separate JIT and temporary named volumes; WSL2 TP=2/DCP=1; max-seq-8/batch-4,096; zero offload | [DSpark K5](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r18-b12x-dspark5-maxseq8-batch4096-1m-recipe.toml) · [no-spec control](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r18-b12x-nospec-maxseq8-batch4096-1m-recipe.toml) | [r18 promotion-era evidence](../findings/2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion.md) |

## What this page does not reconstruct

A container recipe does not contain the benchmark corpus, request order,
warm/cold state, client clock, measurement instrument, router admission, or
promotion decision. Follow the linked finding and raw artifacts before
comparing a reproduced number. A healthy response proves neither capacity nor
quality, and a configuration copied to different hardware is a new candidate,
not a reproduced result.
