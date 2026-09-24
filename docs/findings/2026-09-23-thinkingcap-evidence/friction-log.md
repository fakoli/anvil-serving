# Friction and fix-forward record

Chronological record: early sections describe superseded preparation states. The RTX5090 campaign subsequently reached healthy serving, text/tools/vision gates and measured capacity. Dates and later closure paragraphs supersede earlier blockers.

| Issue | Earliest observation | Durable follow-up and independent closure |
|---|---|---|
| Gated artifact access | config.json: HTTP 401 GatedRepo | User grants access and provisions HF_TOKEN; exact managed pull succeeds |
| Remote host identity | SSH host-key verification failed | Trusted fingerprint verification; normal authenticated inventory succeeds |
| Missing local model owner | Recipe inventory safety refusal | Reviewed private candidate topology; managed validation/inventory passes |
| Controller command-host mismatch | Unknown host in saved topology | Converge topology with actual execution identity; identity-checked read passes |
| Missing MCP coverage | No Anvil tools in session catalog | Verified CLI fallback; MCP setup remains outside this campaign |
| Executing source mismatch | Original 1.2.1 vs isolated 1.0.0 | Pin final host launcher before inference; preserve unrelated dirty work |

These were discovery-stage observations. Later entries record their resolution or exclusion from the corrected RTX 5090 campaign.

## Credential and transport correction

The missing dotenv observation applied only to the local Windows profile. The existing model-host user home .env was found and used by the managed pull. Existing trusted companion-host SSH resolves the remote access path without disabling host-key checks. A separate validated private campaign topology supplies missing catalog/cache roles without changing the deployed topology. Model-host CLI identity is 1.2.1. Those paths supersede the earlier discovery blockers for this campaign.

The authenticated exact-revision pull passed its disk gate, then failed with: Access denied. This repository requires approval. This is repository authorization, not missing credentials. Deferred fix-forward: account owner grants access to the selected AWQ repository; independent closure requires the managed pull and exact snapshot verification to succeed. Both GPUs remain occupied by the original GLM serve. Fleet Exec also rejected legitimate credential-reference flags and revision hashes; the same visible arguments were executed through ordinary trusted SSH and the product CLI, without transmitting token values.

## Access resolved and candidate prepared

The authenticated managed pull succeeded and independently verified the complete pinned snapshot. Vision config and 333 indexed visual tensors are present. The pinned vLLM 0.29.0 candidate registry and load preview validate. GPU occupancy still requires an exact restoration plan and the human interruption gate. Operator config inventory rejects a symlinked home and then a symlinked dotenv; bounded export is the supported fallback. Recipe status exposes semantic and registry digests but no original registry path, so restoration provenance must be recovered before unloading.

## Shared GPU conflict

Exact B4096 GLM registry and recipe hashes were recovered from the owning private campaign directory. A fresh managed inventory then showed B8192 replacing it, and that campaign telemetry was still being updated. The earlier baseline is therefore stale. No ThinkingCap serving mutation is permitted until ownership is coordinated and a fresh restoration plan is validated. The installed CLI also lacks the GLM recipe memory-limit renderer used by the other campaign; it cannot be assumed to reproduce that workload. Narrow read-only container inspection established the current containment settings without recording credentials.

## Evaluation target correction

The user clarified the RTX 5090 target. Prior PRO 6000 preparation was an agent targeting mistake, not a user-approved alternate evaluation. No inference was run. Local Docker Desktop is running and managed inventory identifies an existing Qwen Huihui NInfer serve. A separate private candidate topology declares model roles without changing the deployed topology. The GLM campaign has no bearing on this evaluation.

## Authorization and chronology correction

The earlier remote preparation and stale human-interruption gates belong to the superseded PRO 6000/GLM line of work. The user has since authorized the RTX 5090 candidate campaign and its managed startup recovery autonomously. That authorization is limited to the current candidate operation; it does not authorize route changes or promotion, which retain their independent human gate.

The operational launcher for the actual campaign is the original dirty checkout at CLI 1.2.1. This isolated CLI 1.0.0 worktree is documentation-only and did not launch or inspect the candidate.

## RTX 5090 startup: V2 runner UVA failure and in-progress recovery

The initial 16K eager vLLM 0.29.0 calibration recipe is retained at `native/startup/initial-uva-failure-recipe.toml`, with its startup log at `native/startup/startup-16k-eager-uva-failure.log`. The default V2 model runner failed before model-weight loading with `RuntimeError: UVA is not available` while creating `UvaBuffer`.

The pinned vLLM source documents `VLLM_WSL2_ENABLE_PIN_MEMORY=1` for WSL2 when pinned memory or UVA is required, including the V2 model runner. The managed recipe retry added only that environment setting. The retry passed the former UVA point and is loading weights; it is not healthy yet, so this is recovery progress rather than a completed startup or qualification result.

The first Docker image inspection falsely reported a mismatch when inspecting the tag-plus-digest reference. A bare canonical digest inspection then succeeded. Retain the canonical digest evidence; treat the tag-plus-digest inspector result as a tooling behavior, not an image-identity failure. The durable product-gap record is [image-tag-digest canonicalization](product-gaps/2026-09-23-image-tag-digest-canonicalization.md).

## RTX 5090 startup healthy; functional evidence is overlap-limited

