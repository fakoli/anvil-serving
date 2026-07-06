# Voice TTS Candidate Preflight: Kokoro-82M, Orpheus-3B, Qwen3-TTS

> **STATUS: PASSED for anvil task T009 on 2026-07-06.**
> The no-argument packet proof command synthesized all three T009 candidates
> on the fakoli-dark Blackwell workstation and exited 0:
>
> ```powershell
> python scripts/voice/preflight_tts.py --report --capture-dir "$env:TEMP\anvil-tts-ab-full-20260706"
> ```

Related: `docs/findings/2026-07-04-hf-speech-to-speech-review.md` s8
(sm_120 component guidance) and `scripts/voice/preflight_tts.py`.

## Decision

Select **Kokoro-82M** as the v1 default TTS engine.

Reasoning:

- Kokoro is the fastest measured candidate and is already the stable local TTS
  serve in the voice pipeline.
- Orpheus synthesized successfully but was much slower and required a
  two-container disposable sidecar: llama.cpp on the RTX 5090 plus a patched
  OpenAI-compatible FastAPI shim.
- Qwen3-TTS synthesized successfully on the RTX 5090 with the CustomVoice
  checkpoint, but the measured sample was slower than real time and the
  disposable API container had to install a conflicting dependency set inside
  the throwaway vLLM image.

No subjective MOS or human listening winner is claimed here. The `quality`
field in the JSON reports remains `not measured; human listening pass
required`, and the table below records only automated PCM sanity.

## Hardware and live window

Before the live sidecar window, the normal fast tier
`vllm-qwen36` (`nvidia/Qwen3.6-27B-NVFP4`) was stopped through the product
serve lifecycle:

```powershell
python -m anvil_serving.cli serves --manifest examples\fakoli-dark\serves.toml down fast
```

That freed the RTX 5090 from about 28 GiB used to about 1.4 GiB used. During
the successful full proof report, `nvidia-smi` recorded:

| GPU | device | total MiB | used MiB in report |
|---:|---|---:|---:|
| 0 | NVIDIA GeForce RTX 5090 | 32607 | 10563 |
| 1 | NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition | 97887 | 90660 |

After the proof, the disposable sidecars were torn down and the normal fast
tier was restored:

```powershell
docker compose -f "$env:TEMP\anvil-t009-sidecars\docker-compose.t009.yml" down
python -m anvil_serving.cli serves --manifest examples\fakoli-dark\serves.toml up fast
python -m anvil_serving.cli serves --manifest examples\fakoli-dark\serves.toml status
```

Post-restore status showed `heavy` and `fast` both running with health `200`.

## Live proof setup

The successful proof used the existing Kokoro serve and disposable sidecars for
Orpheus and Qwen:

