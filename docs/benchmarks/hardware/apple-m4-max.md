# Apple M4 Max benchmark view

**Hardware:** Apple M4 Max laptop, 48 GB unified memory. **Reviewed:**
2026-09-12, using retained September 8 evidence. Deployment and harness
statements are historical, with no current live-state or code-release claim.
This page reports only the measured same-host Apple Silicon lane;
it does not describe the RTX fleet or the reference Mini-to-Dark topology.

## Local voice-lane evidence

The 2026-09-08 refresh evaluated an existing Qwen3 4B baseline and MLX 4-bit
Qwen candidates against functional, strict spoken, strict controlled-output,
and bounded audio/Realtimes gates. No LLM route changed and no LLM candidate was
promoted. A separately accepted Kokoro FastAPI 0.8.2 TTS runtime update was
deployed. The Qwen3.5-9B candidate passed six functional groups but failed the
strict spoken suite 33/36, patch-format 0/3, and controlled output 0/10.
Qwen3.8-27B preliminary preflight passed 5/6 groups but shared-prefix tools
were 1/3 and the lane was too slow for the voice goal. Qwen3.6-35B-A3B remains
an unresolved-policy-memory bounded exploration: its spoken suite passed 36/36
strict and one SDK session passed, but strict JSON preflight failed.

| Capability | Exact configuration | Evidence | Decision |
| --- | --- | --- | --- |
| Local voice LLM candidate | `mlx-community/Qwen3.5-9B-4bit@8b2b98c00a6b4d291155e4890773ca8f769aee53`, MLX-LM 0.31.3 / MLX 0.31.2 | `functional` 6/6; diagnostic quality 9/12; spoken 33/36 strict; strict capacity 0/10 | `no-promotion` |
| Local voice LLM preliminary lane | `mlx-community/Qwen3.8-27B-4bit@3e6447f082e89cc7f0bc6e5441afd38dfce760ff` | compatibility-only preflight 5/6; tools 1/3; no capacity | incomplete, `no-promotion` |
| Local voice LLM bounded challenger | `mlx-community/Qwen3.6-35B-A3B-4bit@38740b847e4cb78f352aba30aa41c76e08e6eb46` | preflight 5/6 with strict JSON failure; spoken 36/36; one accepted SDK session | incomplete, `no-promotion` |
| STT/TTS baseline path smoke | Parakeet.cpp TDT 0.6B v3 Metal + Kokoro FastAPI MPS | one synthesized round trip: WER 0.0, 1896.72 ms | path smoke only; not corpus proof |
| Deployed local TTS runtime | Kokoro FastAPI 0.8.2 `58b08a915b3463cb76e376a2867e04f9d828f4df`; same Kokoro 0.9.4 model/voice assets | one candidate round trip WER 0.0, 2139.31 ms; final warm Realtime follow-up TTFA/E2E 353.53/2186.88 ms; four endpoints HTTP 200 | deployed bounded runtime update; no LLM promotion or speed ratio |

The [Voice LLM MLX dossier](../models/voice-llm-mlx.md) separates this Apple
lane from Qwen3.8 results on other hardware. See the
[dated finding](../../findings/2026-09-08-m4-max-voice-refresh.md) and its
[sanitized evidence bundle](../../findings/2026-09-08-m4-max-voice-refresh-evidence/README.md).
