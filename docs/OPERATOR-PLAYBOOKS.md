# Operator playbooks

Use the control plane to operate named local serves and direct capability aliases. The router
does not infer intent, choose between models, or promote benchmark results.

## Read-only inventory

1. Inspect the router and serving substrate:

   ```bash
   anvil-serving doctor --config <router.toml>
   anvil-serving router status
   anvil-serving serves status --manifest <serves.toml>
   ```

2. Confirm the caller alias in `[router.model_routes]` maps to the intended local tier.
3. Inspect `GET /v1/models` and `GET /v1/decisions` through the authenticated router when
   diagnosing discovery or request metadata.

The MCP equivalents are `doctor_summary`, `router_status`, `serves_status`,
`reservation_status`, and `decision_summary`.

## Start, validate, and benchmark a serve

1. Preview the exact lifecycle operation, then repeat with explicit confirmation:

   ```bash
   anvil-serving serves up <serve-name> --manifest <serves.toml> --dry-run
   anvil-serving serves up <serve-name> --manifest <serves.toml> --confirm
   ```

2. Run functional preflight directly against the served model:

   ```bash
   anvil-serving eval preflight \
     --base-url http://127.0.0.1:<port>/v1 \
     --model <served-model> --confirm
   ```

3. Record benchmark evidence only after preflight passes. Publish a dated finding and raw
   artifact link when the outcome changes a current recommendation or reference deployment.

4. Promote or roll back only through the guarded `serves promote` transaction with explicit
   human approval. A benchmark never updates an alias or serve automatically.

## Voice topology

In the reference topology, Fakoli Dark owns LLM, STT, and TTS serves. Fakoli Mini owns the
OpenClaw Gateway and realtime proxy only. Use `voice audio` and `voice proxy` commands with an
explicit topology file; loopback addresses are host-relative.

## OpenClaw sync

Render or apply OpenClaw provider configuration through `harness sync openclaw`. The generated
provider names the router base URL, a token environment variable, and a direct model alias.
Do not use a decision endpoint or plugin classifier.

## Evidence and stop conditions

Keep the following together for a serve decision: manifest/config identity, health, preflight
output, benchmark artifact, failures, and rollback plan. Stop and ask for direction when the
target alias, local tier, topology owner, or promotion authority is ambiguous.
