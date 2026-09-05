# Router request diagnostics and remote endpoint research

Status: implemented; independent adversarial reviews passed
Priority: P1
Scope: public product; source changes only

## Problem

The gateway already has a bounded DecisionLog, request lookup, readiness,
capacity, and buffer metrics. Ordinary clients do not receive a dependable
correlation identifier, overall latency does not distinguish response startup
from completion, and chat accounting can estimate input tokens even when the
upstream supplies usage. Operators must assemble separate API calls manually.
Some front-door exception logging also formats raw backend exceptions, which
can disclose content or endpoint details while investigating a failure.

## Action plan

1. Supply bounded request correlation across client, gateway decision evidence,
   and the selected upstream; preserve protocol bodies and exact route choice.
2. Record content-free phase timing, normalized termination, output limits, and
   upstream versus estimated usage. Unknown measurements remain unknown.
3. Add a read-only `router diagnose` command that retrieves one terminal record,
   identifies evidence-based next checks, and distinguishes current build
   metadata from historical request evidence. It never replays a request.
4. Research a portable private remote inference endpoint with Tailscale and
   publish the supported configuration path and provider-specific prerequisites.
5. Exercise real loopback HTTP/SSE with synthetic backends, regression tests,
   documentation and package checks, then independent adversarial review.

## Concurrent work

The qualified-replica-sets, replica-capacity-scheduler, fleet-node-enrollment,
and workload-visibility PRDs have their own active implementation campaign.
This work complements those contracts; it does not create a second scheduler,
fleet registry, or active-workload store. Cross-host replica selection remains
a separate design decision because the current replica PRD is same-host.

## Boundaries

No prompt/response/tool payload capture, inferred intent, alternate-model
fallback, retries, cloud purchases, live tailnet enrollment, or deployment.
Public source publication and private live acceptance remain separate gates.
Every review defect will be recorded here and fixed before merge.

## Validation and review

Independent review passed after all confirmed defects below were fixed. Source
merge remains gated on the full regression suite and the current commit's CI.

Confirmed review defects and fixes:

- Closing a stream before its first iteration leaked the optional concurrency
  semaphore even though admission was released. An explicit closing iterator
  now owns both upstream cleanup and permit release. The regression proves
  permit reacquisition, exactly one close, and successful subsequent inference.
- A structured-result callback exception produced no terminal decision. The
  completion boundary now records a content-free failure, clears stale response
  metadata, and releases admission exactly once.
- Caller JSON could forge `_anvil_output_clamp`. Every routing invocation now
  removes the marker before deriving its own output cap and measurements.
- Generated-shaped IDs could fall through to caller-ID lookup after eviction.
  The generated namespace now requires an exact gateway-ID match.
- Default relay transports followed redirects and formatted upstream exception
  reasons. They now disable redirects/proxies, close HTTP errors, and log bounded
  classes/status/IDs. Hostile-response and redirect-receiver tests cover this.
- The first bundle draft accepted arbitrary launch arguments without proving
  its loopback/revision claims. A narrow vLLM adapter now constructs those flags;
  a strict canonical argument allowlist prevents alternate spellings and config
  overrides, vLLM auth uses its actual environment variable, and the adapter
  owns its cache path and launches the advertised context. Tool support requires
  explicit parser flags. Invalid/deep/oversized JSON and unknown field names do
  not leak manifest content.
- Malformed HTTP status lines escaped the diagnostic transport boundary. They
  now become content-free typed transport errors. The diagnostic client also
  enforces the generated-ID namespace and discards impossible phase/clamp values.
- Invalid non-text backend deltas could break error accounting and lose the
  terminal record. The relay loop now enforces its text-fragment protocol before
  storing or emitting each delta.
- Partial token usage was discarded by production relay parsers, while
  interrupted content was counted as unknown zero. Per-field usage is retained;
  observed interrupted text is estimated. Aggregate source counts distinguish
  reported, estimated, and unknown records.
- Purpose/audio responses initially had generated headers without corresponding
  terminal IDs. Their decision and upstream seams now carry the same generated
  ID, keeping legacy caller lineage separate.
- Embedding/reranking upstream caller errors escaped the purpose boundary as
  generic 500s without terminal records. Real HTTP regressions now preserve
  400/413/415/422 and exactly one correlated failure; unexpected transport
  exceptions become sanitized 502s with terminal evidence.