| candidate | container(s) | endpoint | implementation note |
|---|---|---|---|
| Kokoro-82M | `kokoro-tts` | `http://127.0.0.1:30011/v1` | Existing `ghcr.io/remsky/kokoro-fastapi-gpu:latest-cu128` serve. |
| Orpheus-3B | `orpheus-llama`, `orpheus-tts` | `http://127.0.0.1:30013/v1` | `lex-au/Orpheus-3b-FT-Q8_0.gguf` served by `ghcr.io/ggml-org/llama.cpp:server-cuda` on the RTX 5090, plus a patched Orpheus-FastAPI shim that added `/health` and `/v1/models`. The shim was forced CPU-only because its CUDA 12.4 PyTorch wheel does not support `sm_120`; llama.cpp generation still ran on the 5090. |
| Qwen3-TTS 1.7B | `qwen3-tts` | `http://127.0.0.1:30014/v1` | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` loaded in a disposable `vllm/vllm-openai:nightly` container with the Qwen OpenAI FastAPI server mounted from a temp checkout. The Base checkpoint loaded but rejected the plain custom-voice API path, so the successful run used CustomVoice. |

The disposable compose file lived at
`$env:TEMP\anvil-t009-sidecars\docker-compose.t009.yml` and was not added to
the repo.

## Results

Full proof output:

```text
candidate        ttfa       rtf      status    capture
------------------------------------------------------
kokoro-82m       444.2ms    0.154    ok        C:\Users\sdoum\AppData\Local\Temp\anvil-tts-ab-full-20260706\kokoro-82m.wav
orpheus-3b       2958.8ms   0.991    ok        C:\Users\sdoum\AppData\Local\Temp\anvil-tts-ab-full-20260706\orpheus-3b.wav
qwen3-tts-1.7b   4697.1ms   1.796    ok        C:\Users\sdoum\AppData\Local\Temp\anvil-tts-ab-full-20260706\qwen3-tts-1.7b.wav
```

| candidate | endpoint | wire model | TTFA (ms) | audio seconds | RTF | automated audio sanity note | quality note |
|---|---|---|---:|---:|---:|---|---|
| Kokoro-82M | `http://127.0.0.1:30011/v1` | `kokoro` | 444.20 | 2.8887 | 0.1545 | PCM sanity passed: 69,328 samples, RMS 1637.55, peak 11,175, 62,716 nonzero samples. | Not measured; human listening pass required. |
| Orpheus-3B | `http://127.0.0.1:30013/v1` | `orpheus-3b` | 2958.77 | 2.9876 | 0.9912 | PCM sanity passed: 71,702 samples, RMS 1532.58, peak 28,006, 71,262 nonzero samples. | Not measured; human listening pass required. |
| Qwen3-TTS 1.7B | `http://127.0.0.1:30014/v1` | `qwen3-tts` | 4697.07 | 2.6169 | 1.7959 | PCM sanity passed: 62,805 samples, RMS 1411.14, peak 9,983, 60,384 nonzero samples. | Not measured; human listening pass required. |

Output reports:

- `docs/findings/2026-07-voice-tts-ab.json` - full T009 proof; all three
  candidates ready and synthesized; command exit 0.
- `docs/findings/tts-ab-kokoro-current-20260706.json` - explicit Kokoro-only
  smoke proof after the readiness hardening.
- `docs/findings/tts-ab-orpheus-current-20260706.json` - Orpheus-only isolated
  proof during the sidecar window.
- `docs/findings/tts-ab-qwen3-current-20260706.json` - Qwen-only isolated
  proof during the sidecar window.
- `docs/findings/tts-ab-kokoro-5090-20260706.json` - earlier temporary 5090
  Kokoro proof retained as historical supporting evidence.

## Follow-up notes

- The harness now fails readiness if `/v1/models` does not advertise the
  expected model ID. This prevents a healthy but wrong service from satisfying
  the T009 proof.
- Orpheus should not be productized from this exact disposable setup. A stable
  future Orpheus serve needs either a Blackwell-compatible PyTorch/SNAC image
  or an intentional CPU decoder design documented in the serve manifest.
- Qwen3-TTS should not be productized from the throwaway vLLM container either.
  The successful path proves the model can synthesize on the RTX 5090, but a
  real serve image should pin a coherent CUDA/PyTorch/Transformers set and
  avoid mutating the vLLM runtime dependency graph at startup.
- Do not dump container environments into evidence; probe only specific safe
  fields such as `CUDA_VISIBLE_DEVICES`.

## Source references

- Kokoro-FastAPI: <https://github.com/remsky/Kokoro-FastAPI>
- Orpheus-FastAPI: <https://github.com/Lex-au/Orpheus-FastAPI>
- Orpheus-TTS: <https://github.com/canopyai/Orpheus-TTS>
- Qwen3-TTS: <https://github.com/QwenLM/Qwen3-TTS>
- Qwen3-TTS OpenAI FastAPI sidecar used for the disposable proof:
  <https://github.com/pasky/Qwen3-TTS-Openai-Fastapi>
- vLLM-Omni speech API: <https://docs.vllm.ai/projects/vllm-omni/en/latest/serving/speech_api/>
