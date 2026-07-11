# Reproduction — 2026-07-10 Blackwell local-model bakeoff

All commands run from the repository root (`$REPO_ROOT`) on the fakoli-dark
reference host (RTX 5090 32 GB + RTX PRO 6000 96 GB, sm_120, Windows 11 +
WSL2 + Docker Desktop). Weights live in the named docker volume
`vllm-hfcache` (D:-backed ext4 — never a `C:/` bind mount, repo gotcha #15).

## 0. Prerequisites

```bash
pip install -e .
docker volume create vllm-hfcache   # once, if absent
# HF_TOKEN must be exported in the environment for gated/large pulls.
# Never write the token into any config file.
```

## 1. Pull candidate weights (into the named volume)

```bash
anvil-serving models pull nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 --token-env HF_TOKEN
anvil-serving models pull nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4 --token-env HF_TOKEN
anvil-serving models pull nvidia/Gemma-4-31B-IT-NVFP4 --token-env HF_TOKEN
anvil-serving models pull deepreinforce-ai/Ornith-1.0-35B-FP8 --token-env HF_TOKEN
anvil-serving models pull dervig/m51Lab-MiniMax-M2.7-REAP-139B-A10B-NVFP4 --token-env HF_TOKEN
anvil-serving models pull nvidia/DeepSeek-V4-Flash-NVFP4 --token-env HF_TOKEN
```

## 2. Baselines (production tiers, measured in place)

```bash
# Heavy baseline — gpt-oss-120b, already serving on :30002
anvil-serving benchmark --bakeoff \
  --base-url http://127.0.0.1:30002/v1 --model gpt-oss-120b \
  --candidate-id gpt-oss-120b-baseline --config-id vllm-production-131k \
  --suite chat,context,tool,session,intelligence --context-targets 131072 \
  --evidence-out $EVIDENCE_DIR/baseline-gpt-oss-120b-vllm-mxfp4-131k.bakeoff.json

# Fast baseline — qwen36-35b-a3b-nvfp4 on :30003
anvil-serving serves up fast --manifest examples/fakoli-dark/serves.toml
anvil-serving benchmark --bakeoff \
  --base-url http://127.0.0.1:30003/v1 --model qwen36-35b-a3b-nvfp4 \
  --candidate-id qwen36-35b-a3b-baseline --config-id vllm-production-nvfp4-32k \
  --suite chat,context,tool,session,intelligence --context-targets 32768 \
  --evidence-out $EVIDENCE_DIR/baseline-qwen36-35b-a3b-vllm-nvfp4-32k.bakeoff.json
```

## 3. 5090-track candidates (production heavy stays up)

For each of `cand-nemotron3-nano-30b` (:39020), `cand-nemotron3-omni-30b`
(:39021), `fast-gemma4-31b` (:39011): bring the production fast tier down
first if VRAM requires it, then

```bash
anvil-serving serves down fast --manifest examples/fakoli-dark/serves.toml
anvil-serving serves up <serve-name> --manifest examples/fakoli-dark/serves.toml
anvil-serving preflight --base-url http://127.0.0.1:<port>/v1 --model <served-name>
anvil-serving benchmark --bakeoff \
  --base-url http://127.0.0.1:<port>/v1 --model <served-name> \
  --candidate-id <candidate> --config-id <engine-quant-context> \
  --suite chat,context,tool,session,intelligence --context-targets <ctx> \
  --evidence-out $EVIDENCE_DIR/<candidate>-<engine>-<quant>-<ctx>.bakeoff.json
anvil-serving serves down <serve-name> --manifest examples/fakoli-dark/serves.toml
```

## 4. PRO-6000-track candidates (heavy must come down)

```bash
anvil-serving serves down heavy --manifest examples/fakoli-dark/serves.toml
# then per candidate: serves up / preflight / benchmark --bakeoff / serves down
# candidates: cand-ornith-35b-fp8 (:39022), cand-minimax-m27-reap (:39023),
#             cand-deepseek-v4-flash (:39024)
```

## 5. Restore production

```bash
anvil-serving serves up heavy --manifest examples/fakoli-dark/serves.toml
anvil-serving serves up fast  --manifest examples/fakoli-dark/serves.toml
anvil-serving preflight --base-url http://127.0.0.1:30002/v1 --model gpt-oss-120b
anvil-serving preflight --base-url http://127.0.0.1:30003/v1 --model qwen36-35b-a3b-nvfp4
```

See `runtime-restoration.md` for the recorded restoration outcome of this run.
