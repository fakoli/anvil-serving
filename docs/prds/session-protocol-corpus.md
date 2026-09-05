# Project: Protocol and session correctness corpus

## Summary

Add a small, versioned, synthetic regression corpus that proves supported gateway
dialects preserve conversation history, tool-call identity, streaming boundaries,
and explicit retry behavior. Reuse the router's real adapters and the native
agentic runner's injected caller. The deliverable is deterministic compatibility
evidence, not a new session server, tokenizer, or model-quality leaderboard.

## Status and evidence

**Status:** Proposed. **Priority:** High.
**Baseline:** Anvil `f50dca489780b95d0cd98dee59cf620618c4ccd1`.

Anvil already has dialect, Responses, and streaming tests, deterministic agentic
scenarios, and incremental long-session execution. This proposal consolidates
cross-turn invariants and adds adversarial coverage; it does not claim those
features are missing. In `suite_runner.py`, inspect the actual requests supplied
to the injected caller: a scorer's stored `history` is not automatically proof
of what was sent on every turn.

Miles' [agentic rollout documentation](https://github.com/radixark/miles/blob/d2fc97ce581577e255e494801d7568747d5a10d7/docs/user-guide/agentic-rollout.md)
motivates testing exact history, matching rules, and session branches. Its TITO
implementation has explicit experimental and modality limitations. Borrow these
test questions, not its token ownership or history-reconstruction machinery.

## Goals

- Catch a protocol regression before a client or model qualification encounters it.
- Distinguish gateway corruption from model-generated malformed output.
- Make failures reproducible using public synthetic inputs and independent oracles.
- Give future adapters a named compatibility contract they can run in ordinary CI.

## Non-Goals

- Prefix caching, retokenization, server-side conversation storage, or TITO adoption.
- New dialect features, Responses storage, speculative decoding, or tool execution.
- Automatic retries, route fallback, history repair, deduplication, or compaction.
- Mining private conversations, recording reasoning text, or model self-grading.
- A new benchmark suite name, CLI command, dependency, or all-dialects live harness.

## Requirements

- R001: Store a versioned corpus with stable case IDs and a content hash. Each
  case declares the invariant, applicable existing adapter path, synthetic input,
  expected result, and whether the input is deliberately invalid. Unknown keys,
  duplicate IDs, missing expectations, and invalid fixture references fail loading.
- R002: Test the real request translation/relay path against explicit expected
  payloads or events. Do not generate expected output with the production
  translator or normalize away fields being tested.
- R003: Preserve supported role order, content, tool names, argument values,
  tool-call IDs, and tool-result associations across turns. Compare parsed JSON
  argument objects for semantic cases; use exact bytes only for cases explicitly
  testing string or transport fidelity. Object-key ordering alone is not failure.
- R004: Capture deep copies of every request given to the agentic runner's fake
  caller. Assert prior messages are unchanged and the actual assistant response
  plus corresponding tool results appear in the next request. A mutation of a
  previously captured request must be detected independently of the final answer.
- R005: A branched-history fixture executes two independent client histories.
  Neither request may contain the other branch's messages or tool results. This
  tests stateless request isolation, not support for server-side branching.
- R006: SSE cases prove incremental delivery, split UTF-8 handling, split tool
  arguments, correct terminal events, and bounded failure on truncation. A reader
  must observe an early event before the fake upstream releases its final event.
- R007: Disconnect and upstream-error cases preserve the current transport error
  contract and release admission leases exactly once. A partial response must
  never be reported as a complete successful answer.
- R008: Explicit client retries are separate requests. The gateway must not
  issue additional upstream attempts, execute tools, or switch tiers. Request
  correlation stays per attempt; do not promise idempotent upstream execution.
- R009: Missing/duplicate tool IDs, malformed arguments, unsupported input items,
  and exhausted output budgets have explicit case outcomes. Preserve each
  documented adapter's reject/pass-through behavior; do not silently invent a
  successful repaired message. Where current contracts differ, record separate
  adapter expectations rather than imposing a new compatibility policy.
- R010: Reports separate `passed`, `failed`, and `not_applicable` per adapter.
  Every applicable case must pass for that adapter to claim this corpus version.
  A newly unsupported case is a failure until the contract change is reviewed.
- R011: CI performs no external model calls and executes no fixture tool commands.
  Public fixtures are authored synthetically. Persist only case IDs, input hashes,
  counts, expected/observed structural differences, and bounded failure codes by
  default; never publish real request content or reasoning text.
- R012: Existing agentic/context/SWE profile hashes and scores remain unchanged.
  Live use of related agentic scenarios is optional separate evidence with exact
  model, endpoint contract, controls, and corpus identity; CI success is not model
  qualification or client acceptance.

## Corpus contract and initial coverage

