# Optional Jev assistance

```bash
anvil-serving workbench jev status
```

Jev is off by default. Installing or upgrading Serving does not enable it,
read a TypeSafe credential, start Anvil, or send selected content. These
experimental annotations never authorize operations, approve tasks, change
providers, select serving routes, or establish benchmark success.

Serving reuses the versioned `anvil jev bridge --json` local executable
contract. The optional installed Anvil version must provide that command.
Serving does not install it automatically, add a dependency, implement another
TypeSafe HTTP client, or change its Claude Agent SDK policy. Anvil owns the
fixed TypeSafe endpoint, pinned `jev-1.13.0` model and typed question rubrics.

## Operator setup and off switch

```bash
anvil-serving workbench jev setup --confirm
anvil-serving workbench jev enable skill_suggestion --allow-api --allow-export --confirm
anvil-serving workbench jev disable --confirm
```

Setup records the installed `anvil` executable discovered on PATH; use
`--anvil-binary /absolute/path/to/anvil` to select another trusted installation.
Setup alone leaves new installations off. Non-secret settings live in
`jev.json` in the existing operator configuration home, and load automatically.
Repeated setup with the same binary does not rewrite the file. Policy updates
retain a numbered backup. The commands do not start or restart services.

Enable each capability independently: `skill_suggestion`, `context_ranking`,
`incident_triage`, or `voice_intent`. Disabling with a capability name removes
only that capability; disabling without a name clears global API/export
permission. Every operation additionally needs selected-source export consent.
Changes are rechecked before displaying an answer; already sent bytes cannot
be recalled.

The trusted execution environment supplies `TYPESAFE_API_KEY` through its
existing protected secret mechanism. Do not put its value in the policy,
browser, CLI argument, shared environment file, or repository. The bridge
receives only that named credential and a small execution-environment
allowlist; neither process scans account stores or env files. Status does not
check credential presence.

## Explicit CLI advice

```bash
anvil-serving workbench jev advise incident_triage --input observation.json --allow-export --json
```

The input is deliberately selected JSON, limited to 32 KiB including bridge
overhead. Secret files and symlinks are refused. `--no-jev` disables the
operation before reading the input. Ordinary typed results include the model,
capability, status, whether a request started, whether usable advice completed,
input/rubric digests, elapsed time, usage and validated answers. Errors omit
raw subprocess/provider output. Missing optional tooling reports unavailable.

| Capability | Selected input |
| --- | --- |
| `skill_suggestion` | `{"intent":"Review tests","candidates":[{"id":"testing","description":"An available testing skill"}]}` |
| `context_ranking` | `{"intent":"Understand cancellation","candidates":[{"id":"context1","text":"A selected optional snippet"}]}` |
| `incident_triage` | `{"observation":"A selected synthetic request returned HTTP 401"}` |
| `voice_intent` | `{"text":"Tell me how to restart the service"}` |

Candidate lists contain 1–24 distinct opaque IDs (at most 48 characters) with
bounded descriptions or snippets. Selected text is at most 4,096 characters;
additional byte and request-overhead bounds apply. The caller determines
eligibility and authorization before selection.
Skill output selects one supplied ID or `none`; it never installs or invokes
anything. Context scores only produce stable optional ordering, with the
baseline retained. Pinned instructions and active packets remain outside this
selection. Incident categories choose a local read-only diagnostic suggestion,
never a model-generated command or verified root cause.

## Workbench

Open **Settings → Jev assistance**. Select an authorized source, the assistance
type and safe excerpts or descriptions. Consent starts unchecked. Only clicking
**Request Jev advice** sends the selected text. The panel shows persistent Jev
attribution, request-attempt status, model and expanded experimental confidence.
**Restore original order** preserves all optional candidates; **Disable for
this selection** invalidates pending display and removes local consent.

