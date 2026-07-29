# Agents-A1 multimodal qualification

## Goal

Qualify Agents-A1 BF16, official FP8, and ProtoLabs NVFP4 on one RTX PRO
6000 for text, image, and video workloads without changing the production
route. Make every blocking fix durable in Anvil Serving and leave a reusable
repository skill.

## Completed

- Created `codex/agents-a1-qualification` in the isolated
  `anvil-serving-wt-agents-a1-qualification` worktree from current
  `origin/main`.
- Added a read-only named-volume and Docker storage inventory library, CLI
  verb, and MCP tool using schema `model-cache-inventory/v1`.
- Added atomic inventory artifact output and focused CLI/MCP tests.
- Captured pre-cleanup storage evidence.
- Removed five explicitly approved cached snapshots, reclaiming
  172,288,291,120 bytes.
- Removed only audited dangling, superseded, or rejected-candidate images and
  build cache older than seven days. No broad prune or volume deletion ran.
- Raised `/dev/sdd` available space from 15,447,490,560 to
  350,050,500,608 bytes and captured post-cleanup evidence.
- Added bounded direct `video_url` preflight support with matching MCP
  arguments, media identity, deterministic expectations, and no media-byte
  logging.
- Preserved same-dialect video blocks through the router, failed closed for
  unsupported cross-dialect video translation, and extended capacity scenarios
  with video counts and estimated visual tokens.
- Added opt-in routed media admission with independent content-free counting,
  one-video/four-image limits, fixed per-tier visual-token estimates, requested
  output headroom, precise pre-upstream rejection, and capacity-report parity.
  Existing tiers remain unchanged unless `media_admission_enabled=true`.
- Added `configs/agents-a1-qualification-router.toml` with one isolated alias,
  explicit qualification-only auth names, one-video/four-image admission, and
  no reference to the live Primary alias.
- Added `source-registry.json` with observed dates, age/evidence classes,
  hardware relevance, and decision impact for the three model variants,
  publisher discussion, vLLM/Qwen video guidance, MoE tuner source, and both
  Creative Commons fixtures.
- Added `eval benchmark multimodal` with strict
  `multimodal-corpus/v1` containment/hash/MIME/size validation and
  `multimodal-benchmark-evidence/v1` attempt and aggregate evidence.
- Added a 15-case corpus covering image, video, mixed media, 10/30/60/120
  second durations, and one-video/four-image requests. Synthetic fixtures
  regenerate deterministically with the pinned runtime image.
- Added two unmodified Wikimedia Commons videos as a bounded real-world
  supplement with author, license, URL, bytes, and SHA-256 attribution.
- Added the repository-owned `anvil-serving-kernel-tuning` skill and thin
  `.agents/skills` discovery wrapper. The skill decides when tuning is
  warranted, estimates work, preserves a default baseline, runs the official
  tuner in isolation, requires a paired end-to-end A/B, and accepts, rejects,
  or marks a tune inconclusive without changing a live route.
- Added the canonical
  `configs/kernel-tunes/<engine>/<engine-revision>/<gpu-slug>/` artifact
  contract with exact compatibility keys, `kernel-tune-manifest/v1`, explicit
  managed-recipe activation, and dated raw evidence links.
- Updated `AGENTS.md` so future sessions know where tunes and raw evidence
  belong, when a missing-config warning warrants tuning, which identity changes
  invalidate reuse, and that storage alone never activates a tune.
- Corrected the existing LLM qualification skill's UI prompt so explicit skill
  invocation uses `$anvil-serving-llm-qualification`.
- Fast-forwarded the qualification branch from `5367e8a` to current
  `origin/main` at `d59cf27` after the benchmark-docs portal merged. Preserved
  every campaign change, adopted the canonical benchmark publication skill,
  and reapplied the kernel-tune guidance to the updated `AGENTS.md`.
- Fast-forwarded again to the documentation-portal refactor at `e4c132b`.
  Reapplied the complete staged campaign without losing evidence, regenerated
  the CLI audit from source, and replaced the portal's stale Agents-A1
  placeholder with separate measured BF16, official FP8, and ProtoLabs NVFP4
  rows covering quantization, context/admission, TTFT, aggregate throughput,
  and reproducible recipe links.
- Added the campaign ticket to the hardware portal's `measured-on` audit after
  the new portal gate correctly flagged this ticket's PRO 6000 measurements as
  unclassified.
