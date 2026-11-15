# Ignis 0.8 required-tool qualification on one RTX PRO 6000

**Date:** 2026-10-26 local time.

**Status:** The original `tool_choice: "auto"` preflight missed its frozen
all-four tool expectation at 3/4. A separately frozen phase-two experiment kept
the exact Ignis 0.8.0 image, Qwen3.8 model revision, recipe, and hardware
profile, changed only the harness/request policy, and passed all 16 bounded
requests. The selected request-level configuration was `tool_choice:
"required"` with normal thinking. This qualifies that supported path for steps
where a tool call is mandatory; it does not establish `auto` reliability, a
global agent default, coding/SWE quality, performance, capacity, or promotion.

<!-- benchmark-result-card/v1 -->
## Result card

| Setup | Frozen value |
|---|---|
| Model | `gpillon/Qwen3.8-Flash-Next-trellis-a25-ignis@8d94db83cd8e58d6be31e796723e5e35b960638c`; served as `qwen3.8-flash-next` |
| Runtime | Ignis 0.8.0, immutable image `sha256:0226b62047113c219124ca4a02b1e52492402b44f5a3020db91e77756914b8cc` |
| Hardware | One RTX PRO 6000 Blackwell Max-Q measured; the second equal card remained at 0 MiB candidate allocation in retained snapshots |
| Recipe | TP1, 131,072 configured context, eight decode lanes, MTP3, `hq-e8-2b` KV, vision disabled, 52 GiB cgroup RAM limit, no cgroup swap |
| Phase-one source | `dc576b25083d7cfdab1554db89379feee82346b5` |
| Phase-two harness | `tool-call/v2-exact-weather`; patch SHA-256 `0c9b211fd6bf8d49493a2db46f5def4ebfe9a887d2470d8a74f6f37331b46206`; reviewed source commit `c727ff01f34d2d7564fab63d94203fec68890baf` |
| Common request controls | Temperature 0.6; maximum 4,608 completion tokens; 60-second timeout; zero retries |
| Measurement boundary | Focused direct functional tool-policy qualification; no coding, SWE, route/client, capacity, performance, or promotion claim |

| Headline evidence | Result | Boundary |
|---|---:|---|
| Original phase-one named checks | 4/5 | Smoke, JSON prompt, streaming tool, and tool-result passed; `auto` shared-prefix batch failed |
| Original phase-one observations | 8/9 | Nine requests across six sequential phases; tools were 3/4 |
| Phase-two mechanism cells | 3/3 | Seed 17: `auto`/default, `required`/default, and `required`/thinking-disabled each made one valid call |
| Selected required/default full preflight | 5/5 checks; 9/9 observations | Seed 17; selected by the frozen normal-thinking-first rule |
| Selected required/default repeat | 4/4 tool calls | Seed 18; one concurrent shared-prefix batch |
| Phase-two request accounting | 16/16 terminal engine requests | All 16 reconciled; literal HTTP 200 retained for the 12 captured tool requests, not all 16 observations |
| Candidate GPU allocation | 96,027 MiB / 0 MiB | Candidate and protected second GPU, respectively |
| Prepared coding campaign | 0/12 cells run | Separate route/source prerequisites blocked dispatch; it contributes no coding result |

[Authoritative summary](2026-10-26-ignis-080-preflight-evidence/summary.json) ·
[phase-two evidence](2026-10-26-ignis-080-preflight-evidence/tool-choice-followup.json) ·
[artifact manifest](2026-10-26-ignis-080-preflight-evidence/artifact-manifest.json) ·
[evidence index](2026-10-26-ignis-080-preflight-evidence/README.md)

## Phase one: retain the automatic-choice miss

The original private plan and pre-live review described six HTTP requests.
That was a planning-count error: six is the number of sequential phases. The
frozen command made one smoke request, one JSON request, four concurrent tools
requests, one streaming-tool request, and two requests for the tool-result
exchange: nine total. Nine native observations and nine terminal engine events
agree. Explicit HTTP status fields were not retained for this phase, so the
report does not turn successful decoding into a literal nine-of-nine HTTP-200
claim.

Each tools request sent the same 400-line synthetic system prefix, the user
instruction `What's the weather in Oakland? Use the tool.`, and one
`get_weather(city: string)` definition with `tool_choice: "auto"`. Three calls
were valid. The fourth returned ordinary assistant prose, stopped, and
contained no `tool_calls`. The harness correctly rejected it against the
predeclared all-four expectation.

That result is not an Ignis protocol violation: the documented and implemented
meaning of `auto` leaves the decision to the model. Nor does the evidence
identify an engine, cache, template, parser, or model defect. Two other
reused-prefix requests passed, and the engine's internal request-done field was
`stop` for successful API tool calls as well as the failed prose response.
The phase-one result remains a bounded reliability diagnostic under its older,
unseeded oracle; it is not retroactively rescored by phase two.

