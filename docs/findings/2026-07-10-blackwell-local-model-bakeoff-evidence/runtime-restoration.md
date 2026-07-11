# Runtime restoration — 2026-07-10 Blackwell bakeoff

Production topology was fully restored after the bakeoff window and verified
with independent preflights. **No production configuration changed.**

| Tier | Container | Port | Restored | Verification |
|---|---|---|---|---|
| heavy (gpt-oss-120b) | `vllm-gptoss120` | :30002 | 2026-07-11 ~06:40Z (after WSL 2.7.10 upgrade) | preflight **ALL PASS** — smoke, structured JSON, needle@~128k (28.9 s), tool batch 20/20 |
| fast (qwen36-35b-a3b-nvfp4) | `vllm-qwen36` | :30003 | 2026-07-11 ~07:55Z (after final 5090 candidate cycle) | preflight pass within tier window — smoke, structured JSON, tool batch 20/20; needle@128k returns HTTP 400 because the probe exceeds the tier's 32,768 max-model-len (expected; matches promotion-era behavior) |

Downtime notes:
- Heavy was down for the PRO-6000 candidate window (Ornith → MiniMax →
  DeepSeek-abort), plus one planned platform restart (`wsl --update` to
  2.7.10 + `autoMemoryReclaim=gradual` activation). It restarted cleanly
  both times from the named volume (~5 min to serving).
- Fast was down during 5090 candidate cycles; restored twice (once mid-run,
  once final). One fast start raced a candidate start after a transient CLI
  permission error; resolved by stopping the container before the candidate
  cycle — no measurement was affected.
- All candidate evaluation containers are stopped and removed. The two
  production containers are the only serves running at close-out
  (GPU0 27.6 GB = fast; GPU1 87.0 GB = heavy).
