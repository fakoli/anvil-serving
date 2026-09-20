# Feasibility plan

Screen the integrated VoiceChat path before acquiring weights. Use 48 GiB unified memory and a 16 GiB policy reserve for OS, desktop and protected voice. Do not subtract that reserve again from observed available RAM. Preserve unknown loader, workspace, per-session and per-token state. The proposed text-side contract is 8,192 input + 512 output tokens, C1; effective duplex audio/state context remains unknown. A later audio-only probe would be loopback-only, C1, at most 30 seconds, and declare `tool_support=false`.

Disk policy reserves an extra package copy for temporary behavior, 5 GiB for runtime/evidence, and 20 GiB free. The selected package fits this arithmetic; secondary loader dependencies and managed native acquisition remain unresolved. Recheck live space before any later acquisition.

Future gates: 100% deterministic success, at most 5% relative quality loss, at least 20% warm end-to-end improvement, no OOM, swap growth, protected-service restart or request loss; protected-service p95 degradation at most 20% against a measured baseline. These thresholds are policy, not observed results. No candidate workload ran and none is qualified by this record.

Reproduce the calculation with the repository's deterministic recipe-feasibility calculator using `feasibility-input-v1.json`. The paired result retains the calculator's native schema.
