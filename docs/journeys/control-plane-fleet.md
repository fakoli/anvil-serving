# Control Plane & Fleet

Control Plane & Fleet ties your hosts together. A topology file declares which
host owns which resource; the controller and MCP tools dispatch bounded,
typed operations to that owner; and fleet commands report versions and state
across every host. SSH is a recovery path, not the normal way to operate.

<!-- product-journey:control-plane-fleet -->

## When to use it

- You run serves, voice, or media across more than one machine.
- Agents should operate the system through a bounded tool catalog.
- You need one view of versions, health, and workload across hosts.

## Go deeper

**Topology and networking**

- [Device topologies](../DEVICE-TOPOLOGIES.md)
- [Private networking with Tailscale](../TAILSCALE-NETWORKING.md)
- [Tailnet endpoint runbook](../TAILNET-ENDPOINT-RUNBOOK.md)
- [Fleet state and replication](../FLEET-STATE.md)

**Operate**

- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
- [Workbench portal](../WORKBENCH-PORTAL.md)
- [Telemetry collectors](../TENSORFOLD-TELEMETRY.md)

**Connect agents**

- [OpenClaw integration](../OPENCLAW-INTEGRATION-SPEC.md)
- [Operator skills and sub-agents](../OPERATOR-SKILLS-AND-SUBAGENTS.md)
- [Supplemental memory](../HINDSIGHT.md)
- [Optional Jev assistance](../JEV-ASSISTANCE.md)

**Command reference**

- [Control Plane and Fleet](../cli/control-plane.md), [host](../cli/host.md), [fleet](../cli/fleet.md)
