# GPT-OSS Puzzle 88B on one RTX PRO 6000

This is the complete, pinned recipe for serving
`nvidia/gpt-oss-puzzle-88B` on one NVIDIA RTX PRO 6000 Blackwell 96 GB with
Anvil Serving. It covers the custom vLLM build, checkpoint cache, managed Heavy
serve, functional and benchmark gates, router start, and rollback.

Do not replace the exact engine image with stock vLLM. This checkpoint varies
expert counts and attention windows by layer, and its generation config omits
the Harmony `<|call|>` EOS token required by the working tool path.

## Pinned identity

| Component | Required value |
|---|---|
| Model | `nvidia/gpt-oss-puzzle-88B` |
| Model revision | `9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2` |
| Engine repository | [`fakoli/anvil-vllm`](https://github.com/fakoli/anvil-vllm) |
| Engine commit | `485463b3498ed3ffcf0c8fcb52c1670a21be5d82` |
| Fork PR | [`fakoli/anvil-vllm#1`](https://github.com/fakoli/anvil-vllm/pull/1) |
| Local image tag | `anvil-vllm:gpt-oss-puzzle-485463b3498ed3ffcf0c8fcb52c1670a21be5d82` |
| Qualified image ID | `sha256:470f7b7e39c4363696d5a79fd041d6a45253229a9ba1c055d089ddbdc0ed120c` |
| Served name | `gpt-oss-puzzle-88b` |
| Direct endpoint | `http://127.0.0.1:30002/v1` |
| Context | 131,072 tokens |
| Quantization / KV | checkpoint-native MXFP4 with Marlin MoE / FP8 KV |

Commit `485463b3` contains the earlier Puzzle model-support commit `3fbe020f`
and the full generation-config override fix. Related upstream work is
[vLLM #38135](https://github.com/vllm-project/vllm/pull/38135) and
[vLLM #45978](https://github.com/vllm-project/vllm/pull/45978). Do not assume
either upstream change is available in a different vLLM release without
requalifying that exact release.

The same identity is machine-readable in
[`configs/serve-recipes.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml).
The production command lives in
[`examples/fakoli-dark/docker-compose.yml`](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.yml),
and the promotion/rollback transaction lives in
[`examples/fakoli-dark/serves.toml`](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/serves.toml).

## 1. Build the exact vLLM image

Clone the fork and detach at the qualified commit:

```powershell
git clone https://github.com/fakoli/anvil-vllm.git
Set-Location anvil-vllm
git checkout 485463b3498ed3ffcf0c8fcb52c1670a21be5d82
git rev-parse HEAD
```

The final command used for the qualified local build was:

```powershell
docker build --target vllm-openai `
  --build-arg torch_cuda_arch_list=12.0 `
  --build-arg max_jobs=16 `
  --build-arg nvcc_threads=4 `
  --build-arg VLLM_BUILD_COMMIT=485463b3498ed3ffcf0c8fcb52c1670a21be5d82 `
  --build-arg VLLM_BUILD_PIPELINE=local-anvil `
  --tag anvil-vllm:gpt-oss-puzzle-485463b3498ed3ffcf0c8fcb52c1670a21be5d82 `
  --file docker/Dockerfile .
```

This is a full source build and can take substantial time and host resources.
The tested build used Docker Desktop/WSL2, CUDA 13.0.2, driver 610.62, and the
RTX PRO 6000's sm_120 path. Do not retag a different build with the qualified
tag. Give every changed engine commit a new immutable tag and rerun the gates.

Verify the result before configuring Anvil Serving:

```powershell
docker image inspect `
  anvil-vllm:gpt-oss-puzzle-485463b3498ed3ffcf0c8fcb52c1670a21be5d82
```

## 2. Pull the pinned checkpoint into the native cache

From an Anvil Serving checkout, preview and then run the resumable download:

```powershell
anvil-serving models pull nvidia/gpt-oss-puzzle-88B `
  --revision 9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2 `
  --volume vllm-hfcache --dry-run

anvil-serving models pull nvidia/gpt-oss-puzzle-88B `
  --revision 9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2 `
  --volume vllm-hfcache --confirm
```

The default `~/.env` token file may contain `HF_TOKEN`; never place its value in
Compose, TOML, logs, or command arguments. A completed pull only fills the
cache. It does not start a serve or change routing.

## 3. Select the GPU and immutable image

Find the local GPU UUID rather than copying the tested machine's UUID:

```powershell
nvidia-smi --query-gpu=index,name,uuid,memory.total --format=csv
```

Set these in the private Compose environment file used by the router and serve:

```dotenv
HEAVY_GPU_UUID=GPU-REPLACE-WITH-YOUR-RTX-PRO-6000-UUID
HEAVY_PUZZLE_VLLM_IMAGE=anvil-vllm:gpt-oss-puzzle-485463b3498ed3ffcf0c8fcb52c1670a21be5d82
HEAVY_PUZZLE_REVISION=9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2
```

Keep that file outside version control. The repository defaults contain the
qualified image and checkpoint, but the GPU UUID is host-specific.

## 4. Start and identify the Heavy serve

Use the managed lifecycle. Preview first:

```powershell
anvil-serving serves up heavy `
  --manifest examples/fakoli-dark/serves.toml `
  --recreate --no-router --dry-run

anvil-serving serves up heavy `
  --manifest examples/fakoli-dark/serves.toml `
  --recreate --no-router --confirm
```

Then inspect status and the advertised model:

```powershell
anvil-serving serves status --manifest examples/fakoli-dark/serves.toml
Invoke-RestMethod http://127.0.0.1:30002/v1/models
```

Do not continue unless the endpoint advertises `gpt-oss-puzzle-88b` and a
131,072-token maximum.

## 5. Run the required gates

Run functional preflight before capacity or quality measurement:

```powershell
anvil-serving eval preflight `
  --tier heavy --manifest examples/fakoli-dark/serves.toml `
  --checks smoke,json,needle,tools --needle-ctx 120000 --tool-batch 20 `
  --thinking-mode default --reasoning-effort low `
  --visible-answer-tokens 512 --reasoning-headroom-tokens 4096 `
  --reasoning-evidence required --confirm
```

The qualified shape passed coding, JSON, a 99,100-observed-prompt-token
retrieval, and 20/20 tool calls. Stop if any of those gates fail.

For bounded capacity and repeated protocol-v3 quality:

```powershell
anvil-serving eval benchmark capacity `
  --tier heavy --manifest examples/fakoli-dark/serves.toml `
  --requests 40 --concurrency 8 --ctx-tokens 8192 --max-tokens 256 `
  --reasoning-effort low --confirm

anvil-serving eval benchmark quality `
  --tier heavy --manifest examples/fakoli-dark/serves.toml `
  --suite chat,context,tool,session,intelligence `
  --context-targets 32768,128000 --eval-repetitions 3 `
  --eval-min-pass-rate 1.0 --visible-answer-tokens 256 `
  --reasoning-headroom-tokens 2048 --reasoning-effort low --confirm
```

The final image completed 40/40 capacity requests at concurrency eight. The
strict quality run passed context, tools, session recall, and timeout triage,
but unified-diff formatting passed 2/3. Preserve that failure; this recipe is a
serving/tool compatibility result, not proof of better general quality than
Gemma 4.

## 6. Start and verify the router

Preview the exact Compose operation and explicit private env file, then start:

```powershell
anvil-serving router up `
  --compose examples/fakoli-dark/docker-compose.yml `
  --env-file "$HOME/.env" --dry-run

anvil-serving router up `
  --compose examples/fakoli-dark/docker-compose.yml `
  --env-file "$HOME/.env" --confirm

anvil-serving router status --json
anvil-serving router endpoint --no-tailscale --json
anvil-serving router token
```

`router token` reports whether authentication is configured without revealing
the secret. The reference router binds the private Fakoli Dark address; use the
endpoint reported on the target host. Never paste the bearer value into saved
evidence.

## 7. Preserve the required runtime shape

These settings are coupled to the qualified result:

| Requirement | Why it stays |
|---|---|
| exact engine commit/image | stock vLLM does not represent the qualified heterogeneous Puzzle and generation-config path |
| exact checkpoint revision | prevents model/config drift |
| `VLLM_USE_V2_MODEL_RUNNER=0` | retains the tested GPT-OSS reasoning/tool behavior |
| `--moe-backend marlin` | FlashInfer MXFP4/MXFP8 was slower in the local spot comparison |
| `--kv-cache-dtype fp8` | part of the validated 131K memory shape |
| native Harmony template | the model's reasoning and tool protocol depends on it |
| OpenAI tool parser and automatic tool choice | required for the tested tool path |
| `--override-generation-config '{"eos_token_id":[200002,199999,200012]}'` | restores omitted `<|call|>` termination and prevents the reproduced parser 500 |
| 8 sequences / 8,192 batched tokens | tested admission and scheduling bounds |

If upstream vLLM later contains equivalent support, build a new immutable
image, run the same preflight and benchmark gates, and update the recipe only
after comparing the exact artifacts. Do not silently swap the image behind the
existing tag.

## 8. Roll back through the managed transaction

Gemma 4 12B W4A16 is the declared immediate rollback. Preview the complete
transaction before applying it:

```powershell
anvil-serving serves promote gpt-oss-puzzle-88b-heavy `
  --manifest examples/fakoli-dark/serves.toml --rollback --dry-run

anvil-serving serves promote gpt-oss-puzzle-88b-heavy `
  --manifest examples/fakoli-dark/serves.toml --rollback --confirm
```

The transaction restores the paired Gemma serve, router config, and profile;
do not approximate it with manual container edits.

## Evidence

The complete qualification and benchmark narrative is the
[GPT-OSS Puzzle Heavy promotion record](../findings/2026-07-18-gpt-oss-puzzle-heavy-promotion.md).
Its linked JSON artifacts retain exact image identity, long context, tool
regression, interfaces, router state, capacity, quality, and checksums.
