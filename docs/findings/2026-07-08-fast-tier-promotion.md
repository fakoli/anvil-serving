# Fast-Tier Promotion: Qwen3.6-35B-A3B-NVFP4

Date: 2026-07-08

## Decision

Promote `nvidia/Qwen3.6-35B-A3B-NVFP4`, served as
`qwen36-35b-a3b-nvfp4`, to the production Fast tier on Fakoli Dark.

This is the human-gated follow-up to the Fast-tier bakeoff finding:
[`2026-07-08-fast-tier-llm-bakeoff.md`](2026-07-08-fast-tier-llm-bakeoff.md).
The bakeoff task recommended promotion but did not auto-promote. This finding
records the explicit promotion apply and validation evidence.

## Promoted Runtime

| Component | Value |
|---|---|
| Host | Fakoli Dark |
| Production serve | `fast` / `vllm-qwen36` |
| Port | `127.0.0.1:30003` |
| Model | `nvidia/Qwen3.6-35B-A3B-NVFP4` |
| Served model name | `qwen36-35b-a3b-nvfp4` |
| Engine | vLLM nightly |
| Quantization | `modelopt_fp4` |
| Context | 32768 |
| Max sequences | 2 |
| Router preset | `chat-fast` -> `fast-local` |

Mini remains model-free in the reference topology. OpenClaw Gateway and Anvil
Voice Realtime/proxy run on Mini; STT/TTS/LLM model endpoints remain on Fakoli
Dark or are reached through Mini proxy ports that forward to Dark.

## Evidence

Bakeoff metrics from
[`2026-07-08-fast-tier-llm-bakeoff.md`](2026-07-08-fast-tier-llm-bakeoff.md):

| Candidate/config | Voice total / LLM stage | TTFT / E2E | Approx decode tok/s | Hard gates |
|---|---:|---:|---:|---|
| `nvidia/Qwen3.6-27B-NVFP4` control | 1130.21 ms / 814.83 ms | 6203.94 ms / 9041.91 ms | 67.65 | pass |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | 377.52 ms / 165.40 ms | 1489.36 ms / 2302.37 ms | 236.16 | pass |

Direct Fast endpoint preflight after serve swap:

```powershell
anvil-serving preflight --base-url http://127.0.0.1:30003/v1 --model qwen36-35b-a3b-nvfp4 --needle-ctx 32768 --tool-batch 5 --no-thinking
```

Result: all probes passed: smoke, structured JSON, 32768-token needle retrieval,
and shared-prefix tool batch.

Router promotion command:

```powershell
anvil-serving router promote --profile docs\findings\fast-tier-bakeoff-evidence\fast-tier-promotion-profile.json --config examples\fakoli-dark\anvil-router.live.toml
```

Result: profile and config were written to the `anvil-router-cfg` volume and
`anvil-router` restarted successfully with the promoted profile/config.

The promoted profile is an operational routing gate, not fresh per-model
quality calibration for every work class. It keeps Fast eligible for low-latency
chat/voice and bounded-edit paths, while denying explicit Fast pins for
planning and review until those high-risk classes are recalibrated on
`qwen36-35b-a3b-nvfp4`.

High-risk pin proof after the stricter profile was applied:

```json
{
  "status": 200,
  "provider": "heavy-local",
  "model": "gpt-oss-120b",
  "tier": "local",
  "work_class": "planning",
  "reason": "pinned; quality gate: pin fast-local denied for planning; routed via gated pool"
}
```

Route proof from inside the authed `anvil-router` container:

```json
{
  "status": 200,
  "provider": "fast-local",
  "model": "qwen36-35b-a3b-nvfp4",
  "tier": "local",
  "work_class": "chat-fast",
  "reason": "preset='chat-fast'; quality gate: on"
}
```

End-to-end router relay proof through `POST /v1/chat/completions`:

```json
{
  "status": 200,
  "model": "chat-fast",
  "finish_reason": "stop",
  "content": "Fast tier"
}
```

Runtime status after promotion:

| Serve | Container | Port | Health |
|---|---|---:|---:|
| Heavy | `vllm-gptoss120` | 30002 | 200 |
| Fast | `vllm-qwen36` | 30003 | 200 |

OpenClaw gateway sync:

```powershell
anvil-serving harness sync openclaw --config examples\fakoli-dark\anvil-router.live.toml --base-url http://100.87.34.66:8000/v1 --voice --voice-realtime-url ws://127.0.0.1:8765/v1/realtime --gateway-host fakoli-mini --restart
```

Result: synced six Anvil preset models into
`fakoli-mini:~/.openclaw/openclaw.json`, merged with existing config, took a
backup, and restarted the OpenClaw gateway.

Local repo gates:

| Gate | Result |
|---|---|
| `python -m ruff check anvil_serving tests scripts` | pass |
| `python -m pytest tests/ -q` | 2086 passed, 2 skipped |
| `docker compose -f examples\fakoli-dark\docker-compose.yml config --quiet` | pass |
| `anvil-serving serves --manifest examples\fakoli-dark\serves.toml --dry-run up fast` | pass |
| MCP `openclaw_sync` preview with `voice=true` | `voice_model=chat-fast`, `voice_consult_model=anvil/chat-fast` |

## Apply Notes

The first live router promotion attempt rolled back correctly because the
Windows checkout wrote CRLF line endings into `/etc/anvil/config.toml`; the
deployed router image rejected literal `\r` characters in TOML. The promotion
path now normalizes text written into the Linux config volume to LF and writes
stdin with UTF-8 encoding. Regression coverage lives in
`tests/test_router_manage.py`.

The promoted repo state also updates OpenClaw/voice manifests to call the
`chat-fast` preset and expect route proof for `fast-local` /
`qwen36-35b-a3b-nvfp4` / `local`.

## Adversarial Review Follow-up

Two read-only review passes were run after the initial apply. The runtime/config
review found that MCP `openclaw_sync` still defaulted Talk to the tier pin
`fast-local`; the MCP wrapper now preserves explicit `voice_model` values but
otherwise lets the harness resolve the preset default (`chat-fast`, then
`chat`). The same review found that the promotion profile still allowed pinned
Fast planning; the profile now denies Fast planning and review until those
classes are recalibrated on the promoted model.

The docs/evidence review found that the profile could be misread as fresh
per-model quality calibration; this report now labels it as an operational
routing gate. It also found a stale 64K rerun command and an old setup story
that read like current guidance; both were corrected.
