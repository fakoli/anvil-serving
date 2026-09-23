# Anvil Serving observations

`@anvil-serving/observations` is a stdlib-only Node library for a trusted
adapter that retains admitted PNG observations and requests a compact visual
inspection. It has no Pi registration, provider configuration, or service
lifecycle behavior.

```js
import { createObservationOwner, reopenObservationOwner } from "@anvil-serving/observations/owner";
import { validatePng } from "@anvil-serving/observations/png";

const owner = createObservationOwner({
  stateDirectory: "/trusted/private/state",
  authorityId: "adapter-authority",
  sessionId: "session-opaque-id",
  modelId: "vision-model-opaque-id",
  profileId: "inspection-profile-opaque-id",
  priorSession: false,
  inspector: async ({ image, question, signal }) => ({
    inspection_status: "observed",
    facts: [],
    reason: "",
  }),
});

const admitted = owner.bind({ entryId: "entry-opaque-id", imagePart: 0, image });
const result = await owner.inspect({ observationId: admitted.observation_id, question: "What is visible?" });
await owner.close();
```

`image` is `{ mimeType: "image/png", data: string }`, where `data` is
canonical base64. `validatePng(data, options?)` returns a frozen object with
the admitted `Buffer` at `png` plus `width` and `height`. The inspector receives
`{ image: { mimeType: "image/png", data: Buffer }, question, signal }`.
`createObservationOwner` requires
`priorSession: false`; `reopenObservationOwner` requires `priorSession: true`
and a cleanly closed, matching prior state.

The state directory is a trusted, private Linux directory with mode `0700`; all
durable state files are mode `0600`. The owner accepts opaque authority, session,
model, and profile IDs. It retains metadata, inspection receipts, tombstones,
and the mediation ledger; it does not create a second durable raw-image store.
Raw image bytes exist only in the live owner and must be rebound after reopening.

Limits are session-wide and conservative: 64 retained lifetimes (active plus
tombstoned), 256 MiB active PNG data, 60 minutes idle retention, and a ledger
budget of 32 attempts, 120 seconds cumulative, and 30 seconds per call. Questions
are at most 512 UTF-8 bytes and mediation envelopes are at most 8192 UTF-8 bytes.
The injected inspector may return at most 32 facts, each with `kind` of
`visible_text` or `visual_fact`, `text` at most 512 UTF-8 bytes, an allowed
uncertainty, and `region_ref: null`; metadata and receipt identity stay
owner-controlled. Admission accepts only canonical-base64, noninterlaced
8-bit RGB or RGBA PNGs containing IHDR, IDAT, and IEND chunks only, at most
8 MiB and 8 million pixels. PNG ancillary chunks are intentionally rejected.

Failures are typed through `ObservationError#code` or error envelopes, including
`budget_state_unavailable`, `budget_exhausted`, `invalid_image`, `image_limit`,
`stale_observation`, `expired_observation`, `cancelled`, `deadline_exceeded`,
`endpoint_unavailable`, `invalid_response`, and `result_too_large`. A crash,
uncertain durable write, unclean ledger lease, missing state, or mismatched
binding fails closed as `budget_state_unavailable`. This package exposes no
generic recovery API. Durable state support is POSIX/Linux-only.

Consumers install the exact reviewed merge commit, for example
`git+https://github.com/fakoli/anvil-serving.git#<reviewed-merge-commit>`. Do
not use a branch, tag, semver range, copied ledger, or an external filesystem
import.
