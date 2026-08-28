# Hermes media generation through Anvil

Hermes can use the Anvil Gateway as a narrow media MCP server. The recommended
baseline launches Anvil's packaged Node 20+ stdio bridge and lets the bridge
connect to the authenticated gateway/controller endpoint. The skill under
`skills/anvil-media/` teaches Hermes the job and artifact flow without exposing
the media worker implementation.

## MCP configuration

Set `ANVIL_MEDIA_MCP_URL` and `ANVIL_CONTROLLER_TOKEN` in the owner-only Hermes
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
      - ANVIL_CONTROLLER_TOKEN
    env:
      ANVIL_CONTROLLER_TOKEN: "${ANVIL_CONTROLLER_TOKEN}"
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

The explicit allowlist keeps operator tools out of Hermes even if the upstream
controller has broader authority. Give the bridge token only `media:read`,
`media:submit`, and `media:cancel` scopes. Do not grant operator or
cross-principal scopes to an ordinary Hermes media profile.

Install `skills/anvil-media/` as a Hermes skill, start a new Hermes session, and
invoke `/anvil-media` with an image or video request. Current Hermes versions
also allow this directory to be supplied as a trusted external skill source.

## Acceptance smoke

A release smoke is complete only when the trace shows all of the following for
the same opaque job and artifact identities:

1. Hermes used the configured stdio MCP server and discovered only the
   allowlisted media tools.
2. The bridge authenticated to the Anvil gateway/controller and preserved the
   modern upstream MCP protocol metadata.
3. A named workflow submission reached the selected managed media worker once,
   with the same idempotency key on any transport retry.
4. Job polling reached a truthful terminal state, artifact inspection returned
   bounded metadata, and authenticated artifact retrieval returned bytes whose
   digest matches that metadata.
5. Negative controls keep unavailable and approval-required work unsubmitted
   and provide no alternate execution path.

The source tree tests exercise this contract with both the legacy SDK shape and
the modern MCP client. A final live smoke still requires a deployed release,
scoped credential, qualified workflow, and the separate live-enablement gate.
