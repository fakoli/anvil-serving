# Hermes media generation through Anvil

Hermes can use the Anvil Gateway as a narrow media MCP server. The recommended
baseline launches Anvil's packaged Node 20+ stdio bridge and lets the bridge
connect to the authenticated router gateway endpoint. The skill under
`skills/anvil-media/` teaches Hermes the job and artifact flow without exposing
the media worker implementation.

## MCP configuration

Set `ANVIL_MEDIA_MCP_URL` and `ANVIL_ROUTER_TOKEN` in the owner-only Hermes
environment. Store only their references in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  anvil-media:
    command: anvil-serving
    args:
      - mcp
      - serve
      - --controller-url
      - "${ANVIL_MEDIA_MCP_URL}"
      - --auth-env
      - ANVIL_ROUTER_TOKEN
    env:
      ANVIL_ROUTER_TOKEN: "${ANVIL_ROUTER_TOKEN}"
    tools:
      include:
        - media_capabilities
        - media_workflow_list
        - media_workflow_show
        - media_workflow_validate
        - media_workflow_run
        - media_job_status
        - media_job_cancel
        - media_artifact_inspect
      resources: false
      prompts: false
```

The explicit allowlist keeps operator tools out of Hermes even if the router's
global catalog is broader. The gateway binds the authenticated Hermes media
principal to `media:read`, `media:submit`, and `media:cancel`; never substitute
the separate lifecycle-controller credential or grant operator or
cross-principal scopes to an ordinary Hermes media profile.

Install `skills/anvil-media/` as a Hermes skill, start a new Hermes session, and
invoke `/anvil-media` with an image or video request. Current Hermes versions
also allow this directory to be supplied as a trusted external skill source.

The managed path installs both the packaged skill and the same scoped MCP
definition without writing secret values:

```bash
anvil-serving harness sync hermes-media --hermes-profiles default --dry-run
anvil-serving harness sync hermes-media --hermes-profiles default --confirm
anvil-serving harness sync hermes-media --hermes-profiles default --dry-run
```

The final preview must report no changes. The image workflow advertises exact
`draft`, `standard`, and `high` profiles; Hermes uses the returned default when
the user does not choose one. Completed job status includes phase and
end-to-end latency. Artifact inspection returns bounded native image content
alongside the authenticated resource metadata, while video remains
resource-only. Native image content is capped at six binary MiB so its base64
envelope remains within the controller and MCP SDK framing limits.

## Acceptance smoke

A release smoke is complete only when the trace shows all of the following for
the same opaque job and artifact identities:

1. Hermes used the configured stdio MCP server and discovered only the
   allowlisted media tools.
2. The bridge authenticated to the Anvil router gateway and preserved the
   modern upstream MCP protocol metadata.
3. A named workflow submission reached the selected managed media worker once,
   with the same idempotency key on any transport retry.
4. Job polling reached a truthful terminal state, artifact inspection returned
   bounded metadata, and authenticated artifact retrieval returned bytes whose
   digest matches that metadata.
5. The selected quality profile and gateway-observed latency are returned, and
   image inspection produces native MCP image content whose decoded digest
   matches the retained artifact.
6. Negative controls keep unavailable and approval-required work unsubmitted
   and provide no alternate execution path.

At a cold approval boundary, `media_workflow_run` returns a server-produced
`resumeBundle`. Hermes must reproduce all seven fields exactly; an incomplete
or abbreviated bundle is canceled instead of being approved or reconstructed
from session history.

The source tree tests exercise this contract with both the legacy SDK shape and
the modern MCP client. A final live smoke still requires a deployed release,
scoped credential, qualified workflow, and the separate live-enablement gate.