Proposed location: `tests/fixtures/protocol_sessions/manifest.json` and adjacent
JSON fixtures. Use schema `anvil-serving.protocol-session-corpus/v1`. The
manifest has `schema`, `version`, `content_sha256`, and `cases`. Build one
identity object containing `manifest` (the manifest without `content_sha256`)
and `fixtures` (one record per distinct referenced input or expected-output
file, sorted by its exact relative POSIX path). Each fixture record contains
`path`, `size_bytes`, and `sha256` of its raw bytes. Hash the canonical JSON bytes
of that complete object using the repository's canonical JSON helper. Never hash
unframed concatenated file contents: fixtures containing `1` and `23` must have
a different identity from fixtures containing `12` and `3`. Include a golden
digest plus file-boundary and rename negative controls so independent tools can
reproduce the identity and detect changes to each individual file.

Each case has `id`, `invariant`, `adapter_paths`, `fixture`, `expected_fixture`,
and `kind` (`roundtrip`, `stream`, `runner`, or `negative`). Adapter-path IDs
come from an explicit table in the test helper, not import strings from JSON.
Fixtures cannot name an executable, arbitrary filesystem path, or network URL.
Bound v1 to 32 cases, 256 KiB per file, and 2 MiB total. All fixture references
must remain within this directory after resolution, including on Windows.

| Case ID | Minimum assertion |
| --- | --- |
| `tool-result-roundtrip` | Tool result remains attached to the original call ID. |
| `two-calls-one-turn` | Two calls retain distinct IDs, names, and arguments. |
| `results-out-of-order` | Result order cannot change which call each result belongs to. |
| `unicode-arguments` | Unicode, empty strings, booleans, null, and nested JSON survive. |
| `history-append-only` | Earlier request messages are unchanged in later turns. |
| `explicit-tool-error` | An injected tool error is passed back as data, not hidden. |
| `client-retry-is-explicit` | Two client attempts create exactly two upstream attempts. |
| `branch-isolation` | Two histories with a common prefix remain independent. |
| `stream-first-event` | First event is readable while final event is still blocked. |
| `stream-split-utf8` | Byte splits do not corrupt a multibyte character. |
| `stream-split-tool-json` | Argument fragments form the declared final object. |
| `stream-disconnect` | Disconnect closes upstream work and releases one lease. |
| `stream-truncated` | Missing completion is incomplete/error, never success. |
| `invalid-tool-identity` | Missing and duplicate IDs follow explicit adapter contracts. |
| `invalid-arguments` | Invalid JSON is not silently repaired into a valid tool call. |
| `budget-exhaustion` | A length/step limit does not become a completed answer. |

Use parameterized variants for missing versus duplicate IDs and supported versus
unsupported Responses items. Scope the matrix to adapter combinations already
supported at implementation time. Mark a genuinely inapplicable combination with
a checked-in reason; do not skip a failing case based on runtime behavior.

## Implementation map

| Existing file | Purpose |
| --- | --- |
| `tests/router/test_dialect_parity.py` | Current dialect fixtures and translation assertions |
| `tests/router/test_responses.py` | Supported Responses subset and negative contracts |
| `tests/router/test_streaming_relay.py` | Real relay, fake upstream, streaming/lease tests |
| `tests/router/test_transition_integration.py` | Lease behavior during transitions |
| `anvil_serving/benchmarking/suite_runner.py` | `_run_agentic_case`, `_run_long_session_case`, `_normalized_tool_calls` |
| `anvil_serving/benchmarking/agentic.py` | Deterministic scenarios and independent scoring |
| `tests/test_benchmark_suite_runner.py` | Injected caller and actual request-history assertions |
| `tests/fixtures/session_evals/suite.json` | Existing separate session-eval format; do not replace it |

Proposed new files: `tests/protocol_session_helpers.py`,
`tests/router/test_protocol_session_corpus.py`, and the corpus directory above.
Keep the loader in test support for v1. Do not install test fixtures into the
runtime package or introduce a generic replay service.

## Features

### F001: Versioned corpus and independent structural oracles

**Requirements:** R001, R002, R003, R009, R010, R011

### F002: Cross-turn and client-attempt isolation

**Requirements:** R004, R005, R008, R012

### F003: Streaming and resource-lifetime regression gates

**Requirements:** R006, R007

## Tasks

### T001: Freeze the supported matrix and implement the fixture loader

**Feature:** F001
**Priority:** high
**Likely files:** proposed corpus directory, tests/protocol_session_helpers.py,
tests/router/test_protocol_session_corpus.py

Read existing adapter tests first. Write the manifest and expected fixtures by
hand, then validate bounds, safe references, duplicates, and hashes. Record each
existing adapter path and deliberate unsupported combination in the manifest.

**Acceptance criteria:**

- Each case has a named oracle and no expectation generated by production code.
- A bad hash, traversal reference, duplicate case, or unknown adapter ID fails.
- Different file boundaries or relative paths produce different corpus hashes,
  even when concatenating the fixture bytes would produce the same byte stream.
