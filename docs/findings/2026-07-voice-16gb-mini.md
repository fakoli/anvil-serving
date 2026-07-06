# Voice on a 16GB Mini: local STT+TTS, LLM routed to fakoli-dark

> **STATUS: READY FOR LIVE T016 RUN.** The harness is
> `scripts/voice/mini_validation.py`. The acceptance command writes a JSON
> report, appends one session row below, and returns nonzero for an
> `unsupported` verdict so a negative-control run cannot satisfy the task.
> Missing target-host, fakoli-dark route/auth, post-benchmark memory, or
> first-audio proof is `unsupported`, not `experimental`:
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
   the STT/TTS containers. The report also records host memory before/after
   load and attempts `docker stats` per configured serve container after the
   end-to-end benchmark, so lazy model load is included.
2. **A non-Mini run is a negative control, not a pass.** The harness records
   `host_is_16gb_class` and `host_matches_expected_mini`; runs on a
   workstation, generic 16GB VM, or GPU host must be read as `unsupported`
   unless the report proves both a 16GB-class host and the expected Mini host.
3. **No serves.toml entries ship for the Mini's local STT/TTS containers
   yet** (mirrors the same gap noted in the STT/TTS A/B docs) — declare them
   before running, or `bring_up_ok` will read `False` with a
   `ServeNotConfigured` error. The harness still probes the local endpoints,
   so already-running sidecars can pass readiness without being managed by
   `anvil-serving serves`.

## How to run

```bash
python scripts/voice/mini_validation.py --report
```

The default manifest is `examples/voice/fakoli-dark.toml` when present: STT and
TTS are loopback endpoints on the Mini, while the LLM base URL points at
fakoli-dark over the tailnet and declares the expected route/provider/model
and expected endpoint host. The shell running the command must have
`ANVIL_ROUTER_TOKEN` set when the manifest names that auth env var.

For a custom Mini serve manifest:

```bash
python scripts/voice/mini_validation.py \
  --config examples/voice/fakoli-dark.toml \
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
| expected Mini host match | _TBD_ | default pattern matches `fakoli-mini`/`mini`; override with `--target-host-pattern` only for renamed target hardware |
| STT startup (s) | _TBD_ | |
| STT container memory after benchmark | _TBD_ | via `docker stats` |
| TTS startup (s) | _TBD_ | |
| TTS container memory after benchmark | _TBD_ | via `docker stats` |
| TTFA (ms), LLM on fakoli-dark | _TBD_ | via `anvil_serving.voice.benchmark` |
| turn latency (ms) | _TBD_ | |
| TTS output bytes | _TBD_ | must be >0 for first-audio proof |
| LLM endpoint host / route | _TBD_ | must be fakoli-dark tailnet host and expected route provider/model/tier |
| LLM auth env present | _TBD_ | `ANVIL_ROUTER_TOKEN` expected for fakoli-dark |
| driver process peak RSS (MB) | _TBD_ | informational only — see gap #1 |
| failure mode(s) observed | _TBD_ | e.g. OOM-kill, tailnet timeout, cold-start stall |
| verdict | _TBD_ | `supported`, `experimental`, or `unsupported` |

## Session log

| timestamp (UTC) | host | host memory | verdict | STT | TTS | TTFA / latency ms | host used / available GB | failure modes | report path |
|---|---|---|---|---|---|---|---|---|---|

(`mini_validation.py` appends a row here automatically — see
`append_finding_row` in that script.)

## Findings

_TBD once run on target hardware — in particular: does total container memory
(STT + TTS + whatever else is resident on a 16GB box) leave enough headroom, or
does this reproduce a variant of the WSL2/`.wslconfig` OOM gotcha
(CLAUDE.md gotcha #3) on a different box?_

## Decision

_TBD from the first target-hardware run — `supported` if the expected 16GB
Fakoli Mini runs local STT+TTS with the LLM routed to fakoli-dark and records
usable memory headroom/latency. `experimental` is reserved for complete target
evidence with a caution such as low remaining memory; incomplete, non-target,
or missing route/audio/memory proof is `unsupported`._
