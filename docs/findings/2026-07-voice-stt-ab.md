# Voice STT A/B: parakeet.cpp vs vLLM-served Whisper

> **STATUS: EXECUTED on fakoli-dark, 2026-07-05.** This records anvil task
> T007 live evidence from sm_120 hardware using `scripts/voice/preflight_stt.py`.

Related: `docs/findings/2026-07-04-hf-speech-to-speech-review.md` s8
(sm_120 component guidance) and `scripts/voice/preflight_stt.py`.

## Hardware and serves

`nvidia-smi --query-gpu=index,uuid,name,memory.free,memory.used --format=csv,noheader`
reported:

| GPU | UUID | device | free MiB before vLLM probe | used MiB before vLLM probe |
|---|---|---|---:|---:|
| 0 | `GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1` | NVIDIA GeForce RTX 5090 | 3454 | 29144 |
| 1 | `GPU-d0f446cf-1771-414c-e116-a39138798a8c` | NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition | 6843 | 90596 |

Live serve inventory during the run:

| container | image | endpoint | role |
|---|---|---|---|
| `parakeet-stt` | `ghcr.io/mudler/parakeet.cpp-server:latest-cuda` | `http://127.0.0.1:30010/v1` | parakeet.cpp STT |
| `anvil-stt-vllm-whisper-tiny-eager-test` | `vllm/vllm-openai:nightly` | `http://127.0.0.1:30015/v1` | disposable vLLM Whisper STT probe |

The tracked JSON reports also include a `gpu` field collected with
`nvidia-smi`, showing both visible GPUs at `sm_120`.

The vLLM probe used `openai/whisper-tiny` rather than a production-size
Whisper or Qwen3-ASR model because both live GPUs were already heavily
allocated to the local LLM tiers. This still exercises the same vLLM
`/v1/audio/transcriptions` serving path required by T007. Production ASR
follow-up should repeat this with `openai/whisper-large-v3-turbo`,
`openai/whisper-large-v3`, or Qwen3-ASR on a GPU with reserved capacity.

## Startup notes

parakeet.cpp was already running from the earlier live voice session:

```text
docker ps: parakeet-stt ghcr.io/mudler/parakeet.cpp-server:latest-cuda 127.0.0.1:30010->8080/tcp
```

The vLLM Whisper probe required:

- `HF_TOKEN` from the operator environment, passed as an environment variable only.
- `librosa` and `soundfile` installed inside the disposable container before
  starting vLLM. The vLLM speech-to-text docs require the audio extras for
  transcription support.
- `--enforce-eager`; without it the first disposable vLLM container stalled
  during compile/CUDA graph startup and did not become healthy.
- `--init`; the first disposable container was hard to reap without an init
  process after the stalled startup path.
- Low `--gpu-memory-utilization` because the RTX 5090 had only about 3.4 GiB
  free while the main LLM tier was running.

Successful vLLM serve log evidence included:

```text
Supported tasks: ['transcription']
Route: /v1/audio/transcriptions, Methods: POST
GET /health HTTP/1.1" 200 OK
```

The tracked JSON reports include per-candidate readiness evidence:
`health.status: 200`, `container.status: running`, and the container image for
both `parakeet-stt` and `anvil-stt-vllm-whisper-tiny-eager-test`.

No run emitted `sm_120 not compatible`, `no kernel image`, or
`cuBLAS_STATUS_NOT_SUPPORTED`.

## Measurement command

Both candidates were measured with the same real WAV sample from the reviewed
Claude session:

```powershell
python scripts/voice/preflight_stt.py --no-bring-up `
  --candidate name=parakeet.cpp,base_url=http://127.0.0.1:30010/v1,model=tdt_ctc-110m,container=parakeet-stt,stream=false `
  --candidate name=vllm-whisper-tiny,base_url=http://127.0.0.1:30015/v1,model=whisper-tiny,container=anvil-stt-vllm-whisper-tiny-eager-test,stream=false `
  --sample 'C:\Users\sdoum\AppData\Local\Temp\claude\C--Users-sdoum-ai-code-anvil-serving\21eba8ab-885c-4f97-acef-864745e5c375\scratchpad\test_stt.wav' `
  --reference-text 'The quick brown fox jumps over the lazy dog. Anvil serving routes local models where proven and cloud where not.' `
  --report docs/findings/stt-ab-live-20260705.json
```

Reports:

- `docs/findings/stt-ab-live-20260705.json`: measured run with GPU,
  endpoint health, and container status probes.
- `docs/findings/stt-ab-live-warm-20260705.json`: immediate repeat run with
  the same evidence fields.

The WER helper is the repo's existing raw word-split WER. It is
case/punctuation sensitive and intentionally not a normalized ASR corpus
metric.

## Results

Measured run:

| candidate | wire mode | base_url | model | latency (ms) | WER | hypothesis summary |
|---|---|---|---|---:|---:|---|
| parakeet.cpp | JSON | `http://127.0.0.1:30010/v1` | `tdt_ctc-110m` | 148.36 | 0.050 | One homophone: `where` -> `wear` |
| vLLM Whisper | JSON | `http://127.0.0.1:30015/v1` | `whisper-tiny` | 162.46 | 0.150 | `Anvil` -> `Andville`, `routes` -> `roots`, `where` -> `were` |

Warmed rerun:

| candidate | wire mode | base_url | model | latency (ms) | WER | hypothesis summary |
|---|---|---|---|---:|---:|---|
| parakeet.cpp | JSON | `http://127.0.0.1:30010/v1` | `tdt_ctc-110m` | 151.56 | 0.050 | Same one homophone |
| vLLM Whisper | JSON | `http://127.0.0.1:30015/v1` | `whisper-tiny` | 147.31 | 0.150 | Same three word errors |

## Findings

parakeet.cpp is the better v1 default for this host:

- Accuracy was better on the shared sample: 0.050 WER vs 0.150 for the vLLM
  Whisper-tiny probe.
- Startup and operational fit were much simpler. parakeet.cpp was already up
  and answered immediately over the non-streaming JSON transcription shape.
- The vLLM route is viable on sm_120, but it required HF auth, audio Python
  extras, eager mode, low memory caps, and an explicitly disposable serve
  recipe before it was a stable transcription endpoint.
- The vLLM fallback should be retained for "one engine everywhere" experiments
  and larger Whisper/Qwen3-ASR validation, but it is not the lowest-risk v1
  default on fakoli-dark.

## Decision

Use parakeet.cpp as the v1 default `[voice.stt]` engine on fakoli-dark:

```toml
[voice.stt]
base_url = "http://127.0.0.1:30010/v1"
model = "tdt_ctc-110m"
stream = false
```

Keep vLLM Whisper/Qwen3-ASR as a fallback/experimental path. Before promoting
that path, add a managed serve entry and repeat the A/B with a production ASR
model on reserved GPU capacity rather than the disposable `whisper-tiny` probe.
