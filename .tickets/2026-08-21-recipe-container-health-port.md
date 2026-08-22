# Managed recipes cannot override an image healthcheck port

## Observed

On 2026-08-21, a managed llama.cpp candidate served its configured endpoint
successfully and passed direct `/health`, model identity, long-context, tool,
and multimodal gates. Docker still classified the container as unhealthy
because the upstream image healthcheck probed its default internal port while
the managed recipe selected a different serve port.

The recipe status and log surfaces exposed the contradictory states, but the
recipe schema had no explicit, validated way to align or replace the inherited
container healthcheck.

## Impact

A correct serve can appear unhealthy to container-aware orchestration. That is
unsafe promotion evidence: ignoring health would hide real failures, while
honoring the inherited probe rejects a proven-live endpoint. Operators must
not use an ad hoc raw Docker override to close this gap.

## Resolution

Add a serving-engine-agnostic recipe healthcheck contract. It must either map
the recipe's declared internal port and path into the container healthcheck or
allow a bounded explicit override. Render the resolved check through the
managed lifecycle command and retain it in status/evidence without assuming a
llama.cpp, SGLang, or vLLM port.

## Acceptance

- A recipe can declare the in-container health port/path independently of the
  published host port.
- Load renders the declaration without shell interpolation or secret values.
- Status distinguishes endpoint readiness from container health and reports
  the resolved probe target.
- Invalid ports, paths, or unsupported healthcheck modes fail before mutation.
- Existing recipes retain their current behavior when the declaration is absent.
- Tests cover inherited, overridden, healthy, unhealthy, and mismatched-port
  cases across at least two engine-neutral fixtures.
