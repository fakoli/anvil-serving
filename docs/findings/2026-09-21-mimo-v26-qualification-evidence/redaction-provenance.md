# Public redaction provenance

The public JSON files retain their native schemas, metrics, failures, completion fields, model identifiers, source URLs, revisions, and image digests.

`gpus-final.json` is historical public command output despite its `.json`
suffix. `artifact-manifest-source.json` declares it in
`legacy_plaintext_files` with this reason before the generated manifest hashes
it; no filename receives an implicit malformed-JSON exception.

The transformation replaces operator host labels with `Primary Node`, absolute operator and checkout paths with public/private role labels, the baseline loopback port with `<redacted-port>`, GPU UUIDs with `GPU-REDACTED`, and every container ID, including IDs inside status-log or nested-string payloads, with `REDACTED_CONTAINER_ID`. It removes no metric, failure, or request result. The retained startup logs are sanitized raw failure output; option names are preserved, but no credential values, environment files, private endpoint identities, cache paths, or active listener details were copied.

`router-final-status.json` retains its envelope shape but replaces active tier,
model-assignment, and readiness state with `REDACTED_ACTIVE_ROUTER_STATE`.
The independent direct and routed probe artifacts retain the bounded pass/fail
outcomes used by the restoration claim; the redacted router snapshot does not
claim that a particular tier is active.

The public recipe is a reconstruction, rather than a transformed operator recipe, because the private source contains an operator cache snapshot path and listener/containment implementation. Values absent from the reconstruction are intentionally not recorded here.

`mimo-v2-logs.log` is the retained stdout-only capture and contains only the containment marker. The candidate had already exited when the complete container log could have been collected. `mimo-v2-tool-transcript.txt` is the recovered stderr excerpt from the actual campaign tool transcript; [its provenance](mimo-v2-capture-provenance.json) records source type, capture scope, limitation, and source SHA-256. It is not represented as a complete container log.