## Phase two: use the supported mandatory-tool policy

Source review pinned Ignis 0.8.0 to peeled commit
`950ee0d1c2cf6178cd7fec29df3f17c3e127f987`. Ignis documents `auto` as the
model-decided default and `required` as the forced-call path. The phase-two
harness added explicit tool-choice, seed, and bounded transport-capture
controls, reused the existing thinking control, and added an exact oracle: one complete `get_weather` call,
JSON-object arguments with non-empty `city` equal to `Oakland`, and
`finish_reason: "tool_calls"`. No engine or model code changed.

The predeclared first stage sent three sequential seed-17 requests. All passed:
`auto` with default thinking, `required` with default thinking, and `required`
with thinking disabled. The selection rule preferred normal thinking when its
required cell passed, so the disabled-thinking cell remained a diagnostic and
did not become the selected configuration.

The selected `required`/default-thinking path then passed all five full
preflight checks and all nine observations at seed 17. A separate seed-18
shared-prefix repeat passed 4/4. Across the three stages, engine terminal IDs
1–16 formed a complete event set with no observed request loss. Twelve captured
tool requests retained HTTP status 200: the three mechanism cells, five
non-streaming tool transports in the full preflight, and four repeat requests.
The other four successful observations did not retain literal status fields.

The seeded `auto` 1/1 pass is useful mechanism evidence only. It does not erase
the prior unseeded 3/4 result or establish an automatic-choice success rate.
Likewise, `required` is the supported request-level policy for a workflow step
that must call a tool; it is not a production-wide default for coding, chat, or
autonomous agents. Ignis still leaves tool arguments to model generation, so
the exact local argument oracle remains necessary.

[The retained contract note](2026-10-26-ignis-080-preflight-evidence/tool-usage-contract.md)
links the source lines used for this interpretation.

## Identity and resources

Both phases used the same immutable Ignis image, exact model revision, served
name, one-card selection, `rtxpro6000` profile, eight lanes, MTP3, configured
131,072-token context, and 52 GiB/no-swap containment. No profile, template,
prompt, temperature, artifact, or speculation control changed in phase two.

The post-phase-two point snapshot recorded 96,027 MiB on the candidate GPU and
0 MiB on the protected second GPU, 44,944,740,352 bytes (41.86 GiB) cgroup
peak, zero cgroup OOM events, 61,764,468,736 bytes host `MemAvailable`, and zero
ten-second memory pressure. The GPU and host values are bounded point samples;
the managed cgroup peak is a lifecycle high-water mark. None establishes
capacity, efficiency, or performance. Startup and CLI durations are also
excluded from performance claims.

Disk-KV remained disabled because its directory could not be opened; load
continued without that optional tier. The configured image prompt-enhancer
warning does not govern text-chat tool choice. Neither warning is assigned as
a cause of the phase-one `auto` miss.

## Separate unrun work and decision

The prepared 12-cell coding campaign submitted zero cells because its explicit
evaluation route and protected-source prerequisites were unavailable through a
supported path. It contributes no coding, SWE, route-integration, timing,
capacity, or promotion evidence.

The useful result is narrower: on this exact pin, Ignis 0.8.0 with normal
thinking passed the bounded `required`-tool mechanism, full five-check
preflight, and independent-seed four-call repeat. The earlier `auto` miss
remains visible and is interpreted according to the documented policy. No
engine patch was needed, no engine/model bug is claimed, and no general agent
configuration, performance rank, capacity claim, or promotion follows.

## Restoration boundary

Phase-one restoration already returned the exact incumbent recipe, image,
model revision, served identity, and two-GPU order; candidate absence, managed
readmission, a routed smoke, idle engine state, and zero OOM delta passed. The
first smoke invocation failed locally before any request because its explicit
auth environment variable was absent; the retained r2 used the supported
credential resolver and passed one request. Router custody objects were
byte-identical, but mounted router configuration bytes were not independently
inspected.

Phase two independently repeated exact operational restoration: incumbent
model, revision, immutable image, recipe and registry digests, GPU order,
bound endpoints, and served identity matched the captured start; the candidate
was absent; router custody was unchanged; the primary route was readmitted
ready; one routed smoke passed; and the engine was idle afterward with zero
container OOM events. Mounted router configuration bytes were again not
independently inspected, and the one smoke is not full quality or strict-JSON
acceptance. The managed incumbent load took 484.84 seconds; that is an
operational duration, not a performance result.

Both operational restorations passed. Independent exact-head review remains
the publication gate. No candidate is promoted.
