# Operator-owned video workflows through MCP

Model choices, variant names, asset fingerprints and deployment qualification
records belong in private operator configuration. The public interface is a
validated named-workflow contract.

The ordinary media connection submits jobs, polls status, cancels work and
inspects owned artifacts. A separately authorized operator connection prepares
and releases a declared worker through its lifecycle transaction. Preserve the
returned cold-start resume bundle; after preparation, replay the identical
workflow request and idempotency key.

```json
{
  "workflow_id": "video.OPERATOR_WORKFLOW_ID",
  "version": "v1",
  "quality_profile": "standard",
  "parameters": {"prompt": "A studio conversation.", "seed": 42},
  "idempotency_key": "episode-01-shot-001"
}
```

The example ID is a placeholder, not an installed workflow. Use the private
registry's actual catalog and input schema. Resolution, duration, model files,
node permissions and resource envelopes remain operator-controlled.

An artifact input accepts only a bounded, unexpired caller-owned artifact with
the descriptor's permitted media type. Its bytes and hash are checked before
upload; a worker filename is substituted only into the private execution graph.
Caller input never supplies an arbitrary path, graph or backend route.

Install clients in versioned environments so a running Windows executable does
not need in-place replacement. Keep ordinary and operator authentication in
protected secret files. A lifecycle client may supply the approved
`ANVIL_MCP_EXECUTION_CONTEXT` object containing `topology`, `execution_host` and
`execution_runtime`. Confirmed calls receive independent HTTP idempotency keys.

Use the supported [Windows memory guard](cli/media.md) when required by the
private worker profile. Preserve one stable config path, fresh safe signals,
exclusive GPU admission, and the exact reviewed dependency mounts. Lifecycle
health remains authenticated; gateway liveness must work with the worker stopped.
Offline flags do not establish network isolation.

After all dependent jobs terminate, release only the worker owned by the
matching lifecycle transaction. Export desired artifacts before their configured
retention expires. Keep the source audio master authoritative: a video writer
may re-encode its soundtrack, and audio fidelity does not establish lip sync.

See [Anvil Media operations](cli/media.md) for the supported tools and commands.