- Added the kernel tuner role and canonical skill/storage boundary to
  `docs/OPERATOR-SKILLS-AND-SUBAGENTS.md`.
- Documented `models cache inventory` in the generated command surface and
  workbench MCP catalogs, regenerated `CLI-COMMAND-MANIFEST.json`, and
  deliberately advanced the public MCP catalog fingerprint for the new
  read-only tool.
- Narrowed inventory container records to IDs, names, image identity, state,
  status, and creation time. Raw Docker labels are excluded because they can
  expose unrelated machine-local Compose paths without improving cleanup
  decisions.
- Published the dated campaign finding, findings index entry, run-catalog row,
  Agents-A1 model dossier, RTX PRO 6000 hardware view, and chronological
  BF16/FP8/NVFP4 comparison without changing any live recommendation or route.
- Completed all 18 official FP8 fused-MoE tuner batch sizes (34,560 candidate
  configurations) in 12,650 seconds. Stored the 3,294-byte artifact and
  `kernel-tune-manifest/v1` under the exact engine/GPU compatibility key.
- Built a pinned derived image, proved the exact tune loaded from startup logs,
  and ran paired default/tuned functional gates plus three warmed 8K c16 and
  128K c1 repetitions.
- Rejected activation of the tune. Primary aggregate throughput regressed
  1.399% and missed the 5% adoption gate, despite a 2.127% TTFT p95
  improvement and no protected-lane regression beyond the 3% limit.
- Added `capacity-v3` publication timing evidence: per-request and aggregate
  TTFT, effective prefill rate, generation duration, decode rate, mean
  inter-token latency, prompt/output tokens, E2E latency, and wall-clock
  duration with explicit client-observed methodology.
- Qualified the isolated router through direct video, same-dialect media
  fidelity, one-video/four-image admission, tools with video, SSE streaming,
  malformed media, and unsupported-dialect tests.
- Fixed caller-correctable upstream media rejections and unsupported
  cross-dialect video so both streaming and non-streaming requests return a
  sanitized 4xx before a streaming 200 is committed. Upstream 5xx and
  transport failures remain sanitized internal errors.
- Hard-set `enable_thinking=false` in the isolated tier after the first routed
  smoke consumed its 1,024-token budget in hidden reasoning. The rerun passed
  every routed preflight check.
- Renamed four mixed transcript/container-ID captures from `.json` to `.log`;
  they were evidence logs, not valid single JSON documents.
- Removed the exact isolated router process and campaign model container, then
  verified port 18000 closed, every pre-campaign managed serve remained absent,
  and `anvil-router` retained the same image ID, healthy running state,
  `127.0.0.1:8000` binding, and `unless-stopped` policy.

## Compatibility policy

The campaign targets the final product contract. Accidental legacy behavior
may be replaced when required for correctness, provided direct routing remains
explicit, unsupported media translation fails closed, promotion remains
human-gated, and the change is covered by independent tests.

## Verification

- `python -m pytest tests/test_models.py tests/test_mcp_foundations.py -q -x`
  — 128 passed after the cache inventory change.
- Repeated the cache-inventory focused pack after narrowing container records:
  128 passed, affected-file Ruff passed, and the three historical before/after
  inventory artifacts were mechanically normalized to the same public
  container schema. A bounded scan found no raw Docker command/label fields,
  Compose path labels, or `C:\Users` paths in those artifacts.
- `python -m pytest tests/test_preflight.py
  tests/test_mcp_preflight_parity.py -q -x` — 29 passed.
- `python -m pytest tests/router/test_image_fidelity.py
  tests/router/test_video_fidelity.py tests/router/test_model_capacity.py -q
  -x` — 21 passed.
- `python -m pytest tests/test_multimodal_benchmark.py
  tests/test_benchmark.py -q -x` — 151 passed.
- Regenerated the synthetic fixtures and revalidated all 15 manifest cases and
  21 media references against pinned hashes.
- Full pytest reached 3,261 passed, 2 skipped, and four deterministic contract
  failures: the command manifest, cache-inventory docs anchor, two workbench
  MCP catalogs, and public MCP fingerprint had not yet been advanced for the
  new surfaces. After correcting those records, the four exact regression
  tests pass and Ruff passes for the affected files.
