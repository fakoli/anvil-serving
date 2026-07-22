---
title: "anvil-serving x OpenClaw — current integration contract"
date: 2026-07-22
status: current-contract
verdict: supported-with-caveat
---

# OpenClaw integration contract

OpenClaw integrates with anvil-serving through three deliberately separate layers:

1. the checked-in `before_model_resolve` plugin selects a native OpenClaw route or an Anvil
   intent preset for each run;
2. the protocol-standard Anvil router owns tier selection, quality verification, fallback, and
   inference; and
3. Anvil's CLI, MCP server, and controller own explicit setup and operations.

Use `anvil-serving harness sync openclaw` to render or apply the integration. Do not hand-merge a
provider fragment when the sync command can own the same keys.

The integration is supported with one load-bearing caveat: after the plugin returns
`providerOverride: "anvil"`, OpenClaw's native fallback walk does not reliably escape that provider
if Anvil later exhausts. Keep correctness fallback inside the Anvil router, move known-risk classes
to the plugin's native route, or explicitly configure an Anvil cloud tier. See
[ADR-0005](adr/0005-anvil-503-native-failover-unreliable.md).

The architecture and ownership decision is [ADR-0013](adr/0013-openclaw-layers-and-mcp-control-plane.md).
Day-to-day procedures are in [Operator Playbooks](OPERATOR-PLAYBOOKS.md#playbook-e-sync-openclaw-config).

## 0. Hook contract and cadence

The reference plugin lives at `plugins/openclaw-anvil-intent-router/`. Its OpenClaw-facing contract
is:

```ts
type Attachment = {
  kind: "image" | "video" | "audio" | "document" | "other";
  mimeType?: string;
};

type BeforeModelResolveEvent = {
  prompt: string;
  attachments?: Attachment[];
};

type BeforeModelResolveResult = {
  modelOverride?: string;
  providerOverride?: string;
};

type BeforeModelResolveContext = {
  modelProviderId?: string;
  modelId?: string;
  runId?: string;
  sessionKey?: string;
};
```

The handler receives `(event, ctx)` and is registered as
`api.on("before_model_resolve", handler, { priority: 50 })`. OpenClaw
fires it once per run, above the provider/model attempt loop. It does not fire again for a
`before_agent_finalize` revise retry. For a normal chat bridge, one run corresponds to the user turn
that needs routing.

The event contains the current prompt and attachment metadata, not the full conversation history.
The plugin therefore performs deterministic Tier-0 classification; it is not a full-context judge.

The plugin must be enabled under its packaged id and granted conversation access:

```jsonc
{
  plugins: {
    entries: {
      "openclaw-anvil-intent-router": {
        enabled: true,
        hooks: { allowConversationAccess: true }
      }
    }
  }
}
```

The hook does not mutate prompts and does not require `allowPromptInjection`. Its outer error guard
returns `{}` on unexpected failure so plugin classification, route lookup, or decision logging
cannot break an OpenClaw run.

## 1. Per-run routing behavior

The plugin first honors an explicit trusted Anvil model in OpenClaw's resolved context, then uses
the shared Tier-0 keyword taxonomy plus prompt/attachment pressure signals. Context pins the model
when `ctx.modelProviderId` is `anvil` or `ctx.modelId` is an `anvil/<preset>` reference, and the
resolved suffix belongs to the plugin's closed preset set. Unknown or non-Anvil context does not
pin routing. `runId` and `sessionKey` are used only for metadata-only decision correlation.

Automatic classification emits `planning`, `quick-edit`, `review`, `chat`, or `long-context`.
`chat-fast` remains available for an explicit OpenClaw runtime selection and for voice consult
configuration; the heuristic does not select it automatically.

Classification precedence is deterministic: a prompt of at least 24,000 characters or at least
four attachments selects `long-context`; otherwise any image, video, audio, or document attachment
selects `review`; then the shared keyword classes apply in review, planning, multi-file-refactor,
and bounded-edit order; anything else selects `chat`. Multi-file refactors map to `review`, and
bounded edits map to `quick-edit`.

The routing result has two possible shapes:

```ts
// Local-preferred run
{ providerOverride: "anvil", modelOverride: "quick-edit" }

// Cloud-preferred run, using the configured native route
{ providerOverride: "openai", modelOverride: "gpt-5.6-sol" }
```

The bare Anvil preset in `modelOverride` is required. Returning only
`modelOverride: "anvil/quick-edit"` was live-confirmed to mis-resolve in OpenClaw. The provider and
model components must be separate.

`planning` is the default cloud-preferred class. Operators can replace the cloud-class set through
plugin config or `ANVIL_CLOUD_CLASSES`; a non-empty environment value takes precedence. Production
setup must name the gateway's real native provider and model through plugin config or
`ANVIL_NATIVE_PROVIDER` plus `ANVIL_NATIVE_MODEL`. Keep a native OpenClaw model as
`agents.defaults.model.primary` so disabling or failing the plugin preserves a usable route.

### Authoritative route lookup

When no explicit trusted Anvil preset was resolved, a configured `routeEndpoint` or
`ANVIL_ROUTE_ENDPOINT` makes the plugin call Anvil's `POST /v1/route` before selecting local versus
native. Explicit Anvil context always pins Anvil and skips the route probe; this includes Talk
consults resolved as `anvil/chat-fast`. `harness sync openclaw` enables authoritative lookup by
default and derives `<base-url>/route`.

The route probe is bounded by `routeTimeoutMs` or `ANVIL_ROUTE_TIMEOUT_MS` (1–5000 ms). For an
authenticated router, `routeAuthEnv` or `ANVIL_ROUTE_AUTH_ENV` names the environment variable that
contains the bearer token; plugin config never contains the token value. Environment settings take
precedence over plugin config.

An unreachable, unauthorized, timed-out, or malformed route response degrades to the deterministic
client-side decision. It does not fail the user run. Decision-log records distinguish
`anvil-route`, `client-side`, `client-side-fallback`, and explicit `openclaw-context` routing.

For a split-host deployment, the route URL must be reachable from the OpenClaw gateway. From
Fakoli Mini, `127.0.0.1` means Mini, not the Dark router host.

## 2. Provider and plugin setup

### Supported setup command

For a fresh gateway, provide the native route, the absolute plugin path as seen by that gateway,
and explicit tool/exec policy:

```bash
anvil-serving harness sync openclaw \
  --config configs/example.toml \
  --base-url http://100.87.34.66:8000/v1 \
  --native-provider openai \
  --native-model gpt-5.6-sol \
  --plugin-dir /absolute/path/to/openclaw-anvil-intent-router \
  --tool-profile full \
  --exec-mode auto \
  --out ~/.openclaw/openclaw.json \
  --restart
```

The sync command:

- renders one `anvil` provider model for each configured router preset;
- keeps the native model as OpenClaw's primary;
- adds every `anvil/<preset>` to `agents.defaults.models`, which is OpenClaw's model-picker
  allowlist;
- enables the plugin, grants `allowConversationAccess`, and adds its absolute load path;
- configures the authoritative route endpoint and an environment-backed token name;
- merges only Anvil-owned keys by default and preserves unrelated provider, plugin, and tool policy;
- runtime-validates fresh, overwrite, or incomplete-integration applies made to the real default
  config path; and
- attempts backup and rollback/removal around those validated applies and surfaces any failure.

Fresh writes and overwrites fail closed when the native route, plugin path, or explicit tool/exec
policy needed for a complete setup is absent. Existing-config merges may omit operator-owned values
that are already present and valid. Use `--overwrite` only for an intentional replacement.

For a remote gateway, use `--gateway-host <host>` and a plugin path absolute on that remote host.
OpenClaw reads config at gateway startup, so apply with `--restart` or restart separately with
`anvil-serving harness restart openclaw`.

Ordinary merges into an already complete config are not automatically runtime-validated. Inspect
the plugin runtime and gateway status after every apply. Backups and rollback are best-effort safety
mechanisms: a filesystem or remote-transport failure is reported but cannot guarantee restoration.

On OpenClaw 2026.6.11 and newer, compiled/TypeScript plugins require a linked install. When
installing outside harness sync, use:

```bash
openclaw plugins install --link /absolute/path/to/openclaw-anvil-intent-router
openclaw gateway restart
openclaw plugins inspect openclaw-anvil-intent-router --runtime --json
```

A plugin path loads executable code into the gateway with conversation access. Link only an
operator-trusted, reviewed, pinned checkout on that host, and satisfy the gateway's
`security.installPolicy` plus `plugins.allow`/`plugins.deny` policy before enabling it. Manifest and
runtime inspection prove the packaged id and load state, not source provenance or code integrity.
Harness sync does not bypass OpenClaw's install policy or grant operating-system trust to an
unreviewed path.

Treat the configured route endpoint as a trusted private router: every authoritative probe sends
current prompt content and, when auth is configured, the resolved router token. Likewise,
`--tool-profile full --exec-mode auto` is an intentional high-trust operator policy, not a neutral
default; select and review it for the gateway rather than copying it blindly.

### Generated provider shape

The rendered config uses `models.mode: "merge"`, provider id `anvil`, and
`api: "openai-completions"`. The essential shape is:

```jsonc
{
  models: {
    mode: "merge",
    providers: {
      anvil: {
        baseUrl: "http://100.87.34.66:8000/v1",
        apiKey: "${ANVIL_ROUTER_TOKEN}",
        api: "openai-completions",
        models: [
          {
            id: "chat",
            name: "Anvil · Chat",
            reasoning: true,
            input: ["text"],
            contextWindow: 131072,
            maxTokens: 8192
          }
        ]
      }
    }
  },
  agents: {
    defaults: {
      model: { primary: "openai/gpt-5.6-sol" },
      models: { "anvil/chat": {} }
    }
  }
}
```

The command generates the complete model list; the abbreviated example is explanatory, not a
hand-maintained replacement.

### `contextWindow` rule

For each preset, `contextWindow` must equal the largest `context_limit` among every tier that preset
can route to. A preset that normally starts on a 32K Fast tier but can fall back to a 128K Heavy tier
must declare 128K in OpenClaw.

This is a caller-budget contract, not display metadata. OpenClaw derives its completion budget from
the declared window. An understated value can collapse `max_completion_tokens` to one after the
conversation grows past that window. The model then correctly returns a caller-capped one-token
response. Anvil v0.7.1 and newer no longer misclassifies a non-empty caller-capped response as
unexpected truncation, but the understated OpenClaw declaration still starves the turn.

`harness sync openclaw` computes this maximum from the router config. Re-run sync whenever preset
pools or tier context limits change.

### 2a. OpenClaw Talk and Anvil Voice

Text routing and Realtime voice use separate OpenClaw registries. Add `--voice` only when OpenClaw
Talk should use Anvil Voice:

```bash
anvil-serving harness sync openclaw \
  --config configs/example.toml \
  --base-url http://100.87.34.66:8000/v1 \
  --voice \
  --voice-realtime-url ws://127.0.0.1:8765/v1/realtime \
  --voice-consult-model anvil/chat-fast \
  --out ./openclaw.anvil.json
```

Loopback is relative to the gateway host. In the reference topology, Mini runs OpenClaw Gateway and
the Anvil Voice Realtime/proxy process but no STT, TTS, or LLM models. See
[Voice pipeline](VOICE.md) for topology and token requirements.

## 3. Preset and model-id contract

- OpenClaw model references use `anvil/<preset>` in configuration and selection surfaces.
- The plugin returns `providerOverride: "anvil"` with a bare preset `modelOverride` on the hook
  wire.
- Anvil accepts both bare presets and `anvil/<preset>` for robustness.
- `GET /v1/models` advertises Anvil's canonical intent vocabulary, not arbitrary custom preset keys
  from one router config. The inline provider `models[]` rendered by harness sync is the
  configuration-specific OpenClaw authority and the required path for custom presets.
- Every selectable or plugin-emitted preset must appear in both provider `models[]` and
  `agents.defaults.models`; the latter is OpenClaw's allowlist.
- Empty `agents.defaults.models["anvil/<preset>"]` objects are intentional. Per-tier reasoning,
  thinking, and sampling defaults belong in router tier configuration rather than duplicated
  OpenClaw params.
- The plugin's automatic classifier and router share the canonical Tier-0 keyword taxonomy. The
  plugin bundles a byte-identical copy because it cannot read the Python package after standalone
  installation; parity tests reject drift.

## 4. Ownership boundaries

| Responsibility | Owner | Contract |
|---|---|---|
| Per-run intent adaptation | OpenClaw plugin | Select native or `{ anvil, bare-preset }`; never break a run. |
| Intent/tier policy | Anvil router | Resolve presets, hard constraints, quality profile, residency, and billing gates. |
| Structural verification and same-run fallback | Anvil router | Buffer where required, verify independently, and walk configured tiers. |
| Provider/model config | `harness sync openclaw` | Render and merge Anvil-owned gateway keys; back up and validate applies. |
| Explicit operations | Anvil CLI/MCP/controller | Status, route probes, gateway sync/restart, serve/router/voice lifecycle, and evidence. |
| Product logic and native-provider fallback | OpenClaw | Outside the Anvil router core; subject to ADR-0005's provider-override caveat. |

The plugin may observe or log its routing decision. It does not verify model output, restart
gateways, manage serves, promote profiles, or proxy inference. The router core imports no OpenClaw
SDK and remains usable by every supported HTTP harness.

OpenClaw's model fallbacks handle transport-class failures, not Anvil quality verdicts. Its
`llm_output` and `model_call_ended` hooks are observational, and `before_agent_finalize` can request
a bounded retry but cannot replace the already served response with output from another provider.
There is no response-swap hook. Quality verification and same-run cross-tier fallback therefore
belong in the router that sees and controls the model response.

MCP/controller operations are control-plane calls, not a replacement for the per-run hook or the
router data plane. See [ADR-0013](adr/0013-openclaw-layers-and-mcp-control-plane.md).

## 5. Operate the integration

After a router preset, tier context window, model id, route URL, token env name, plugin build, or
OpenClaw version changes:

1. preview `harness sync openclaw` against the intended router config and gateway-visible URLs;
2. apply the merge to the exact gateway target and restart OpenClaw;
3. inspect the plugin runtime and gateway status;
4. run the deterministic COLO fixture;
5. run the live Mini-to-Dark smoke when that environment is in scope; and
6. retain the artifact whenever the result will support a release or recommendation.

```bash
openclaw plugins inspect openclaw-anvil-intent-router --runtime --json
anvil-serving harness status openclaw --gateway-host fakoli-mini
python examples/openclaw/colo_smoke.py \
  --fixture \
  --artifact .anvil/evidence/openclaw-colo-fixture.json \
  --pretty
```

The live smoke and interaction benchmark are documented in
[Operator Playbooks](OPERATOR-PLAYBOOKS.md#playbook-f-openclaw-colo-smokeeval). Direct-router
generation probes prove gateway-to-router reachability and router behavior; they do not by
themselves prove OpenClaw's full provider attempt loop.

The plugin writes metadata-only JSONL decisions. A logging failure is non-fatal. The committed
`decision_log.fixture.jsonl` is synthetic and marked `"synthetic": true`; it is generated by the
same classifier and routing functions as the plugin and is not a live capture.

## 6. Upgrade and validation gate

Re-run the live OpenClaw gate after upgrading OpenClaw, changing the plugin API compatibility floor,
changing provider config, or changing routing behavior. A valid proof establishes:

1. the plugin is loaded under id `openclaw-anvil-intent-router` with its hook active;
2. `before_model_resolve` fires once per run at the expected cadence;
3. a cloud-preferred `planning` turn resolves to the configured native provider/model and does not
   contact Anvil for generation;
4. a local-preferred turn resolves to provider `anvil` with a bare preset id and reaches the Anvil
   endpoint;
5. authoritative route lookup uses the configured auth environment and falls back to the
   client-side classifier on bounded failure;
6. provider entries and `agents.defaults.models` contain every configured preset;
7. every generated `contextWindow` equals the maximum reachable tier window; and
8. the decision artifact identifies source, preset, destination, override, routing source, and
   authoritative status without prompt content or secrets.

The historical `examples/openclaw/validate.py` aggregate wire check currently conflates the
plugin/configured vocabulary with optional router-global presets; that separate tooling defect is
tracked in [issue #287](https://github.com/fakoli/anvil-serving/issues/287). Until it is fixed, do
not cite its aggregate result as a passing current gate. The plugin and harness contract tests remain
the automated proof, and a real gateway capture remains the live proof.

Unit tests and the synthetic fixture prove deterministic adapter behavior. They do not replace the
live gateway/provider proof. Likewise, a successful Anvil 503 proves the router's exhaustion
contract, not successful handoff to a native OpenClaw provider.

Remaining version-sensitive questions are limited to OpenClaw-owned surfaces: plugin API floor,
`definePluginEntry` packaging, provider timeout/config names, and future runtime restrictions on
plugin environment access. A change in any of those surfaces is isolated to the adapter and sync
layer; it does not change the Anvil router protocol.

## 7. History and known caveats

This appendix records why the current rules exist. It is not a second setup guide.

### 2026-06-30 — hook cadence and wire form

Live validation established that the hook fires once per run above the attempt loop, and that the
working local route is `providerOverride: "anvil"` plus a bare preset `modelOverride`. Those facts
are now pinned in the plugin, validation tooling, and §0/§3 above.

### 2026-07-01 — native fallback does not escape the override

An Anvil exhaustion 503 correctly triggered OpenClaw's transport-fallback category, but fallback
attempts still resolved through provider `anvil` after the hook had returned an Anvil provider
override. The native provider was not reached.

There is no Anvil-router status-code fix for OpenClaw's provider-resolution behavior. The supported
mitigations are:

- classify an at-risk preset as native/cloud-preferred before it contacts Anvil; or
- explicitly bind an Anvil cloud tier for the permitted work classes so fallback completes inside
  the Anvil request.

See [ADR-0005](adr/0005-anvil-503-native-failover-unreliable.md) and the public
[keyless-failover finding](findings/2026-07-04-openclaw-keyless-failover.md).

### 2026-07-02 — understated context window starved completion

A provider entry declared the Fast-tier window even though the preset could fall back to a larger
Heavy tier. Once a real conversation exceeded the declared window, OpenClaw reduced the completion
budget to one token. This produced caller-capped output and, before Anvil v0.7.1, cascading
verification failures. The durable setup rule is the maximum-reachable-tier window in §2.

### 2026-07-05 — integration layers became product contracts

The plugin adapter, router data plane, and MCP/controller operations were formalized as separate
layers in [ADR-0013](adr/0013-openclaw-layers-and-mcp-control-plane.md). Remote gateway application
remains an explicit transport fallback under [ADR-0014](adr/0014-tailnet-controller-transport.md),
not permission for arbitrary SSH automation.
