# VoiceChat Apple feasibility stop

**Date:** 2026-09-19

**Scope:** read-only candidate feasibility for NVIDIA NemotronLabs VoiceChat
11B MLX 4-bit on an Apple M4 Max with 48 GiB unified memory. No candidate
artifact was downloaded, loaded, or queried.

**Decision:** `no-promotion`; the whole tool-capable pipeline replacement is
blocked. The pinned MLX session surface has no supported tool-result ingress,
and managed native acquisition and memory containment remain unavailable.

## Result

The selected package is 8.553 GiB on disk. That is a disk artifact size, not a
RAM measurement: the disk arithmetic passes, while loader, Metal workspace,
duplex-session, context, and peak-memory values remain unresolved. A smaller,
English-only audio-session experiment is a separate future configuration, not
a qualified replacement.

Protected local audio diagnostics are private-only because no sanitized native
audio artifacts and workload provenance are retained publicly. They contain no
candidate LLM and do not support public latency or quality claims.

No route, client catalog, model, service, or voice configuration changed. No
graph is published because there are no matched candidate performance cells.

## Evidence boundary and provenance

The public summary is a minimized, sanitized derivative of a retained private
operator evidence packet. It preserves generic hardware, pinned public model
and runtime identity, disk arithmetic, feasibility status, and audio-diagnostic
scope. It omits host/account identifiers, private paths, addresses, ports,
service labels, process data, deployment assignments, raw audio, and command
transcripts.

Evidence: [sanitized summary](2026-09-19-voicechat-feasibility-evidence/summary.json) ·
[feasibility result](2026-09-19-voicechat-feasibility-evidence/feasibility-result-v1.json) ·
[evidence index](2026-09-19-voicechat-feasibility-evidence/README.md).

## What remains required

Pin and close the native runtime/artifact acquisition path, provide bounded
loader/RAM/swap/Metal containment, and add a tool-result continuation contract
before evaluating a whole tool-capable replacement. Any later probe needs a
frozen workload, explicit C1/session bounds, repeated functional and quality
evidence, controlled cache state, and independent latency/capacity collection.
