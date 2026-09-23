# Read-only browser pilot adapter

`@anvil-serving/observations/browser` exports `createLiveObservationAdapter`.
The dependency-free package requires Node.js 22+. The trusted harness owns its
pinned browser dependency, executable, launch timeout and process cleanup.
Importing this package does not launch a browser or enable a Pi extension.

```js
import { createLiveObservationAdapter } from "@anvil-serving/observations/browser";

// launch is a trusted harness function returning a Playwright Browser.
const adapter = await createLiveObservationAdapter({
  launch,
  piSessionId: "session-1",
  pages: [
    { id: "home", url: "https://example.com/" },
    { id: "projects", url: "https://example.com/projects/" },
  ],
  jev: { enabled: false },
});
try {
  const receipt = await adapter.execute({
    operation: "capture", page_id: "projects",
    request: {
      schema: "widget-resolution/v1", request_id: "request-1",
      target: { description: "Projects heading", qualifiers: [] },
      predicates: ["exists", "in_viewport", "occluded", "enabled"],
      scope: { kind: "document", root: "document" },
      require_unique: false, entity_offset: 0,
    },
  }, { signal }); // optional AbortSignal supplied by the harness
  if (receipt.status === "ok") {
    const observation_id = receipt.result.observation_id;
    const candidate = receipt.result.entities[0];
    if (candidate) await adapter.execute({
      operation: "resolve", observation_id, entity_id: candidate.id,
    });
    await adapter.execute({ operation: "release", observation_id });
  }
} finally {
  await adapter.close();
}
```

Success envelopes contain `schema: "browser-owner-adapter/v1"`, `status: "ok"`,
`operation`, `binding: { pi_session_id, owner_session_id }`, `page_id` and `result`.
Refusals contain only the same schema, `status: "refused"` and a bounded `code`,
for example `unknown_observation`. Successful envelopes are limited to 15 KiB;
requests to 16 KiB. No screenshot bytes or paths enter these receipts.

Configuration allows one or two exact canonical HTTPS URLs on one origin.
Requests select their configured page ID; they cannot supply URLs, browser
settings or export policy. The adapter offers capture, resolve, release and
`{ operation: "jev_resolve", observation_id }` only. It exposes no interaction
or arbitrary script tool. Same-page capture creates a fresh observation without
navigating. Changing pages invalidates previous adapter references. Subtree
capture requires a previously offered same-page `observation_id:entity_id` root.

Captures return eight candidates at a time. Use `result.paging.next_offset`
when present as the next request's `entity_offset` (0–2047). Each page is a new
observation; omissions and incomplete coverage remain explicit. Combining pages
cannot prove global absence or uniqueness. Resolving an offered candidate checks
owner freshness and returns DOM facts; it does not establish a semantic match.
Disabled controls can match with `enabled: false`.

Jev is off unless trusted configuration explicitly enables it. Its configuration
includes `enabled`, the exact `origin`, and `fields` in this order:
`schema`, `request_id`, `observation_id`, `source`, `target`, `scope`, `coverage`,
`entities`. Trusted `executable`, `cwd` and `timeout` may select the existing
project-aware Anvil command. The owner invokes `anvil jev evaluate` through its
bounded subprocess consumer; it does not duplicate credentials or a provider
client. Jev selects an offered candidate or abstains. The owner checks freshness
afterward. Advice never authorizes an action or proves absence or uniqueness.

## Bounds and remaining gates

The transport admits GET requests without request credentials, redirects or
compressed responses. It permits only configured document URLs and same-origin
assets, checking public DNS on each fresh connection. Limits are 64 requests,
four concurrent requests, 2 MiB per response, 12 MiB aggregate admitted chunks,
16 KiB response headers, five seconds per fetch/DNS and a 120-second transport
lifetime. Service workers, WebSockets, popups and downloads are blocked.

These are application admission, retention and forwarding bounds. Node socket
and TLS buffers can receive additional bytes before admission. The strict
encoded wire-byte quota for general production remains an open gate; this pilot
is not evidence that gate passed. Dedicated-worker or WebRTC isolation and
kernel-level network quotas are not claimed.

The owner retains at most 64 records/256 MiB, with an 8 MiB/8-megapixel PNG limit,
64 candidates per owner capture, a 2048-node DOM scan and 8 KiB metadata limit.
The live adapter uses eight-candidate pages. Operations are serialized with a
`busy` refusal; cancellation closes the owner and discards late results.
The trusted harness must enforce startup and forced process teardown deadlines.

Local fixtures and fake-provider checks validate contracts, not live Jev quality.
Run `npm test --prefix browser_owner` and the native Pi fixture probe
`node tests/computer_use/pi_browser_owner_probe/probe.mjs` from the repository.
Installed Pi lifecycle, exact-runtime Chromium cleanup and independent live
site receipts must pass separately before pilot enablement. Vision qualification,
model promotion and browser actions are outside this adapter's acceptance.
