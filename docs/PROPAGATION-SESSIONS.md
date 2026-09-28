# Propagation session observations

Development owner API: `anvil_serving.propagation_sessions`. Run its controlled
checks with `python scripts/run_tests.py tests/test_propagation_sessions.py -q`.
The native bridge execution check additionally requires Node.js 22.13 or newer.
This is not a deployed fleet capability or evidence of live client acceptance.

The installed execution profile supplies each `SessionCheck`: exact target,
installation, profile, runtime, executable digest, native session ID, session kind,
expected model and catalog digests. Its required check inventory is bound by
`session_inventory_digest` to the approved target's `expected_identity_digest`;
`expected_identity_ref` resolves that owner inventory. A changed executable,
profile or session set needs a new approved binding. Targets declare `session-new-loaded` and/or `session-existing-loaded` in
the approved `checks`. Declared kinds missing evidence remain pending; undeclared
kinds are explicitly not-required. Per-kind results retain the bounded pending
reason and observation timestamp. New-session evidence never substitutes for an existing ID.
The profile must independently verify the executable and transport binding; an
HTTP model response alone cannot establish either.

For native Pi Web 0.9.2, the packaged bridge adds a read-only
`GET /api/propagation/session-state?id=<native-id>` route. The optional installer
field `observer_token_env_file` references a protected file supplying
`PI_WEB_PROPAGATION_OBSERVER_TOKEN`. It requires the pinned 0.9.2 source bridge.
The observer token must be distinct from the Workbench mutation token and Web
password. Host/origin checks precede scoped authorization. No credentials are
created, enrolled or installed by the development tests.

The route reads only an already-loaded native RPC session. It does not start a
session, refresh models, enumerate history, read conversations, select providers
or call lifecycle operations. The output contains a bounded identity projection
and digests, excluding endpoints, headers, credentials, prompts and cloud model
identities. A missing or incomplete in-memory catalog remains pending.

Model digests hash the UTF-8 JSON array
`["pi-session-model/v1", provider, id, api, baseUrl, contextWindow, maxTokens]`.
Catalog digests hash `["pi-session-catalog/v1", sortedModelDigests]` for all Anvil
models in the loaded snapshot. These prove the observed routing/limit fields;
file convergence, tool behavior, conversation continuation and other client
settings remain independent checks. The observer bounds strings to 4096 UTF-8
bytes, counts to 16,777,216 tokens and catalogs to 4096 models. Python accepts at
most 64 KiB per response and observations at most five minutes old. Required
session inventories are bounded to 128 checks.

There is no idle reload implementation: an idle snapshot cannot prevent new work
from arriving. CLI and Hermes owners without this observation interface remain
explicitly pending. Every observer reports zero reloads; no active conversation,
account or cloud-provider setting is changed. T009 supplies the native fleet
execution-profile integration; T012 supplies real client continuation checks.
Live installation and acceptance retain their separate authorization gates.

The execution-profile adapter maps detailed reasons to the owner status schema
(`session-acceptance-pending` or `unsupported-capability`) while retaining the
bounded check evidence. These internal projections are not signed receipts.
