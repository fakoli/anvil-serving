# q36 RTX PRO 6000 reproduction

Run from the `anvil-serving` repository root. Preview guarded mutations before
repeating with `--confirm`.

## Pinned weight download

```powershell
anvil-serving models pull unsloth/Qwen3.6-35B-A3B-MTP-GGUF `
  --volume vllm-hfcache `
  --revision 5bc3e238d916f48a861bac2f8a1990a0e9b7e98d `
  --include Qwen3.6-35B-A3B-MXFP4_MOE.gguf `
  --no-token --dry-run
```

Replace `--dry-run` with `--confirm`. Verify the downloaded file SHA-256 is
`e1a4925d2ea132576daa9cb980b1102b970d919d896936b7b6e681ef5bc3d3f6`.

## Build, start, and smoke

```powershell
docker compose -f examples/fakoli-dark/docker-compose.q36.yml build q36-pro6000
anvil-serving serves up --manifest examples/fakoli-dark/serves.q36.toml `
  q36-pro6000 --no-router --confirm
anvil-serving serves status --manifest examples/fakoli-dark/serves.q36.toml `
  q36-pro6000
```

```powershell
$body = @{
  model = "Qwen3.6-35B-A3B-MXFP4_MOE"
  messages = @(@{role = "user"; content = "What is 17 * 23? Respond with only the numeric answer."})
  temperature = 0
  max_tokens = 64
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:39040/v1/chat/completions `
  -ContentType application/json -Body $body
```

## MTP and context controls

```powershell
$env:Q36_MTP = "1"
$env:Q36_MTP_DEPTH = "1"
$env:Q36_CTX = "32768"
anvil-serving serves up --manifest examples/fakoli-dark/serves.q36.toml `
  q36-pro6000 --recreate --no-router --confirm
```

Recreate at `8192`, `32768`, `90112`, and `262144` for the allocation matrix.
Restore `$env:Q36_CTX="32768"` and `$env:Q36_MTP="0"` afterward.

## Native benchmark

Stop the managed q36 service, then run:

```powershell
docker run --rm --gpus all `
  --env CUDA_DEVICE_ORDER=PCI_BUS_ID --env CUDA_VISIBLE_DEVICES=1 `
  --volume vllm-hfcache:/root/.cache/huggingface:ro `
  --entrypoint /opt/nvidia/nvidia_entrypoint.sh `
  q36-engine:458eb018-cuda13.1.2 `
  /opt/q36/q36_bench `
  -m /root/.cache/huggingface/hub/models--unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/5bc3e238d916f48a861bac2f8a1990a0e9b7e98d/Qwen3.6-35B-A3B-MXFP4_MOE.gguf `
  -p 2048,8192,32768,90112 -n 128 -d 0,32768,90112 -r 3
```

The physical GPU index is host-specific. On the recorded dual-GPU Docker
Desktop host, index 0 was the occupied RTX 5090 and index 1 was the RTX PRO
6000. Confirm the inventory before reuse.
