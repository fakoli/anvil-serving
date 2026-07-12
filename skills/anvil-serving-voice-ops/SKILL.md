---
name: anvil-serving-voice-ops
description: Operate topology-owned anvil-serving voice audio and Realtime proxy lifecycle, Mini-to-Dark loopback forwarding, profiles, sidecars, and bounded voice benchmarks. Use for voice status, logs, start/stop/restart, OpenClaw Talk topology checks, and voice evidence that must remain separate from router promotion evidence.
---

# Anvil Serving Voice Ops

Use this skill for voice-pipeline operations: validating sidecar manifests,
rendering speech-to-speech commands, managing Dark-owned STT/TTS, managing the
persistent Mini Realtime proxy, selecting profiles, running the Mini-local
forwarding bridge, and collecting bounded voice benchmarks.

## Start Here

1. Read `README.md`, `CLAUDE.md`, and
   `docs/OPERATOR-SKILLS-AND-SUBAGENTS.md` before changing behavior or
   reporting voice results as product evidence.
2. Prefer the existing CLI verbs before proposing a new tool:
   `anvil-serving voice sidecar validate`,
   `anvil-serving voice sidecar command`,
   `anvil-serving voice sidecar compose`,
   `anvil-serving voice audio {up|down|status|logs}`,
   `anvil-serving voice profiles {list|validate}`,
   `anvil-serving voice proxy {run|up|down|restart|status|logs|bridge}`, and
   `anvil-serving voice benchmark`.
3. Use `127.0.0.1` for local URLs. Do not introduce `localhost`.
4. Pass secrets by environment variable name only. Do not put literal API keys
   in manifests, commands, compose snippets, packets, or logs.
5. Record the command host before interpreting loopback. In the reference
   OpenClaw topology, Fakoli Mini runs Gateway plus Anvil Voice Realtime/proxy
   and reserves its 16 GB RAM for OpenClaw, Claude Code, and Codex. Do not run
   STT/TTS/LLM model serves on Mini for reference testing. Use `dark-audio` for
   Dark bridge ports or `mini-dark-audio-proxy` for Mini proxy ports forwarding
   to Dark. `mini-audio` is an explicit optional same-host/local-audio mode
   only. A non-gateway checkout cannot validate Mini-local proxy paths by
   calling its own `127.0.0.1`.
6. Treat voice benchmark output as voice-pipeline evidence. Voice results are
   not router work-class promotion evidence and must not satisfy a
   `router_promote` gate.
7. Do not introduce one-off lifecycle, proxy, or port-forwarding scripts as the
   operational path. If repeatable voice operation needs a new control, it
   belongs in `anvil-serving voice` and, when agent-operated, MCP/controller.

## Gates

Stop for a human gate before public or non-loopback Realtime binds, exposing a
Realtime token, changing
cloud/STT/TTS spend, installing or updating external speech-to-speech packages,
or starting long-running foreground services on a shared host.

`voice sidecar command --with-auth` and `voice sidecar compose --with-auth`
render environment variable references for authenticated router calls. The
command path returns argv, so explicitly warn that the env-var reference appears
in process argv. The compose path includes an auth exposure comment in the
rendered service. Use either path only on private hosts where process and
Docker metadata are protected.

Never use `anvil-serving voice benchmark` as a shortcut for router quality
promotion. Voice benchmarks measure the voice pipeline: turn latency, TTFA,
audio/STT/TTS behavior, realtime transport, and user-perceived loop quality.
They can support a voice-pipeline status report, but router work-class
promotion still requires router preflight, benchmark, calibration, independent
review, and human approval.

## Playbooks

- Sidecar manifest validation:
  `anvil-serving voice sidecar validate --config <manifest>`.
- Host command rendering:
  `anvil-serving voice sidecar command --config <manifest> --json`; add
  `--with-auth` only when the operator accepts the argv exposure warning.
- Compose rendering:
  `anvil-serving voice sidecar compose --config <manifest>`; inspect loopback
  ports, `host.docker.internal` routing, image name, and auth comments.
- Managed audio serves:
  `anvil-serving voice audio up --topology <topology> --config <manifest>` and
  `voice audio down`; preview mutations first and require `--confirm` for live
  controller execution. Use `anvil-serving voice audio status` and bounded
  `anvil-serving voice audio logs` for reads.
  External lifecycle serves
  should be reported as skipped rather than forced into local Docker control.
- Profile selection:
  `anvil-serving voice profiles list --config <manifest>` to list profiles,
  then add `--profile <name>` to audio, proxy, or
  `voice benchmark` when switching audio topology. For reference OpenClaw Talk
  and candidate benchmarks, keep Mini model-free and select Dark-host audio or
  a Mini-side proxy to Dark. Use `mini-audio` only when explicitly testing the
  optional same-host/local-audio mode.
- Mini-to-Dark audio bridge:
  `anvil-serving voice proxy bridge --topology <topology> --config <manifest>
  --profile mini-dark-audio-proxy --dry-run` first. Run it on Mini. Require
  topology-derived Dark targets and reject every non-loopback Mini listener.
- Realtime proxy:
  use `anvil-serving voice proxy up` for the persistent Mini process and
  `anvil-serving voice proxy run` for a foreground diagnostic. Use
  `down`/`restart` only after preview and explicit
  confirmation; use bounded `status`/`logs` for inspection. Treat unreachable
  STT/TTS/router endpoints as blockers. Never start audio lifecycle implicitly.
- Bounded benchmark:
  `anvil-serving voice benchmark --topology <topology> --config <manifest>` and
  capture JSON as `voice-pipeline` evidence with `promoted=false`.

## Result Packet

Return an `operator-workflow/v1` packet when coordinating with the broader
workbench. Use `artifacts` entries with `kind: "voice-benchmark"` or
`kind: "voice-sidecar-render"`, `evidence_scope: "voice-pipeline"`, and
`promotion_quality_evidence: false`. Keep `advisory_priors` empty unless
external voice-specific priors are explicitly identified as advisory-only. Keep
`promoted=false` and `human_gate_required=true` for any workflow that asks for
router promotion, public binds, cloud enablement, or long-running service
starts.

If the existing `voice` and `voice sidecar` verbs cannot cover the request,
report the missing wrapper as a product gap after showing the closest safe
existing command and why it is insufficient.
