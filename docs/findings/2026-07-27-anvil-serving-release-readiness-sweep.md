# Anvil Serving release-readiness sweep — 2026-07-27

## Decision

The installed `0.13.3` surface and the release candidate source passed the
live model, purpose-service, recipe, audio, ComfyUI, model-cache, and ownership
workflows in scope. Every release-significant gap discovered by this sweep was
fixed in place and is tracked with its compatibility decision and evidence in
`.tickets/2026-07-27-release-sweep-fixes.md`.

The source is ready for merge/release review after the complete repository
gates below. Model promotion remains a separate human decision: Agents-A1 is a
benchmark-qualified challenger, while Laguna remains the qualified Heavy.

## Scope and identity

- Repository revision:
  `58f7809588a603f2c7b41a7684c40fc02f77315c`
- Installed package: `anvil-serving 0.13.3`, editable with development extras
- Host: Fakoli Dark, Windows/WSL2/Docker Desktop
- GPUs:
  - GeForce RTX 5090, 31.84 GiB
  - RTX PRO 6000 Blackwell Max-Q, 95.59 GiB
- vLLM image for Agents-A1 and Laguna:
  `nightly-f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1`
- Router: existing managed `anvil-router`, left running
- Test policy: no model or routing promotion; live endpoints were qualified
  directly, and the original Heavy service was restored at the end

`main` was pulled and rebased before testing. Existing unrelated untracked
editor and graph output was preserved.

## Command-surface coverage

The generated command manifest contained 136 CLI paths: 80 read operations, 50
mutations, and 6 process operations. Every path parsed and returned help
successfully (`136/136`, zero failures). This is parser/discoverability coverage,
not a claim that every destructive path was applied.

Live or applied coverage included initialization and idempotency, doctor, GPU
inventory, router/serve inspection, managed serve up/down/remove/logs, recipe
CRUD/load, pinned model pull, preflight, capacity and quality benchmarks,
benchmark-evidence rendering, and managed voice audio up/status/logs/down.
It also included exact cache-removal planning, idempotent pull verification,
engine-aware serve probes, ComfyUI lifecycle, and stack reconciliation.
Structured coverage is retained in
[cli-surface-sweep.json](2026-07-27-release-readiness-evidence/cli-surface-sweep.json).

`init` was exercised in an isolated operator-config root. It wrote 16 expected
files, and a second run reported all 16 unchanged.

## Live result matrix

| Area | Result | Evidence and caveat |
|---|---|---|
| Recipe CRUD | Pass | Create, list, show, update, delete, recreate, dry-run load, and applied load all used an isolated registry with atomic backups. |
| Agents-A1 artifact pull | Historical failure, product fixed | The original exact revision filled the Docker disk. Pull now preflights selected bytes plus retained headroom and verifies the exact snapshot. |
| Agents-A1 default thinking | Fail | Smoke spent 29.1 s, produced no visible answer, ended `length`, and emitted about 14,797 reasoning characters. |
| Agents-A1 thinking disabled | Pass | Smoke, JSON, 120K retrieval, and 20/20 tool calls passed. |
| Agents-A1 quality | Pass for tested contract | Intelligence 6/6 attempts, session 3/3, and tool 3/3 with zero recorded failures. This is challenger qualification, not promotion. |
| Laguna Heavy preflight | Pass | Thinking-disabled smoke, JSON, 120K retrieval, and 20/20 tool calls passed. |
| Laguna Heavy quality | Pass | Tool, session, unified diff, timeout triage, and 32K context each passed 3/3. |
| Embeddings | Pass | Product `serves probe` returned one 1,024-dimensional vector. |
| Reranker | Pass | Product `serves probe` returned two finite scores and ranked the matching document first. |
| OCR | Pass | Product `serves probe` recognized 200 characters including dashboard/GPU/health markers; measured delta drove a 6,144 MiB reservation. |
| ComfyUI | Pass | Dedicated `comfyui` stack reached `/system_stats`, reported one device, and stopped cleanly. |
| Workbench agent hub | Pass after packaging/connectivity fixes | `workbench build` produced `anvil-workbench:local` from corrected revision `590b088`; Postgres, Neo4j, hub `/healthz`, and the authenticated `llm.primary` sandbox request passed. The local stack remains running. |
| Voice STT/TTS | Pass | TTS produced an 83,496-byte WAV for a known sentence; STT returned `Anvil Voice Release Sweep.` |
| Voice audio benchmark | Pass | Dedicated Dark audio scope measured 272.58 ms round trip, WER 0.0, and TTS RTF 0.0524 without claiming Mini realtime coverage. |
| Laguna cache pull | Pass | Confirmed idempotent pull had zero missing bytes, retained 1 GiB headroom, and verified the exact pinned snapshot. |

