# Voice on a 16GB Mini: local STT+TTS, LLM routed to fakoli-dark

> **STATUS: READY FOR LIVE T016 RUN.** The harness is
> `scripts/voice/mini_validation.py`. The acceptance command writes a JSON
> report, appends one session row below, and returns nonzero unless the verdict
> is `supported` so a negative-control or partial proof cannot satisfy the task.
> Missing target hardware, fakoli-dark route/auth, endpoint model identity,
> post-benchmark per-serve memory, nonblank STT/LLM text, or first-audio proof
> is `unsupported`, not acceptance evidence:
>
> ```bash
> python scripts/voice/mini_validation.py --report
> ```

Related: `docs/findings/2026-07-04-hf-speech-to-speech-review.md` s8 (VRAM/RAM
math: STT ~1-4GB, TTS ~0.5-7GB — comfortably small even on a 16GB box) · the
saved Mini<->router tailnet-binding note (router publishes its tailnet IP,
not loopback) · `scripts/voice/mini_validation.py`

## Known gaps (flagged, not hidden)

1. **Driver-process RSS is not the number that matters.** The script's own
   `resource.getrusage`-based memory reading reflects its driver process, not
   the STT/TTS serves. The report also records host memory before/after load
   and requires post-benchmark per-serve memory. Managed container serves use
   `docker stats`; explicitly external native Mini serves use macOS `lsof`
   plus `ps` to attribute RSS to the process listening on the configured
   loopback port, so lazy model load is included.
2. **A non-Mini run is a negative control, not a pass.** The harness records
   `host_is_16gb_class`, `host_matches_expected_mini`, and
   `host_hw_model_matches_expected`; runs on a workstation, generic 16GB VM,
   or GPU host must be read as `unsupported` unless the report proves a
   16GB-class macOS host, a Fakoli Mini host identity, and the expected Mini
   hardware model (`Mac16,10` by default).
3. **The Mini manifest uses external native STT/TTS by default.**
   `examples/voice/fakoli-mini.toml` declares `lifecycle = "external"` for
   both audio endpoints. `anvil-serving voice up/down` therefore skips managed
   Docker lifecycle for those endpoints, but the harness still requires them
   to be ready on `127.0.0.1:30010/30011`, complete the live benchmark, and
   produce endpoint-attributed process RSS plus a matching `/v1/models` model
   id after the benchmark. Managed container serves may still be validated
   with a custom `serves.toml`.
4. **Router auth is checked both ways.** Manifests that name
   `ANVIL_ROUTER_TOKEN` must prove the token is present for the positive route
   probe and that a no-Authorization `/v1/route` probe is rejected with
   401/403.

## How to run

```bash
python scripts/voice/mini_validation.py --report
```

The default manifest is `examples/voice/fakoli-mini.toml` when present: STT and
TTS are external native loopback endpoints on the Mini, while the LLM base URL
points at fakoli-dark over the tailnet and declares the expected
route/provider/model and expected endpoint host. The shell running the command
must have `ANVIL_ROUTER_TOKEN` set when the manifest names that auth env var.

For a custom Mini serve manifest:

```bash
python scripts/voice/mini_validation.py \
  --config examples/voice/fakoli-mini.toml \
  --serves-manifest ./serves.toml \
  --report /tmp/mini-run1.json
```

Exploratory negative-control runs may opt into a zero exit for diagnostics,
but that mode is not acceptable as T016 evidence:

```bash
python scripts/voice/mini_validation.py --report /tmp/mini-negative.json --allow-unsupported
```

## Measurement template

| metric | value | notes |
|---|---|---|
| host total memory | _TBD_ | must be 16GB-class for a target-hardware pass |
| host memory before load | _TBD_ | available/used GB |
| host memory after serves ready | _TBD_ | available/used GB |
| host memory after benchmark | _TBD_ | available/used GB; verdict uses this value |
| expected Mini host match | _TBD_ | default pattern matches `Fakoli Mini`/`Fakoli-Mini-2`; override with `--target-host-pattern` only for renamed target hardware |
| expected Mini hardware model | _TBD_ | default `Mac16,10`; override with `--target-hw-model-pattern` only for approved target hardware changes |
| STT startup (s) | _TBD_ | |
| STT memory proof after benchmark | _TBD_ | `docker_stats` for managed containers, or `macos_process_rss` attributed to the `127.0.0.1:30010` listener |
| TTS startup (s) | _TBD_ | |
| TTS memory proof after benchmark | _TBD_ | `docker_stats` for managed containers, or `macos_process_rss` attributed to the `127.0.0.1:30011` listener |
| TTFA (ms), LLM on fakoli-dark | _TBD_ | via `anvil_serving.voice.benchmark` |
| turn latency (ms) | _TBD_ | |
| STT/LLM text and TTS audio | _TBD_ | STT hypothesis and LLM reply must be nonblank; TTS output must include >=0.25s of audio |
| LLM endpoint host / route | _TBD_ | must be fakoli-dark tailnet host and expected route provider/model/tier |
| LLM auth env present | _TBD_ | `ANVIL_ROUTER_TOKEN` expected for fakoli-dark |
| driver process peak RSS (MB) | _TBD_ | informational only — see gap #1 |
| failure mode(s) observed | _TBD_ | e.g. OOM-kill, tailnet timeout, cold-start stall |
| verdict | _TBD_ | `supported` is the only accepting verdict |

## Session log

| timestamp (UTC) | host | host memory | verdict | STT | TTS | TTFA / latency ms | host used / available GB | failure modes | report path |
|---|---|---|---|---|---|---|---|---|---|

(`mini_validation.py` appends a row here automatically — see
`append_finding_row` in that script.)

## Findings

_TBD once run on target hardware — in particular: does total serve memory
(STT + TTS + whatever else is resident on a 16GB box) leave enough headroom, or
does this reproduce a variant of the WSL2/`.wslconfig` OOM gotcha
(CLAUDE.md gotcha #3) on a different box?_

## Decision

_TBD from the first target-hardware run — `supported` if the expected 16GB
Fakoli Mini runs local STT+TTS with the LLM routed to fakoli-dark, records
usable memory headroom/latency, and satisfies the hardware/auth/model/audio
proof gates. Incomplete, non-target, low-headroom, or missing
route/audio/memory proof is `unsupported`._
