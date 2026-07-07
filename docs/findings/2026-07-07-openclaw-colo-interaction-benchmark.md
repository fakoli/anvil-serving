# OpenClaw COLO interaction benchmark (2026-07-07)

> **STATUS: LIVE PASS CAPTURED.** From the Fakoli Mini OpenClaw gateway host,
> the COLO runner reached the Fakoli Dark anvil-serving router and completed the
> repeatable direct-router interaction benchmark without HTTP failures,
> truncation, or warnings. Separate OpenClaw config/plugin checks in the same
> artifact verified that the gateway had the Anvil provider and intent plugin
> installed.

This note is the site/blog-ready benchmark citation for the OpenClaw gateway to
anvil-serving router path. It summarizes one bounded live run of direct router
probes launched from the gateway host; it is not proof of OpenClaw's full agent
attempt loop, not a model-card maximum throughput claim, and should not be used
as a standalone promotion decision.

## Environment

- Capture time: `2026-07-07T01:25:03Z`
- Gateway: Fakoli Mini running OpenClaw
- Router: Fakoli Dark anvil-serving front door at `http://100.87.34.66:8000/v1`
- Runner: `examples/openclaw/colo_smoke.py --run-generations --run-interaction-benchmark`
- Artifact filename: `openclaw-colo-live-interactions-repeatable.json` in the operator evidence root
- Artifact SHA-256: `6e108cb68fa9b28600f3854406ffd51302900db267ed0d9a314ba0614768239f`
- Router recipe source: `examples/fakoli-dark/anvil-router.live.toml`
- Router recipe SHA-256: `e03c6684b4262ca10753a698494f5e3f930202e5f7956d2bce060055477269bf`
- Verdict: `pass`
- Proof counts: `8 pass`, `0 warn`, `0 fail`

## Recipe

The benchmark reads its measurement recipe from router tier `params`. These
values travel with the model/tier recipe, so a future heavy-model swap updates
config metadata rather than the runner or skill prompt.

| Tier | Model | Exact max tokens | Stream max tokens | Benchmark reasoning effort | Intent overrides |
|---|---|---:|---:|---|---|
| `fast-local` | `qwen36-27b` | 192 | 128 | none | none |
| `heavy-local` | `gpt-oss-120b` | 1024 | 512 | `low` | `planning`: 2048 exact, 1024 stream |

The router does not forward `params` upstream. They are repeatable smoke/eval
metadata. Runtime defaults for ordinary callers remain in fields such as
`extra_body_defaults`.

## Results

| Metric | Result |
|---|---:|
| Interaction requests | 10 |
| Completed requests | 10 |
| HTTP status counts | `200`: 10 |
| Finish reasons | `stop`: 10 |
| Benchmark warnings | 0 |
| Latency p50 | 407.6 ms |
| Latency p95 | 1041.9 ms |
| Streaming TTFT p50 | 411.9 ms |
| Streaming TTFT p95 | 1049.4 ms |
| Exact output tokens | 350 |
| Exact tokens/sec p50 | 85.37 |
| Exact tokens/sec p95 | 170.82 |

Intent coverage:

| Intent | Requests | Completed | Finish reasons |
|---|---:|---:|---|
| `chat-fast` | 2 | 2 | `stop`: 2 |
| `quick-edit` | 2 | 2 | `stop`: 2 |
| `review` | 2 | 2 | `stop`: 2 |
| `planning` | 2 | 2 | `stop`: 2 |
| `long-context` | 2 | 2 | `stop`: 2 |

The completion requests use the router URL directly. Route evidence below is
from companion `/v1/route` probes in the same artifact, not from OpenClaw's
provider dispatch logs.

Route evidence:

| Intent | Observed route |
|---|---|
| `chat-fast` | `fast-local` / `qwen36-27b` |
| `quick-edit` | `heavy-local` / `gpt-oss-120b` |
| `review` | `heavy-local` / `gpt-oss-120b` |
| `planning` | `heavy-local` / `gpt-oss-120b` |
| `long-context` | `heavy-local` / `gpt-oss-120b` |

## Site Citation

Suggested copy:

> In a live Fakoli Mini to Fakoli Dark COLO smoke test, direct router probes
> launched from the OpenClaw gateway host completed 10/10 repeatable intent
> benchmark requests through anvil-serving with all responses finishing by
> `stop`, no truncation warnings, p50 end-to-end latency of 407.6 ms, p95
> latency of 1041.9 ms, streaming TTFT p50 of 411.9 ms, and exact-generation
> throughput p50 of 85.37 tokens/sec across the bounded benchmark prompts. The
> run covered `chat-fast`, `quick-edit`, `review`, `planning`, and
> `long-context`, with companion route probes showing `chat-fast` on
> `qwen36-27b` and heavier intents on `gpt-oss-120b`.

Use the caveat with the citation:

> These numbers are from a bounded OpenClaw-gateway-host smoke/eval on fixed
> prompts. They validate gateway-to-router reachability, router routing, recipe
> wiring, and direct router interaction behavior; they are not a general
> maximum-throughput benchmark for the models and do not by themselves prove
> OpenClaw's full provider attempt loop.

## Reproduce

```bash
python examples/openclaw/colo_smoke.py \
  --live \
  --gateway-host fakoli-mini \
  --router-base-url http://100.87.34.66:8000/v1 \
  --run-generations \
  --run-interaction-benchmark \
  --artifact .anvil/evidence/openclaw-colo-live-interactions-repeatable.json \
  --pretty
```

If a new recipe changes model family, context window, reasoning controls,
serving engine, quantization, or throughput, update the tier `params` in the
router config and rerun this command before citing new numbers.