- Separate OpenAI SSE usage events replaced earlier token fields. The assembler
  now merges independently validated fields, retaining reported input/output
  counts across split usage events and ignoring later invalid counts.
- The bundle's required manifest flag was absent from declarative command help
  and the machine manifest. Both actions now declare it and have help/schema
  regressions. Repository model IDs reject invalid endings and repeated dots or
  hyphens before rendering; actual repository access remains unproven offline.
- Tailnet hostnames reject a trailing hyphen. Container references validate
  Docker repository components, rejecting traversal, empty paths, invalid
  separators, and unsupported registry-port syntax instead of normalizing a
  reviewed image name during launch.
- The public JSON CLI envelope echoed manifest paths and rejected router URLs
  in its command label. The new diagnostic and bundle leaves now identify only
  the canonical command; full-dispatch tests prove sensitive operands are not
  copied into their result envelopes.
- The PR's automated review reproduced two further cases after the first local
  passes: incomplete/misspelled command families still echoed operands, and an
  AWS-shaped authentication reference rendered a router-invalid fragment.
  Router/edge command labels now omit operands throughout the family; unresolved
  error envelopes drop raw stderr and unexpected-argument details while keeping
  declared actions and the stable error code. New metadata-leaf parser errors
  are content-free. Bundle references now use the canonical router auth-name
  validator, with content-free error projection. Regressions cover missing and
  misspelled leaves/groups, parser failures, and both credential-shaped prefixes.
  Follow-up review also caught conflicting global output flags failing before
  dispatch; that error now labels only the application, never raw arguments.
- Review found a pre-existing tool-only SSE response-size bypass. The
  [streaming cap ticket](2026-09-05-streaming-response-byte-cap.md) tracks the
  raw-byte bound and independent verification.
- Explicit OpenAI/Anthropic SSE provider-error events were ignored and partial
  output could appear served. Both parsers now raise a content-free error that
  produces one failed terminal record and closes the response. Unexpected
  audio transport exceptions likewise become sanitized 502s with one record.
- Clean EOF without the protocol's terminal event also appeared successful.
  Streaming now requires OpenAI `[DONE]` or Anthropic `message_stop`. Deep STT
  JSON and malformed audio response metadata fail through the same recorded
  error boundary instead of losing terminal evidence.
- Purpose records contain an unmeasured legacy zero latency. Diagnosis now
  reports null for embedding/reranking timing, and documentation distinguishes
  that field from measured chat/audio latency.
- Research exposed an older Tailscale mount-prefix error. The
  [edge prefix ticket](2026-09-05-edge-v1-prefix.md) records the bounded source
  correction and explicit migration behavior.

The reviewer withdrew the proposed global missing-credential rejection after
checking the supported optional-auth local-tier contract. A protected
`/v1/models` endpoint rejects missing credentials in the real HTTP regression.
Anonymous health success alone is not evidence that an inference endpoint
enforces authentication; documentation states this distinction.

The diagnostic integration regression traverses a real loopback front door,
the routing backend, a real HTTP relay, and a synthetic model server before
looking up and diagnosing the returned generated ID. It verifies exact upstream
usage, a 64-to-32 output cap, no replay, and no retained payload content.

Final independent dispositions:

| Reviewer | Scope | Result |
| --- | --- | --- |
| `adversarial_measurements` | Timing, provenance, admission cleanup, request lookup, diagnostic evidence | Pass; 72 focused checks plus independent failure probes. |
| `adversarial_correlation` | Front door, relay/SSE, purpose/audio, terminal records, documentation | Pass; 180 focused tests and independent real HTTP/protocol reproductions. |
| `adversarial_bundle` | Strict bundle, actual JSON CLI envelope, authenticated readiness, existing edge correction | Pass; 120 initial checks, then 642 focused checks after PR findings and the global-option fix, plus independent Compose/source probes. |

All reviewers were distinct from the authors of their reviewed code and made
no implementation edits. Ruff, 517 tracked Markdown link checks, strict MkDocs,
CLI manifest/audit, semantic public-snapshot hygiene, wheel/sdist metadata, and
clean wheel installation passed. The new command help, offline validation, and
rendering also passed from a fresh dependency-free wheel installation outside
the checkout. Full-suite and current-commit CI results are recorded in the
source PR before merge. No package or live deployment is included.
