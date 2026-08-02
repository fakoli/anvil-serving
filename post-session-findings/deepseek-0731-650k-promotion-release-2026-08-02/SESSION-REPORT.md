# Session report: DeepSeek 0731 650K promotion and Anvil Serving 0.21.1

## Session shape

| Metric | Value |
|---|---:|
| Project | `C:\Users\operator\ai-code\anvil-serving` |
| Runtime | Codex Desktop |
| Analyzed interval | 2026-08-02 06:51-14:28 UTC |
| Wall-clock | 7.6 h |
| Agent messages | 387 |
| Human messages | 55 |
| Codex task starts | 27 |
| Subagents | 0 |

## Token economy

The stock parser emitted zero additive counters after classifying the resumed
rollout as replay-only. The values below were independently reconstructed from
the last cumulative `token_count` before the release-completion event.

| Metric | Value |
|---|---:|
| Visible output tokens | 501,000 |
| Reasoning output tokens | 147,124 |
| Total generated output | 648,124 |
| Cached input tokens | 210,030,080 |
| Non-cached input tokens | 4,742,524 |
| Total input tokens | 214,772,604 |

The session was unusually expensive because it combined external research,
image builds, repeated long-running model loads, near-limit context probes,
live client testing, full repository validation, two PR/release cycles, and a
production hotfix in one continuous task.

## Tool distribution

| Transport-level event | Count |
|---|---:|
| `exec` | 1,096 |
| `wait` | 402 |
| Patch applications | 91 |
| MCP tool completions | 27 |
| Web search completions | 21 |

`exec` is a wrapper around shell, web, MCP, and other nested operations, so the
counts describe orchestration traffic rather than distinct operator actions.

## Narrative

The full evidence-backed retrospective is in [narrative.md](narrative.md). Its
central finding is that the model decision was correct only after real Pi
traffic overturned the synthetic 1M result, while the release process still
needed a second hotfix because container file-dependency closure was not part
of the 0.21.0 readiness gate.

## Measurement limitations

- The source rollout contains 54 `session_meta` records. Session-retro 1.2.0
  treated the rollout as replay-only and excluded all additive counters.
- Reconstructed counts use unique task, client, call, and event identifiers and
  stop at the 0.21.1 release completion event. They intentionally exclude this
  retrospective/PR turn.
- Token totals are cumulative Codex counters, not cost attribution per
  experiment. No attempt was made to assign tokens to individual model runs.

## Outcome

- DeepSeek V4 Flash 0731 at 650K/maxseq16 became the human-approved
  `llm.primary` for one Pi/OpenClaw coding user.
- The superficially successful 1M profile remained experimental after two
  client-shaped B12X workspace crashes.
- Anvil Serving 0.21.1 repaired the controller profile-mount regression that
  escaped the 0.21.0 release gate.
- Final live verification established version parity, route identity, high
  reasoning, exclusive GPU ownership, blocked competing stacks, clean shared
  memory, and all 16 OpenClaw MCP tools.

## Retrospective

The operator drove the run through short evidence-based pivots: moving display
output to the iGPU, preserving host CPU and RAM, retrying max sequences 16,
benchmarking 650K and 1M, selecting `llm.primary`, making high reasoning the
default, and requiring every competing GPU stack to remain blocked. The
workflow succeeded because model identity and runtime knobs stayed pinned while
context, batching, and admission changed, and because real Pi/OpenClaw client
traffic remained a separate verification tier after synthetic capacity passed.

The largest model lesson was that near-limit retrieval is not an agentic
promotion gate. The 1M profile recovered a needle near 985K, passed bounded
protocol tests, and initially looked like the best option. Real Pi prompt and
tool shapes then crashed B12X twice, including with only 5,120 requested output
tokens. The stable 650K profile won because it survived the actual single-user
coding path on Dark Pi, Mini Pi, and Mini OpenClaw.

The largest release lesson was that clean CI and a merged PR do not prove live
deployment dependency closure. Transactional router activation added target
and rollback profile files to the controller's runtime contract, but the
hardened Docker Compose deployment did not mount them. The missing files
caused an outage after 0.21.0 shipped. The 0.21.1 fix added exact read-only
mounts and a test that derives required profiles from the serve manifest.

Other friction was preventable. The long run briefly lost its explicit goal,
an image build consumed essentially every CPU thread and about 60 GB of Docker
memory before host resource ceilings were set, and the public alias had to be
corrected from `llm.pi` to `llm.primary`. Progress state, host mutation limits,
and the caller-facing contract should be explicit before a long autonomous
run starts.

The Five Whys traced the 0.21.0 outage from the missing profile files, to absent
Compose mounts, to a newly expanded transactional runtime contract, to tests
that did not derive file closure from the manifest, and finally to a release
workflow that did not join source version, container files, target-host config,
endpoint parity, rollback, and real-client smokes. The systemic fix is a staged
release-readiness contract rather than more operator memory.

There were three near misses. The controller defect surfaced while the session
was still active; the 1M failure surfaced before it became the durable Primary;
and the iGPU change produced enough VRAM to pass despite only 94 MiB remaining
after the configured reserve. None of those outcomes should be treated as a
repeatable safety mechanism.

## Recommendations

- Now: run coordinated releases through a release-readiness skill that checks
  base freshness, merged-tree verification, manifest-derived container file
  closure, target config migration, version parity, rollback, and real-client
  post-deploy smokes before closure.
- Now: require a client-shaped Pi/OpenClaw workload after long-context capacity
  and protocol tests but before any human promotion decision.
- Now: declare host CPU, memory, WSL, and Docker limits before compilation and
  keep a concise progress heartbeat with current goal, phase, and next gate.
- Next: add a product command that validates manifest-to-controller deployment
  dependencies and emits a structured parity packet.
- Next: fix session-retro so repeated Codex Desktop metadata records do not
  erase valid task, tool, and token evidence.
- Later: retain a sanitized Pi/OpenClaw client-shape corpus and automate the
  bounded post-deploy route, ownership, shared-memory, and MCP discovery checks.

## Action

The session findings call for one immediate repo-owned workflow addition:
`anvil-serving-release-readiness`, a staged release/deployment gate that joins
manifest dependency closure, version parity, real-client smokes, and outage-free
closure.