The same authenticated Workbench namespace accepts
`POST advisories/skill_suggestion`, `advisories/context_ranking`, or
`advisories/incident_triage`. The closed body contains `resource_id`, opaque
`generation`, boolean `allow_export`, and capability-specific `input`; optional
`disabled: true` overrides policy. Existing same-origin, CSRF and Connect
authentication apply. Resource permission is checked before inspecting selected
input and again after the bridge returns. Results bind to the principal,
session, resource, generation and selected content; they are not cached or
written into projects, task state, Pi sessions or prompts. Source changes in
the panel invalidate delayed results. The input form never reads server files,
retrieves logs or discovers additional candidates.

## Voice extension

Voice intent requires its own operator capability plus explicit permission on
each authenticated Realtime connection:

```json
{"type":"session.update","session":{"anvil_jev":{"enabled":true,"allow_export":true}}}
```

The returned `session.updated` includes the effective Jev enablement, model and
cloud-text disclosure. Client UI/onboarding must present that disclosure before
opting in. Set both booleans false to stop new classifications. This extension
is consumed by the voice owner and never sent to the selected LLM.

Only finalized STT text or explicitly submitted final user text is eligible.
Raw audio, partial transcripts, tool output and conversation history are not
classified. One worker per session has one pending slot and a one-second Jev
request deadline, with finite subprocess cleanup. Normal generation continues
without waiting; cancellation and barge-in remain deterministic. Queue overload
drops advice. Session teardown waits for bounded worker cleanup.

Opted-in consumers may receive `anvil.voice.intent` with `turn_id`,
`turn_revision`, `generation`, `event_id` and the attributed `annotation`.
Stale turns, revoked consent and changed policy discard late results. Intent
labels are `conversation`, `read_only_information`,
`operational_change_request`, `unclear`, or `unsupported`. A request to restart
a model is a label, not permission to restart it. Clients that do not opt in
receive their existing protocol behavior.

## Evidence and limits

Offline tests cover disabled paths, strict bridge output, secret redaction,
stable ranking, native configuration commands, authenticated access, session
revocation, bounded workers and cancellation. They do not establish model
accuracy, production latency, production data-retention suitability, or live
audio qualification. No live API or fleet operation is required by these tests.
Source merge leaves every capability off. Production retention terms and
approved source classes need operator review before live enablement.

### Implementation review receipt (2026-09-20)

Two independent adversarial reviewers cleared the final code after repairs.
The security/privacy review passed 104 focused tests; the separate lifecycle
and output-correctness review passed 52. The author's wider regression gate
passed 461 with one skip. Findings repaired before delivery included consent
revocation at dispatch, queued result generation, core shutdown ordering,
matching rather than merely well-formed input digests, concurrent policy
writers, and disable/re-enable to identical policy values.

Chrome testing against isolated loopback fixtures verified default-off policy,
unchecked consent, refusal before consent, attributed ranking, exact baseline
restore, and clearing consent/advice. Enabled browser responses were synthetic;
no browser test sent project text to a live provider. Separate native DOM and
authenticated HTTP tests cover stale results, resource grants and CSRF.

A clean wheel install verified default-off status, `--no-jev` before reading a
missing input, and inclusion of the Jev UI asset. Strict docs build, tracked
Markdown links, full CLI reference audit and Ruff passed. Current tracked
snapshot semantic and pinned Gitleaks scans reported no findings; Git history
and live credential rotation were not in scope.

The real adapter also completed four synthetic calls through an independently
installed Anvil 0.6.12 candidate wheel into TypeSafe (614–661 ms end to end).
The separate frozen core corpus matched 40/40 prewritten expectations. Full
inputs, outputs, denominators and limitations are retained in
[Anvil integration PR 238](https://github.com/fakoli/anvil/pull/238).
That PR requires a completed Copilot review, currently blocked by quota; source
merge, package publication and live enablement are not implied by these tests.
The optional bridge must be installed separately before enabling Serving.

The configured absolute executable is trusted local software, not a sandbox.
Supported policy writers share the owner lock; direct same-user edits that
bypass the lock are outside its serialization contract. Already transmitted
text cannot be recalled, and secret filtering is not general DLP.
