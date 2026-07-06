# ADR-0015 - Operator skills and sub-agent workflows

- **Status:** Accepted
- **Date:** 2026-07-06
- **Relates to:** ADR-0012, ADR-0013, ADR-0014,
  `docs/OPERATOR-PLAYBOOKS.md`,
  `docs/OPERATOR-SKILLS-AND-SUBAGENTS.md`,
  `anvil_serving/mcp.py`, `anvil_serving/harness.py`

## Context

ADR-0013 established the split between the OpenClaw intent hook, the anvil
router data plane, and the MCP control plane. ADR-0014 added a tailnet controller
transport for split-host deployments. The remaining gap is the human-facing and
agent-facing workflow layer: how an operator or coding agent should use the many
anvil-serving verbs without becoming an expert in every host, model, profile,
and gateway detail.

The current MCP surface is useful but partial. It covers router/serve/doctor
status, route probes, OpenClaw sync, gateway restart, preflight probes, and
bounded benchmark probes. Many verbs still exist only as CLI operations:
inventory, model pull, serve lifecycle, deploy/init rendering, router promotion,
decision-log inspection, external benchmark comparison, host repair, voice ops,
profile analysis, and cache pruning.

The workflow surface must be harness-neutral. OpenClaw is the reference gateway
because it has a `before_model_resolve` hook and Anvil-owned provider config,
but Codex and Claude Code also need repo-visible skills and sub-agent roles.
Those harnesses can use the same router presets in the `model` field and the
same MCP/controller control plane even though they do not have OpenClaw's
per-turn hook.

The product also has a measured sub-agent workload. `profile`, `benchmark`,
`_aggregate_usage.py`, and `_role_split.py` all assume that most specialist
work can be smaller, shorter-output, and more bounded than the main
orchestrator. That means the workflow layer should be able to use modest models
for deterministic slices while reserving larger models and humans for policy,
ambiguous diagnosis, and promotion gates.

## Considered Options

1. **Expose every CLI verb directly as an MCP tool.** Rejected. Some verbs are
   long-running services, destructive operations, or local development entry
   points. A direct one-tool-per-command wrapper would make it too easy for an
   agent to run unsafe operations without the playbook context.

2. **Keep MCP small and document everything else as manual CLI usage.** Rejected.
   ADR-0012 already frames repeated raw operational steps as product gaps. If
   agents are expected to operate the deployment, the common read, probe, sync,
   and lifecycle paths need structured results and confirmation gates.

3. **Build a full workflow engine inside anvil-serving.** Rejected for now. The
   router should not grow into an orchestration runtime. Existing agent harnesses
   can run the workflow if the product gives them stable tools, skills, packet
   formats, and safety rules.

4. **Use a stratified model: MCP for bounded operations, skills for playbooks,
   and sub-agents for parallel workflow slices.** Chosen. This keeps anvil-serving
   responsible for typed operations and evidence contracts while allowing the
   operator's harness to decide how many agents and which model tiers to use.

## Decision

anvil-serving will treat operator skills and sub-agent workflows as a product
surface above the MCP/controller control plane.

### Tooling boundary

MCP/controller tools should wrap operations that are bounded, structured, and
safe to call through schemas:

- read-heavy status and inventory;
- bounded logs and recent decision summaries;
- route probes, preflight, and benchmark probes;
- harness config preview/apply;
- serve and router lifecycle operations with dry-run plus `confirm`;
- promotion validation and evidence preview without automatic promotion.

Some verbs remain skill/CLI-driven:

- long-running data-plane processes such as `serve`, `multiplexer`, and voice
  realtime servers;
- destructive cache pruning;
- host repair that restarts Docker or WSL;
- model pulls that are long-running network/disk operations;
- live routing promotion, cloud-tier enablement, and non-loopback exposure.

### Skill boundary

Skills choose playbooks and fill tool arguments. They are not another policy
engine. Each skill should:

- list the MCP tools it expects and fall back only to documented anvil-serving
  CLI verbs when a wrapper is missing;
- run previews before expensive or mutating operations;
- preserve structured results and command previews as evidence;
- stop for a human gate before profile promotion, router policy changes,
  cloud enablement, destructive host/cache changes, or public/non-loopback
  binds.

The first skill set should cover readiness, model catalog, serve swap,
OpenClaw harness sync, promotion evidence, host repair, and voice operations.

The seed deliverable is a portable `anvil-serving-workbench` skill checked into
the repo for Codex, Claude Code, and OpenClaw example use. Specialized skills
can split out later once their backing MCP tools exist.

### Sub-agent boundary

Sub-agents should own bounded slices:

- inventory scout;
- route analyst;
- serve operator;
- preflight runner;
- benchmark runner;
- evidence reporter;
- independent quality critic.

Small or local models are appropriate for deterministic status collection,
schema filling, command preview interpretation, and report drafting. Stronger
models are reserved for ambiguous remediation, architecture changes, policy
changes, and synthesis across conflicting evidence. Human approval remains
mandatory for promotion and destructive actions.

The model that generated a candidate output must not grade itself. Quality
critics and calibration judges must be independent from the candidate generator,
and live profile promotion remains human-gated.

### Harness packaging

Portable skill files are the canonical workbench contract:

- Codex reads `.agents/skills/anvil-serving-workbench/SKILL.md` and optional
  custom agents from `.codex/agents/`.
- Claude Code reads `.claude/skills/anvil-serving-workbench/SKILL.md` and
  optional project agents from `.claude/agents/`.
- OpenClaw can load `examples/openclaw/skills/` through `skills.load.extraDirs`
  until `anvil-serving harness sync openclaw --skills` renders or applies the
  same Anvil-owned entries.

`anvil-serving harness sync openclaw --skills` should become the render/apply
surface for OpenClaw skill and agent configuration. It should keep OpenClaw's
model allowlists, provider config, and Anvil-owned skills in sync with the
router config and documented workflow roles, while preserving operator-owned
config. This is a follow-up to the existing provider/model sync.

## Consequences

- `docs/OPERATOR-SKILLS-AND-SUBAGENTS.md` becomes the canonical workflow design
  for agent-operated anvil-serving.
- Future MCP additions should be justified by workflow usefulness, not by a goal
  of wrapping every CLI option.
- Tool schemas and skill result packets become product contracts. They need
  stable field names, schema versions, redaction rules, and tests.
- Smaller models can do useful work because the product constrains the work:
  inspect, preview, probe, summarize, and assemble evidence. Larger models and
  humans handle the parts where wrong judgment changes routing trust.
- ADR-0013 remains the layering decision. This ADR defines the consumer layer
  above that control plane.
- The OpenClaw plugin remains thin. Skills and sub-agent configuration are
  synced by harness configuration, not by embedding operational logic in the
  per-turn hook plugin.
