---
name: anvil-serving-release-readiness
description: Gate an Anvil Serving package release and coordinated live deployment across Windows, Fakoli Dark, Fakoli Mini, the controller, router, Hermes, Pi, and OpenClaw. Use when a change will be merged, tagged, published, or deployed and exact manifest dependencies, version parity, recovery policy, and outage-free client smokes must be proven.
---

# Anvil Serving Release Readiness

Use the canonical repository skill:

`skills/anvil-serving-release-readiness/SKILL.md`

Follow all four stages. A published package, healthy container, or passing
route probe is not enough by itself. Do not close the release while any
declared endpoint is version-skewed or any in-scope client path is unavailable.
The canonical skill also covers router-derived Hermes/Pi/OpenClaw catalog sync
and explicitly authorized fix-forward recovery.
