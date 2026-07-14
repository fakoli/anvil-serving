# q36 experiment image

This image packages the actual [`ambud/q36`](https://github.com/ambud/q36)
engine for the Fakoli Dark RTX PRO 6000 experiment. It is intentionally pinned
to q36 commit `458eb018997565445f0ce0a4887ed7cdfeab756b`, CUDA 13.1.2, Ubuntu
24.04, and the source archive checksum recorded in `Dockerfile`.

The q36 Makefile supplies the documented Blackwell-specific compiler settings:
`compute_120a`, `sm_120a`, and `--default-stream per-thread`. The image does not
download model weights. The compose recipe mounts the existing
`vllm-hfcache` Docker volume read-only and selects the exact pinned GGUF
snapshot already downloaded through `anvil-serving models pull`.

Use the managed lifecycle from the repository root:

```powershell
anvil-serving serves up --manifest examples/fakoli-dark/serves.q36.toml q36-pro6000 --confirm
anvil-serving serves status --manifest examples/fakoli-dark/serves.q36.toml q36-pro6000
anvil-serving serves down --manifest examples/fakoli-dark/serves.q36.toml q36-pro6000 --confirm
```

The service conflicts with the production Heavy serve because both own the RTX
PRO 6000. Stop Heavy before starting q36. The endpoint is loopback-only at
`http://127.0.0.1:39040/v1`.

## Reproduce the model and image

Download only the pinned GGUF into the existing ext4-backed data volume. Run
the preview first, then repeat it with `--confirm` in place of `--dry-run`:

```powershell
anvil-serving models pull unsloth/Qwen3.6-35B-A3B-MTP-GGUF `
  --volume vllm-hfcache `
  --revision 5bc3e238d916f48a861bac2f8a1990a0e9b7e98d `
  --include Qwen3.6-35B-A3B-MXFP4_MOE.gguf `
  --no-token `
  --dry-run
```

The model file used for the recorded run had SHA-256
`e1a4925d2ea132576daa9cb980b1102b970d919d896936b7b6e681ef5bc3d3f6`.
Build the image without starting the service:

```powershell
docker compose -f examples/fakoli-dark/docker-compose.q36.yml build q36-pro6000
```

The source archive, source commit, CUDA devel image, and CUDA runtime image all
have immutable pins in `Dockerfile`. The managed `serves up` command builds the
same image before starting it.

Docker Desktop on the measured host exposed both physical GPUs inside a
UUID-selected container. The Compose device reservation remains useful
declarative metadata, but `CUDA_DEVICE_ORDER=PCI_BUS_ID` plus
`CUDA_VISIBLE_DEVICES=1` was the tested runtime isolation boundary. Verify the
physical index before copying this recipe to a different host.

## Configuration

The Compose recipe keeps the source, image, model revision, and GPU binding
pinned while exposing only measured experiment controls:

| Variable | Default | Meaning |
|---|---:|---|
| `Q36_CTX` | `32768` | Allocated context window; tested at 8,192, 32,768, 90,112, and 262,144. |
| `Q36_TEMP` | `0` | Server default temperature; zero keeps greedy requests deterministic. |
| `Q36_MTP` | `0` | Set to `1`, `true`, or `on` to enable q36 self-speculative decode. |
| `Q36_MTP_DEPTH` | `1` | MTP draft depth; the launcher accepts only q36's documented range 1–3. |
| `Q36_GPU_INDEX` | `1` | CUDA-visible physical GPU index after PCI-bus ordering; GPU 1 is the PRO 6000 on this host. |
| `Q36_MODEL_PATH` | pinned snapshot | Exact GGUF path inside the read-only `vllm-hfcache` volume. |
| `Q36_IMAGE` | pinned local tag | Optional replacement image reference. |

The baseline deliberately uses FP16 KV, exposes `<think>` on the OpenAI wire,
and disables state checkpoint caching (`--no-state-cache`). This isolates
engine/context/MTP behavior from KV quantization and host-RAM or disk cache
effects. Those are separate experiment axes, not hidden defaults.

Enable MTP for one managed run without changing the checked-in baseline:

```powershell
$env:Q36_MTP = "1"
$env:Q36_MTP_DEPTH = "1"
anvil-serving serves up --manifest examples/fakoli-dark/serves.q36.toml q36-pro6000 --no-router --confirm
```

Unset the variables and rerun `serves up` to restore the non-MTP baseline.

## Change context and run the native benchmark

Context changes require a recreation. For example:

```powershell
$env:Q36_CTX = "90112"
anvil-serving serves up --manifest examples/fakoli-dark/serves.q36.toml `
  q36-pro6000 --recreate --no-router --confirm
```

Run q36's documented synthetic matrix only while the managed service is down:

```powershell
anvil-serving serves down --manifest examples/fakoli-dark/serves.q36.toml `
  q36-pro6000 --confirm

docker run --rm --gpus all `
  --env CUDA_DEVICE_ORDER=PCI_BUS_ID `
  --env CUDA_VISIBLE_DEVICES=1 `
  --volume vllm-hfcache:/root/.cache/huggingface:ro `
  --entrypoint /opt/nvidia/nvidia_entrypoint.sh `
  q36-engine:458eb018-cuda13.1.2 `
  /opt/q36/q36_bench `
  -m /root/.cache/huggingface/hub/models--unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/5bc3e238d916f48a861bac2f8a1990a0e9b7e98d/Qwen3.6-35B-A3B-MXFP4_MOE.gguf `
  -p 2048,8192,32768,90112 -n 128 -d 0,32768,90112 -r 3
```

Restore `Q36_CTX=32768` and `Q36_MTP=0` through the managed lifecycle after an
experiment. The dated results and caveats are in the
[q36 PRO 6000 finding](../../../docs/findings/2026-07-13-q36-pro6000-container-recipe.md).