- Focused cache, preflight, benchmark, kernel-skill, recipe, and router pack:
  364 passed.
- Benchmark portal and command-contract pack: 37 passed.
- Strict MkDocs passed after classifying the new measured-on finding and
  replacing a documentation-external ticket link with a repository path.
- Full CLI inventory update/check scanned 531 files with zero violations and
  `inventory=ok generated=ok`.
- A subsequent full suite reached 3,266 passed and 2 skipped. Its sole failure
  was the repository Markdown-link test reporting four references to the new
  finding because that validator intentionally inventories `git ls-files` and
  the campaign files had not yet been staged. The paths exist and strict
  MkDocs resolves them; stage the final artifact set before the final full
  rerun.
- Final router/benchmark/skill focused pack: 223 passed.
- Both repository skills passed the installed skill validator.
- Every retained evidence JSON parsed; the evidence directory is 1,242,081
  bytes.
- All 49 tracked TOML files and the reference/voice Compose surfaces parsed.
- Markdown link validation passed across 245 tracked Markdown files.
- Strict MkDocs passed.
- Ruff passed across `anvil_serving` and `tests`.
- Full CLI inventory regeneration/check scanned 549 files with zero violations
  and `inventory=ok generated=ok`.
- Final full pytest: 3,275 passed, 2 skipped.
- Restoration verified the exact production router identity and state and
  removed the isolated campaign process/container.
- Post-portal reconciliation: all three repository skills passed the installed
  validator; the new documentation/CLI contract pack passed 94 tests with 6
  skips; 247 tracked Markdown files passed relative-link validation; strict
  MkDocs passed; and the regenerated full CLI audit scanned 552 files with
  `violations=0 inventory=ok generated=ok nav=ok`.
- Final updated-base gates: Ruff passed and full pytest completed with 3,346
  passed and 8 skipped. The branch and `origin/main` both resolve to
  `e4c132b0b5662c71ca7db2ab542668019e75e146`; the intended campaign remains
  staged, `.scratch-agents-a1/` remains untracked, no campaign container
  remains, and `anvil-router` is still healthy on `127.0.0.1:8000`.

## Friction

- Wikimedia served the first two selected videos, then rate-limited two image
  downloads with HTTP 429. The downloaded files were retained and verified;
  the failed image downloads were not referenced by the corpus because the
  synthetic lane already covers those dimensions.
- vLLM emitted a missing hardware-specific FP8 MoE config warning for
  `E=256,N=512` on the RTX PRO 6000. The official tuner evaluates 1,920
  configurations for each of 18 batch sizes, so progress and duration must be
  reported per batch rather than treating 1,920 as the whole run.
- The first official tuner launch failed because Ray attempted to parse the
  Docker-visible GPU UUID as an integer. The isolated retry retained the exact
  Docker GPU constraint and exposed the physical device as numeric CUDA device
  1; the retry is progressing and the original failure will be retained as
  campaign evidence.
- The first full-pytest invocation used an accidentally short 10-second shell
  deadline. The harness killed pytest and Windows then reported an invalid
  output pipe; this is not a test failure. The suite was restarted as one
  hidden captured process with no competing pytest writer.
- The temporary official tuner used automatic container removal. The complete
  18-key artifact and write timestamp are durable, but there is no retained
  final container exit record or final stdout line.
- The first derived-image Dockerfile copied the repository-wide `configs/`
  directory because Docker resolved the source against the build context.
  The build was corrected to copy only the exact nested tune path; the
  in-image SHA-256 now matches the manifest.
- `serves logs` could not retrieve logs from a recipe-loaded container because
  it was not present in the operator serve manifest. The narrow read-only
  `docker logs` fallback retained startup proof; managed recipe-loaded log
  discovery remains a product gap.
- The first isolated-router preflight inherited default thinking and returned
  an invisible length-truncated smoke result. The qualification tier now pins
  the production thinking-disabled contract.
- Malformed upstream video and unsupported cross-dialect video were initially
  flattened to generic HTTP 500 responses. Typed, sanitized backend client
  errors now preserve bounded 4xx status for both streaming and non-streaming
  requests without exposing upstream bodies, hosts, or media bytes.
- `models recipes load` has no matching unload verb. The exact campaign
  container was therefore stopped and removed through a guarded Docker
  identity check during restoration; symmetric recipe unload remains a
  product lifecycle gap.
