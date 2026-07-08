---
name: anvil-serving-voice-ops
description: Validate and operate anvil-serving voice manifests, profile switches, private audio bridges, Hugging Face speech-to-speech sidecars, realtime run commands, and bounded voice benchmarks without treating voice evidence as router promotion evidence.
---

# Anvil Serving Voice Ops

Use this skill for voice-pipeline operations: validating sidecar manifests,
rendering speech-to-speech commands, previewing compose snippets, bringing
manifest-owned managed/native STT/TTS lifecycle up or down, selecting declared
voice profiles, running private STT/TTS bridge ports, running the realtime voice
loop, and running bounded voice benchmarks.

## Start Here

1. Read `README.md`, `CLAUDE.md`, and
   `docs/OPERATOR-SKILLS-AND-SUBAGENTS.md` before changing behavior or
   reporting voice results as product evidence.
2. Prefer the existing CLI verbs before proposing a new tool:
   `anvil-serving voice-sidecar validate`,
   `anvil-serving voice-sidecar command`,
   `anvil-serving voice-sidecar compose`, `anvil-serving voice up`,
   `anvil-serving voice down`, `anvil-serving voice profiles`,
   `anvil-serving voice bridge`, `anvil-serving voice run`, and
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

Stop for a human gate before public or non-loopback realtime binds, exposing a
realtime token, exposing non-loopback STT/TTS bridge ports, changing
cloud/STT/TTS spend, installing or updating external speech-to-speech packages,
or starting long-running foreground services on a shared host.

`voice-sidecar command --with-auth` and `voice-sidecar compose --with-auth`
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
  `anvil-serving voice-sidecar validate --config <manifest>`.
- Host command rendering:
  `anvil-serving voice-sidecar command --config <manifest> --json`; add
  `--with-auth` only when the operator accepts the argv exposure warning.
- Compose rendering:
  `anvil-serving voice-sidecar compose --config <manifest>`; inspect loopback
  ports, `host.docker.internal` routing, image name, and auth comments.
- Managed audio serves:
  `anvil-serving voice up --config <manifest>` and
  `anvil-serving voice down --config <manifest>`; external lifecycle serves
  should be reported as skipped rather than forced into local Docker control.
- Profile selection:
  `anvil-serving voice profiles --config <manifest>` to list profiles, then add
  `--profile <name>` to `voice up`, `voice down`, `voice run`, or
  `voice benchmark` when switching audio topology. For reference OpenClaw Talk
  and candidate benchmarks, keep Mini model-free and select Dark-host audio or
  a Mini-side proxy to Dark. Use `mini-audio` only when explicitly testing the
  optional same-host/local-audio mode.
- Private audio bridge:
  `anvil-serving voice bridge --dry-run` first. A non-loopback live bind must
  be private/tailnet-scoped and include
  `--i-understand-this-exposes-voice-audio`. Prefer a concrete private or
  tailnet listen address; wildcard binds require `--allow-wildcard-listen` and
  prior firewall or tailnet ACL proof.
- Realtime loop:
  `anvil-serving voice run --config <manifest>` after endpoint reachability is
  understood. Treat unreachable STT/TTS/router endpoints as blockers.
- Bounded benchmark:
  `anvil-serving voice benchmark --config <manifest>` and capture the JSON as
  `voice-pipeline` evidence with `promoted=false`.

## Result Packet

Return an `operator-workflow/v1` packet when coordinating with the broader
workbench. Use `artifacts` entries with `kind: "voice-benchmark"` or
`kind: "voice-sidecar-render"`, `evidence_scope: "voice-pipeline"`, and
`promotion_quality_evidence: false`. Keep `advisory_priors` empty unless
external voice-specific priors are explicitly identified as advisory-only. Keep
`promoted=false` and `human_gate_required=true` for any workflow that asks for
router promotion, public binds, cloud enablement, or long-running service
starts.

If the existing `voice` and `voice-sidecar` verbs cannot cover the request,
report the missing wrapper as a product gap after showing the closest safe
existing command and why it is insufficient.