The managed 16K eager candidate reached health. `native/startup/startup-16k-wsl.log` records 21.34 GiB model-loading memory, 5.67 GiB available KV-cache memory, and a GPU KV cache capacity of 80,554 tokens. `native/candidate/eager/candidate-load-16k-wsl.log` and `native/candidate/eager/candidate-models-16k.json` retain the sanitized managed-load receipt and served identity.

The candidate preflight was accidentally submitted twice after the harness's first wait interval elapsed. The first raw JSON was overwritten, so its latency evidence is unavailable. The retained second run passed 27 observations, but it may have queued behind the first run; `native/candidate/eager/preflight-duplicate-incident.json` makes this limitation explicit. Both preflights completed before the sole multimodal corpus process, and root process inspection verified one process chain. The retained preflight and 18-image corpus result are therefore functional evidence only, with no latency claim.

Durable execution invariant: retain and poll an existing process-session identifier after a harness wait timeout; use unique output names; do not submit another run until completion of the first is proven.

## Quality launcher control proof

Two input-validation attempts were rejected before any model requests: a native preflight artifact was supplied directly where `anvil-serving.control-evidence/v1` is required, then the legacy `--bakeoff` flag was supplied to the canonical `quality` action. The canonical action already selects the quality engine.

Durable invariant: bind a structured control-evidence index to the exact native reasoning-off and reasoning-on proofs and use the documented `eval benchmark quality` flags. The [graph control index](native/candidate/graphs/graph-thinking-control.json) references both successful preflights; the corrected quality run validates that index. Missing streaming token-usage fields are not assumed to be zero: the disabled proof uses observed reasoning text and the preflight forbidden-reasoning gate. The earlier failures were local parser/assertion failures, not model failures.

## Quality sampling boundary and budget exhaustion

The graph MMLU-Pro 10 diagnostic passed 27/30 attempts; the computer-science item exhausted 10,240 completion tokens on all three repetitions. Its native failure remains failed. The quality CLI fixes temperature 0 and rejects temperature/top-p/top-k options, so this campaign cannot reproduce the publisher sampling protocol. No causal link between deterministic decoding and exhaustion is established. The durable [sampling-controls ticket](product-gaps/2026-09-23-quality-sampling-controls.md) and [source inspection](quality-sampling-gap.json) record acceptance criteria. Custom-suite inspection also emits validation messages for intentionally unexecuted built-in suites; those are separate from the real 27/30 outcome.

## Completed recovery and comparison dispositions

- **Access and exact cache: closed.** The local campaign retained the complete pinned 26-file snapshot, including vision and MTP weights. Earlier remote access work is preparation only.
- **UVA startup: closed by recipe.** Every successful local arm contains the pinned runtime's WSL pin-memory environment control. Health, model identity, functional and vision gates independently passed after the managed reload.
- **Restoration provenance: recovered, product gap tracked.** The exact original recipe and registry hashes and managed restore preview are retained privately. The [provenance ticket](product-gaps/2026-09-23-recipe-restoration-provenance.md) records the missing product field.
- **Strict output failures: excluded.** The original 256-word scouts failed; the revised 32-word protocol was declared before clean finalist comparisons. The Flash-target MTP arm later failed one of 100 strict requests and remains performance-ineligible. No retry replaces either failure.
- **Draft backend ambiguity: corrected by explicit target control.** Pinned V2 source review showed that a nested draft-backend setting alone did not establish the effective backend. The new recipe sets target `TRITON_ATTN`; startup logs independently confirm it. [Source review](fp8-v2-review.json) and [BF16 results](triton-bf16-result-summary.json) retain the correction.
- **Small-suite quality: qualified within scope.** Explicit target Triton with MTP3 and BF16 KV passed all 30 repeated diagnostic observations. That is a new configuration result, not a reclassification of the failed earlier arms or a full MMLU-Pro score.
- **Documentation verification capture: repeat required.** The first full-suite execution lost its terminal output. Its outcome remains inconclusive in [initial validation](validation-initial.json). Final checks must capture command output to durable files before launch.

## Final workflow and validation dispositions

- **Durable job owner: closed.** Initial context preflight refused a topology with no evaluation resource. A separate validated private evaluation topology declares the local native client; the model remains on the same RTX 5090. The subsequent preflight passed all seven checks. Deployed topology was unchanged.
- **Submit preview help: tracked gap.** The CLI advertised `--dry-run` but its submit parser rejected it before job creation. The reviewed specification and passing native preflight preceded the authorized confirmed submit. See the [help/parser ticket](product-gaps/2026-09-23-benchmark-submit-preview-help.md).
- **Windows Pi repository test: deferred outside model scope.** The full wrapper failed after 7,923 passes and 417 skips. Independent probes found missing SystemRoot in the test environment and an incompatible anonymous-pipe reader. The exploratory fixture patch was insufficient and was reverted; model and production sources remain unchanged. The [independent diagnosis](validation-pi-failure.json) and [follow-up ticket](product-gaps/2026-09-23-windows-pi-rpc-validation.md) preserve both causes. No full-suite pass is claimed.

## Final publication encoding and links

Strict MkDocs found copied ticket/recipe links outside the docs tree and later three CP1252 Markdown files. Public recipe/ticket copies now use in-tree relative links; the three derivative documents were converted explicitly to UTF-8. Failed validation attempts remain retained. The final strict build, link checker and 19 focused documentation tests passed. No serving or benchmark source changed.