- Reordering JSON keys does not change semantic fixture expectations.
- A changed argument value or call ID does change the relevant expectation.

**Verification:**

- After creating the test: `python scripts/run_tests.py tests/router/test_protocol_session_corpus.py -x -q`

### T002: Wire tool/history cases to real adapters and the injected runner

**Feature:** F002
**Priority:** high
**Dependencies:** T001
**Likely files:** tests/router/test_protocol_session_corpus.py,
tests/test_benchmark_suite_runner.py, tests/protocol_session_helpers.py

Use a scripted fake caller that deep-copies incoming messages before returning
each synthetic response. Assert every outbound request, not merely the final
trace. For retries and branches use independent requests through the real
gateway with a counting fake upstream. Do not add retry behavior to the router.

**Acceptance criteria:**

- Cross-turn tests cover actual assistant messages, two tool calls, explicit
  tool errors, branch isolation, and separate client attempts.
- A negative fixture that swaps tool-result IDs fails even if its final text is correct.
- Mutating an earlier message fails the history assertion.
- If a product defect is found, add a minimal failing test and fix only the
  responsible seam; do not relax the corpus to match corrupted behavior.

**Verification:**

- `python scripts/run_tests.py tests/router/test_protocol_session_corpus.py tests/router/test_dialect_parity.py tests/router/test_responses.py tests/test_benchmark_suite_runner.py -x -q`

### T003: Add deterministic incremental-stream and cancellation cases

**Feature:** F003
**Priority:** high
**Dependencies:** T001
**Likely files:** tests/router/test_protocol_session_corpus.py,
tests/router/test_streaming_relay.py

Reuse the existing loopback fake-upstream test harness. Gate the final upstream
write with an event and prove the consumer sees an earlier event. Split bytes at
specific UTF-8 and JSON boundaries; test a closed client socket and truncated
upstream. Use bounded waits and `finally` cleanup, not timing sleeps.

**Acceptance criteria:**

- Buffering the whole upstream response makes the early-delivery test fail.
- Truncation cannot produce a successful terminal event.
- Disconnect/error paths release exactly one lease and leave no test threads.
- Do not claim arbitrary network timing or live-engine compatibility from these tests.

**Verification:**

- `python scripts/run_tests.py tests/router/test_protocol_session_corpus.py tests/router/test_streaming_relay.py tests/router/test_transition_integration.py -x -q`

### T004: Publish the compatibility matrix and close the regression gate

**Feature:** F001
**Priority:** medium
**Dependencies:** T002, T003
**Likely files:** docs/THIN-CAPABILITY-GATEWAY.md,
docs/OPENCLAW-INTEGRATION-SPEC.md, proposed corpus README

Document how to add a case, the fixture/hash contract, tested adapter paths, and
the distinction between protocol correctness and model behavior. Keep live
benchmark instructions on the existing managed evaluation surfaces.

**Acceptance criteria:**

- Every applicable matrix entry passes; no catch-all skips or xfails hide gaps.
- Existing benchmark profile content hashes are unchanged.
- The handoff includes one failing structural negative control and one failing
  buffering negative control, both restored before final verification.

**Verification:**

- `python scripts/run_tests.py tests/router/ tests/test_benchmark_suite_runner.py tests/test_benchmark_profiles.py -x -q`
- `python -m mkdocs build --strict`
- `git diff --check`

## Acceptance Criteria

A cold-start agent can run the corpus without credentials, Docker, GPU access,
or private transcripts. It must detect a swapped tool ID, mutated prior message,
hidden extra upstream attempt, buffered stream, and truncated successful answer.
Passing the current corpus version is a gateway regression claim only.

## Risks

- Excessive normalization can erase the defect the corpus is supposed to catch.
- A test-only fake translator would validate itself instead of the product.
- Request recording by reference can hide history mutations; deep-copy on entry.
- Overly broad protocol promises can turn a test project into an API expansion.

## Assumptions

### A001: The gateway remains stateless with respect to client history.

**Rationale:** Existing explicit request/dialect seams are sufficient for these checks.
**Requirements:** R004, R005, R008

### A002: CI compatibility and model qualification are separate evidence classes.

**Rationale:** Scripted upstream output cannot demonstrate model reasoning or tool quality.
**Requirements:** R010, R011, R012

## Open Questions

- Parked: opt-in live corpus execution across every dialect. First prove the
  synthetic compatibility contract; do not add another model client in v1.
- Parked: multimodal/token-level history equivalence and session compaction.

## Rollout and rollback

Land the test corpus and any separately reviewed minimal regression fixes.
No operator migration or service restart is required for test-only delivery.
A runtime fix follows normal release and deployment gates. Remove/revert new
tests only with an explained contract change, not to hide a failing adapter.