Purpose-service and voice observations are retained in
[service-functional-smoke.json](2026-07-27-release-readiness-evidence/service-functional-smoke.json).

## Agents-A1 qualification

The test used
[`InternScience/Agents-A1`](https://huggingface.co/InternScience/Agents-A1) at
revision `addff08f1653ee72765c5cf458fe84556bb34f8e`. The checkpoint occupied
about 65.40 GiB. The first recipe load exposed a GPU-selection defect:
Docker's device request alone made both GPUs visible and vLLM selected the
32 GiB RTX 5090. After the loader enforced the same GPU UUID in both Docker's
device request and `CUDA_VISIBLE_DEVICES`, the model loaded on the RTX PRO
6000.

The loaded model used 64.69 GiB for weights and 20.43 GiB for KV cache. vLLM
reported 2,029,630 KV tokens and estimated 15.48-way concurrency at a
131,072-token request length.

| Run | Requests | TTFT p50 / p95 | E2E p50 / p95 | Aggregate output |
|---|---:|---:|---:|---:|
| [c1](2026-07-27-release-readiness-evidence/agents-a1-capacity-c1.json) | 4/4 | 0.30 / 0.44 s | 0.64 / 0.78 s | 85.19 tok/s |
| [c8](2026-07-27-release-readiness-evidence/agents-a1-capacity-c8.json) | 8/8 | 1.38 / 3.53 s | 3.23 / 4.09 s | 142 tok/s |

The [quality artifact](2026-07-27-release-readiness-evidence/agents-a1-quality.json)
records zero failures with thinking disabled. The release decision is therefore
**retain as a benchmark-qualified challenger whose serving contract disables
thinking**. Do not promote it from this sweep.

## Laguna Heavy recheck

The Heavy recheck used
[`poolside/Laguna-S-2.1-NVFP4`](https://huggingface.co/poolside/Laguna-S-2.1-NVFP4)
at revision `07614121b31898586430f189d27a25a0be310843`, served as
`laguna-s-2.1-nvfp4` with thinking disabled.

| Run | Requests | TTFT p50 / p95 | E2E p50 / p95 | Aggregate output |
|---|---:|---:|---:|---:|
| [c1](2026-07-27-release-readiness-evidence/laguna-capacity-c1.json) | 4/4 | 0.079 / 0.11 s | 0.53 / 0.61 s | 62.18 tok/s |
| [c8](2026-07-27-release-readiness-evidence/laguna-capacity-c8.json) | 8/8 | 2.22 / 2.48 s | 2.66 / 2.93 s | 86.68 tok/s |

The [quality artifact](2026-07-27-release-readiness-evidence/laguna-quality.json)
records no failures in the selected repeated suites. This smaller release sweep
corroborates the July 26 qualification; it does not replace that larger
promotion record.

The initial live container used a historical `heavy` name while the current
manifest expected `primary`. It was removed through the managed lifecycle.
After all tests, `serves up primary` recreated the manifest identity and the
endpoint reached HTTP 200. No router alias was promoted or reloaded.

## Purpose services and voice

Embeddings, reranker, and OCR each hit the same stale-container ownership
problem on the first `up`. The new stack contract exposed the mismatch,
previewed the exact replacement, and recreated each under `anvil-auxiliary`.
Each endpoint then passed the new product-native `serves probe` and was stopped
through `serves down`. ComfyUI independently used `anvil-comfyui` and passed
the same managed lifecycle/probe flow.

Voice used the reference ownership boundary: Dark hosted STT and TTS; no model
was placed on Mini. The initialized voice file now describes that topology,
the controller exposes the same ownership fields as CLI, and managed readiness
derives Parakeet's supported `/health` path.

The first TTS start failed with `Errno 28` after the Agents-A1 download filled
the Docker filesystem. At the time, the product had no exact one-model
cache-removal verb.
After validating the precise 66 GiB
`models--InternScience--Agents-A1` cache directory, only that directory was
removed from the named cache volume. Physical free space increased by 37 GiB;
the deleted snapshot is recoverable only by pulling it again. This historical
direct container operation motivated the now-tested
`models cache remove OWNER/REPO --revision COMMIT` workflow.

After recovery, managed voice start, readiness, logs, the TTS-to-STT round trip,
the audio-only benchmark, and managed stop all passed. The WER implementation
was corrected to treat capitalization and terminal punctuation as lexical
matches.

## Fixes made during the sweep

1. Recipe Docker commands now keep the selected GPU UUID and
   `CUDA_VISIBLE_DEVICES` consistent and reject an explicit mismatch.
2. `models recipes load` now waits for declared HTTP readiness even when cache
   reclaim is disabled, fails closed on timeout, and prints the served model
   name in its preflight hint.
3. benchmark evidence now retains protocol-v3 top-level intelligence, tool,
   session, and voice suites.
4. controller benchmark-artifact discovery falls back to the editable package
   workspace, and default model inventory uses the operator config home.
5. bounded `serves logs` output now decodes as UTF-8 with replacement instead
   of silently truncating on a Windows code-page error.
6. the controller voice-proxy lifecycle now imports the path module used by its
   default PID and log locations.
7. router Compose ownership is now fixed to project `anvil-serving` regardless
   of whether the selected Compose file lives in the operator home or the
   `examples/fakoli-dark` deployment directory. Foreign ownership requires an
   explicit `--recreate` reconciliation.
8. user-facing stacks now separate `serving`, `auxiliary`, `voice-audio`,
   `voice-proxy`, and `comfyui`, with path-independent Compose ownership.
9. `serves up` waits for readiness and `serves probe` validates embeddings,
   reranking, OCR/vision, and ComfyUI without raw HTTP.
10. model pull now gates free space and verifies snapshots; exact cache removal
    is available behind dry-run and confirmation.
11. the reference voice config/topology, controller ownership fields, Dark
    audio ports, audio benchmark scope, and lexical WER now agree.
12. OCR's measured 5,607 MiB delta is recorded and its reservation is 6,144
    MiB.
13. Workbench `build` owns the local companion-image build, while `up` waits
    for hub health and defaults to the stable `anvil-workbench:local` tag.
14. the companion Workbench hub image now ships `docs/contracts`; same-host
    Workbench joins the router's private Compose network and its authenticated
    sandbox-to-Serving path passes without widening the router bind.

The Workbench integration used the supported `router token --reveal --confirm`
flow and kept the credential only in the lifecycle process. No credential was
printed, copied into the repository, or retained in evidence. The separate
HTTP controller credential was not needed for this same-host Workbench path;
its schemas, handler forwarding, path defaults, and complete tool catalog
remain covered by the repository suite.

## Final topology

- Laguna `primary`: running, HTTP 200 on `127.0.0.1:30002`
- Router: running and healthy under Compose project `anvil-serving`; published
  only on `127.0.0.1:8000`
- Embeddings, reranker, OCR: test containers removed
- ComfyUI: test container removed
- Voice STT and TTS: test containers removed
- Agents-A1 experiment container: removed
- Workbench: running healthy from `anvil-workbench:local`; Postgres and Neo4j
  healthy, authenticated `llm.primary` sandbox passed
- Container cleanup: 43 stopped release-test and legacy experiment containers
  removed through `anvil-serving serves rm`; zero stopped containers remain
- No model or router promotion performed

## Verification

- Focused changed-area tests: `495 passed`
- Full test suite: `3,121 passed, 2 skipped`
- Ruff: all repository checks passed
- Full CLI reference audit: 480 files, zero violations, inventory and generated
  references current
- Strict MkDocs build: passed
- Relative Markdown links: 190 tracked Markdown files passed
- Companion Workbench: `1,288 passed`; production web build, Compose
  validation, corrected local image build, and authenticated `llm.primary`
  sandbox passed

The first full-suite run after the implementation found four stale contract
expectations: endpoint-owned voice benchmarking, the new default serving stack,
corrected Dark audio ports, and the regenerated repository inventory. Those
expectations were updated and the complete suite then passed. No test failure
was suppressed.
