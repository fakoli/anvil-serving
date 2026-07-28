# Release sweep fixes — 2026-07-27

Baseline: `main` at `58f7809588a603f2c7b41a7684c40fc02f77315c`
(upstream `59afaa7572162e74da17969d55b7fa31b789ef6e` plus the rebased
operator-topology default change).

This is a single-user deployment. Compatibility breaks are acceptable when
they make the installed CLI and controller converge on one clear operator
workflow. Every break still requires an explicit migration note and regression
evidence.

## Fix queue

### F001 — MCP paths inherit the Codex app directory

- Observed: `models_inventory` defaulted to a `WindowsApps/.../model-library`
  path, and `benchmark_artifact` rejected a repository evidence path because
  the MCP process could not identify the Anvil Serving workspace.
- Impact: controller-backed model sync and benchmark artifact retention cannot
  use the same defaults as the installed CLI.
- Intended fix: make controller defaults resolve through operator config or an
  explicit project/evidence root, independent of process current directory.
- Status: fixed locally; regression tests pass. The already-running controller
  must reconnect before the new module is live there.

### F002 — live serve identity can drift outside the manifest

- Observed: the live Heavy container was `vllm-laguna-s-heavy`, while
  `serves.toml` expected `vllm-laguna-s-primary`; `serves status` reported the
  configured serve absent even though the GPU was using about 88 GiB and port
  30002 was healthy.
- Impact: status and lifecycle commands do not explain occupied resources or
  provide a managed reconciliation path.
- Intended fix: surface conflicting/unmanaged containers by port, GPU, and
  Compose identity, then provide one explicit reconciliation action.
- Compatibility decision: status now reports same-port conflicts and durable
  Compose ownership. The historical Heavy was reconciled to the manifest-owned
  `primary` container through the product lifecycle; Laguna and router remain
  healthy.
- Status: fixed locally and reconciled live.

### F003 — benchmark evidence summary drops quality suites

- Observed: a valid protocol-v3 Laguna artifact contained passing `tool`,
  `session`, and `intelligence` sections, but `eval benchmark evidence show`
  normalized `quality.suites` to an empty list.
- Impact: release reviewers can mistake completed quality evidence for an empty
  run.
- Intended fix: normalize both current protocol-v3 top-level suite sections and
  legacy suite layouts, with regression fixtures.
- Status: fixed locally; regression tests pass and the Laguna/Agents-A1
  artifacts now retain their completed quality suites.

### F004 — recipe load reports success before readiness

- Observed: `models recipes load` returned success in about two seconds while
  Agents-A1 was still loading for several minutes. The advertised 600-second
  health wait only ran as a side effect of enabled page-cache reclamation.
- Impact: automation can run preflight against an unavailable endpoint and
  mistake a successfully created container for a usable model serve.
- Compatibility decision: a declared HTTP serve now waits for health
  regardless of cache policy and returns non-zero on timeout. The failed
  container remains available for bounded log inspection and explicit managed
  removal.
- Status: fixed locally; focused regression tests pass.

### F005 — recipe GPU selection omits the CUDA visibility pin

- Observed: `--gpus device=<Windows GPU UUID>` alone exposed both GPUs under
  Docker Desktop and vLLM selected the 32 GB RTX 5090, causing Agents-A1 to
  OOM. The known-good Compose contract pins both the device request and
  `CUDA_VISIBLE_DEVICES`.
- Impact: a recipe can name the correct 96 GB GPU yet execute on another card.
- Compatibility decision: loaders now synthesize
  `CUDA_VISIBLE_DEVICES=<gpu_uuid>` and reject an explicitly conflicting value.
- Status: fixed locally; Agents-A1 selected the RTX PRO 6000 and reached model
  loading, and focused regression tests pass.

### F006 — model pull does not fail closed on insufficient disk

- Observed: the pinned Agents-A1 pull ran for about 16 minutes, repeatedly
  warned that available space had reached 0 MB, then returned success without
  incremental CLI progress.
- Impact: artifact completeness and operator wait state are ambiguous.
- Compatibility decision: confirmed pulls resolve remote or trusted expected
  bytes, inspect the named-volume snapshot, retain explicit headroom, preserve
  native `hf download` progress/resume, and verify exact snapshot completeness
  after download.
- Live verification: the pinned Laguna pull passed idempotently with zero
  missing bytes and verified the exact `076141...` snapshot.
- Status: fixed locally, unit-tested, and verified live.

### F007 — recipe load preflight hint uses the repository model id

- Observed: Agents-A1 was served as `agents-a1`, but the next-step command used
  `InternScience/Agents-A1`.
- Impact: the generated validation command targets an unknown served model.
- Intended fix: prefer `recipe.serve.served_model_name`, falling back to the
  repository model id only when no served alias is declared.
- Status: fixed locally; focused regression tests pass.

### F008 — stopped same-name containers can block `serves up`

- Observed: the first managed `up` for embeddings, reranker, and OCR failed
  because an exited container with the declared name already existed under a
  different Compose project identity. Managed `serves rm` followed by
  `serves up` recovered each service.
- Impact: the normal lifecycle command fails with a low-level Docker name
  conflict instead of identifying the stale ownership and proposing the
  product-native reconciliation action.
- Compatibility decision: each serve now declares a user-facing `stack`,
  mapped to the durable `anvil-<stack>` Compose project. `up` refuses foreign
  ownership and gives the exact `--recreate` recovery; recreation remains an
  explicit operator decision.
- Live verification: embeddings, reranker, and OCR were each previewed,
  recreated under `anvil-auxiliary`, functionally probed, and stopped. Status
  reports no remaining ownership mismatch.
- Status: fixed locally and reconciled live.

### F009 — `serves up` acknowledges creation before readiness

- Observed: purpose-model and Laguna `up` calls returned success while health
  was still unknown. Laguna required roughly three minutes of load and warm-up
  after the successful return.
- Impact: callers can treat a created container as a usable endpoint.
- Compatibility decision: CLI `serves up` now waits for every selected
  declared health endpoint and fails closed on timeout. The library seam keeps
  an explicit opt-in for hermetic callers.
- Live verification: embeddings, reranker, OCR, and ComfyUI each returned only
  after their declared health endpoint passed.
- Status: fixed locally and verified live.

### F010 — Windows log decoding can fail while returning success

- Observed: `serves logs primary` hit a CP1252 `UnicodeDecodeError` in the
  subprocess reader thread on vLLM output, printed an incomplete log, and still
  exited zero.
- Impact: diagnostics can be truncated without a failing command status.
- Compatibility decision: bounded Docker logs are decoded as UTF-8 with
  replacement for malformed bytes, matching the service/log transport
  contract instead of the Windows console code page.
- Status: fixed locally; regression test, Ruff, and a live Laguna log read pass.

### F011 — initialized voice defaults do not describe the reference topology

- Observed: the installed `voice.toml` is a generic unmanaged scaffold with no
  profiles. The reference topology distinguishes Dark-local managed audio
  ports `30010`/`30011` from Mini-local proxy ports `30110`/`30111`, but the
  default lifecycle wrapper could not infer this deployment. The MCP
  `voice_manage` surface also cannot express command host, command runtime,
  target, or transport, so the explicit CLI dispatcher was required.
- Impact: an initialized operator cannot run the documented Dark-owned managed
  voice lifecycle through the default controller arguments.
- Compatibility decision: `init` now ships a canonical Dark-managed
  `voice.toml`; MCP `voice_manage` accepts the same topology overlay, command
  host/runtime, target, transport, and experimental-workload fields as CLI.
- Status: fixed locally with scaffold, CLI, and MCP regression coverage.

### F012 — STT readiness defaults to an unsupported endpoint

- Observed: Parakeet returned HTTP 200 from `/health` but 404 from `/v1/models`.
  The managed voice test required an explicit STT `ready_url`; TTS supported
  both endpoints.
- Impact: a healthy STT service can be classified as unready by the default
  voice manifest.
- Compatibility decision: managed voice readiness is derived from the selected
  serve manifest, whose canonical STT and TTS health path is `/health`.
- Live verification: both Dark audio serves reported ready through the managed
  voice status path.
- Status: fixed locally and verified live.

### F013 — no product verb removes one named model-cache snapshot

- Observed: the Agents-A1 pull filled the Docker filesystem. The read-only MCP
  cache plan cannot delete, and the CLI prune surface did not provide a safe
  exact-repository removal. Voice TTS then failed with `Errno 28`.
- Impact: recovering from one oversized test artifact requires leaving the
  Anvil Serving operator surface.
- Recovery used: after validating the exact 66 GiB
  `models--InternScience--Agents-A1` cache directory, it was removed from the
  named vLLM cache volume and 37 GiB of physical free space was recovered. The
  snapshot is recoverable only by pulling it again.
- Compatibility decision: `models cache remove OWNER/REPO --revision COMMIT`
  now provides a dry-run plan, exact selector, human gate, unreferenced-blob
  collection, and post-delete snapshot verification.
- Live verification: the Laguna dry-run resolved only the pinned snapshot and
  reported 71,938,566,800 reclaimable bytes; no production bytes were removed.
- Status: fixed locally and safely verified live.

### F014 — end-to-end voice benchmark topology cannot target Dark audio alone

- Observed: the STT/TTS round trip passed, but `voice benchmark` failed before
  execution because target `fakoli-dark` has zero declared owners for
  `realtime-proxy`. The reference proxy is Mini-owned.
- Impact: audio-stage qualification and full Mini-to-Dark voice qualification
  cannot be expressed as separate benchmark scopes.
- Compatibility decision: `voice benchmark --scope audio` directly measures
  the Dark-owned TTS-to-STT round trip and retains non-promotion
  voice-pipeline evidence without requiring the Mini-owned realtime proxy.
- Live verification: 272.58 ms round trip, 150.71 ms TTS, 121.86 ms STT,
  WER 0.0, and TTS RTF 0.0524.
- Status: fixed locally and verified live.

### F015 — OCR observed usage is close to or above its reservation

- Observed: with OCR as the only substantial workload on the auxiliary GPU,
  total device usage was about 6,028 MiB against a 5,120 MiB OCR reservation.
  The device total includes the roughly 400 MiB host baseline, so this is not a
  container-only measurement.
- Impact: the current ledger leaves little or no demonstrated headroom for the
  OCR serve.
- Evidence: an otherwise-quiescent auxiliary-device baseline was 1,570 MiB;
  ready OCR plus a real image request was 7,177 MiB, for a 5,607 MiB
  attributable delta.
- Compatibility decision: the OCR reservation is now 6,144 MiB, leaving
  537 MiB over the observed delta while the complete resident set still fits
  the declared budget.
- Status: fixed locally, documented, and verified live.

### F016 — controller voice-proxy defaults raise `NameError`

- Observed: the full Ruff gate found `tool_voice_proxy_manage` using
  `os.path.join` for default PID and log paths without importing `os`.
- Impact: proxy lifecycle calls that reach the default-path construction fail
  before returning a typed controller result.
- Status: fixed locally by importing the required standard-library module;
  full Ruff and MCP tests cover the module.

### F017 — router Compose ownership follows the source directory name

- Observed: the live `anvil-router` carried
  `com.docker.compose.project=fakoli-dark` because it had been created from
  `examples/fakoli-dark/docker-compose.yml`. The restored Laguna `primary`,
  created from the installed operator directory, already belonged to
  `anvil-serving`.
- Impact: the same Anvil Serving resource appears under different Compose
  owners depending on the directory containing the selected file. Lifecycle
  and cleanup behavior can therefore diverge even though the container name
  and product are identical.
- Compatibility decision: the canonical Compose file now declares
  `name: anvil-serving`, and router up/down commands always pass
  `--project-name anvil-serving`. A foreign-owned router is refused with an
  explicit recovery message unless `router up --recreate` authorizes replacing
  that exact container.
- Live reconciliation: the first apply correctly stopped at the CLI
  confirmation gate. The confirmed recreate replaced only `anvil-router`;
  its external config volume remained in place. Docker now reports project
  `anvil-serving`, the container is healthy, the loopback `/v1/models` endpoint
  returns the expected unauthenticated HTTP 401, and port 8000 remains bound
  only to `127.0.0.1`.
- Status: fixed locally and reconciled live; focused regression and full
  release gates are required after the change.

### F018 — WER treated capitalization and punctuation as speech errors

- Observed: the live audio round trip transcribed the reference sentence
  exactly but capitalized the first word and added terminal punctuation,
  producing a false WER of `0.2222`.
- Compatibility decision: WER tokenization now case-folds lexical tokens and
  ignores punctuation that does not represent a spoken word.
- Live verification: the same TTS-to-STT sentence now records WER `0.0`.
- Status: fixed locally and verified live.

### F019 — Dark audio topology reused Mini proxy loopback ports

- Observed: `dark-stt` and `dark-tts` declared `30110`/`30111`, which are the
  Mini-local forwarding ports, while the Dark-managed audio serves listen on
  `30010`/`30011`.
- Compatibility decision: source, scaffold, and installed topology now use
  `30010`/`30011` for Dark ownership and reserve `30110`/`30111` for Mini
  proxy resources.
- Live verification: managed status reached both Dark health endpoints.
- Status: fixed locally and verified live.

### F020 — use-case ownership was expressed as incidental Compose projects

- Observed: services inherited project labels from a directory or shared the
  broad `anvil-serving` owner, so voice, auxiliary, and ComfyUI cleanup could
  not be reasoned about as separate product boundaries.
- Compatibility decision: manifests expose `stack`; the product maps it to
  `anvil-<stack>`, rejects contradictory explicit project flags, and reports
  stack mismatches. Canonical stacks are `serving`, `auxiliary`,
  `voice-audio`, `voice-proxy`, and `comfyui`.
- Live verification: Laguna/router remain under `anvil-serving`; purpose
  services use `anvil-auxiliary`; audio used `anvil-voice-audio`; ComfyUI used
  `anvil-comfyui`.
- Status: fixed locally and reconciled live.

### F021 — purpose-service functionality required raw HTTP

- Observed: managed lifecycle and health were available, but functional
  embeddings, reranker, OCR, and ComfyUI checks required leaving the product
  surface.
- Compatibility decision: new read-only `serves probe` selects an
  engine-appropriate request, validates the response shape, bounds output, and
  never emits image bytes.
- Live verification: embeddings, reranker, OCR, and ComfyUI all passed through
  `serves probe`.
- Status: fixed locally and verified live.

### F022 — cache inspection assumed an image exposed `python`

- Observed: the exact cache-removal dry-run failed against the qualified
  Laguna vLLM image because it exposes `python3`, not `python`.
- Compatibility decision: the inspector uses a small `sh` entrypoint shim that
  prefers `python3`, falls back to `python`, and fails clearly if neither
  exists.
- Live verification: the Laguna exact-snapshot dry-run and subsequent
  idempotent pull verification both passed with the production image.
- Status: fixed locally and verified live.

### F023 — Workbench up returned before the hub was usable

- Observed: `workbench up` returned zero once Compose created the containers,
  but the `latest` hub immediately crashed with
  `plugin-receipt contract schema is unavailable`; the browser endpoint never
  opened.
- Compatibility decision: Workbench now has a real loopback `/healthz`
  healthcheck and `up` uses Compose `--wait` with a bounded configurable
  timeout. The default image is the explicit locally built
  `anvil-workbench:local` tag, never mutable registry `latest`.
- Live verification: the broken `latest` image now failed closed with return
  code 1 and was managed down. The pinned image brought Postgres, Neo4j, and
  the hub healthy, returned `{"ok":true,"service":"anvil-workbench"}` from
  `/healthz`, and was managed down while preserving its test volumes.
- Companion fix: Workbench `deploy/Dockerfile.hub` now copies the complete
  `docs/contracts` tree. A local image built from revision `590b088` plus that
  fix brought Postgres, Neo4j, and the hub healthy without the schema crash.
- Status: fixed locally in both repositories and verified live.

### F024 — Workbench could not reach loopback-published Anvil Serving

- Observed: the router credential was available through
  `router token --reveal --confirm`, but the first authenticated Workbench
  sandbox request returned `Anvil Serving is unreachable: [Errno 111]
  Connection refused`. The configured tailnet address was not listening
  because the router is intentionally published only on host loopback.
- Compatibility decision: same-host Workbench joins the stable
  `anvil-serving_default` Docker network and uses
  `http://anvil-router:8000/v1`. The packaged template now passes through the
  existing bounded sandbox allowlist and loopback-only development actor
  switches. No router bind was widened.
- Credential handling: the deployed router token was captured into the
  Workbench lifecycle process only. It was not printed, logged, written to a
  config file, or added to evidence.
- Live verification: managed Workbench start reached healthy for Postgres,
  Neo4j, and the corrected hub image. `POST /api/sandbox` selected
  `llm.primary`, traversed the authenticated Anvil Serving router, and Laguna
  returned exactly `WORKBENCH_SERVING_OK`. Managed down removed all test
  containers and preserved the documented named volumes.
- Status: fixed locally and verified end to end.

### F025 — Workbench image build was outside the Anvil Serving lifecycle

- Observed: bringing up the corrected companion source still required a raw
  `docker build`, while durable local operations are required to live behind
  the product CLI. This single-user installation does not use a container
  registry for Workbench.
- Compatibility decision: `workbench build` now validates the local companion
  checkout and its hub Dockerfile, supports dry-run and confirmation, and
  builds `anvil-workbench:local` by default. `ANVIL_WORKBENCH_SOURCE` or
  `--source` can select a different checkout; `ANVIL_WORKBENCH_IMAGE` or
  `--image` can select a different local tag.
- Live verification: the new verb built current
  `C:\Users\sdoum\ai-code\anvil-workbench`, managed `workbench up` started that
  exact local tag, and the healthy authenticated sandbox returned
  `WORKBENCH_SERVING_OK`. The stack was intentionally left running.
- Status: fixed locally and verified live.

### F026 — Release testing left stopped containers behind

- Observed: Docker retained 43 stopped containers from the release sweep and
  older local experiments. They included the managed auxiliary, ComfyUI, and
  Dark STT/TTS services; Agents/GPT-OSS and other candidate experiments;
  `fakoli-experiment`, `fakoli-flexibility`, and `fakoli-models` services;
  standalone legacy STT/TTS containers; and two obsolete Workbench services.
- Cleanup: the exact stopped-only target set was validated, previewed, and
  removed through `anvil-serving serves rm --confirm --yes`. No running
  container, image, volume, model cache, or router/model promotion was touched.
- Live verification: Docker now reports five containers total, all running:
  Laguna Primary, the Anvil router, Workbench, Workbench Postgres, and Workbench
  Neo4j. It reports zero exited, created, or dead containers. Router, Laguna,
  and Workbench health checks still pass.
- Status: cleaned and verified.

## Applied fixes

- F001: controller artifact paths now fall back to an editable package
  workspace; model-catalog defaults now resolve under the operator config home.
  The already-running MCP process still has the old module loaded and requires
  a normal controller reconnect before live re-verification.
- F003: protocol-v3 top-level `tool`, `session`, and `intelligence` sections are
  normalized when the legacy `suites` table is empty.
- F004: recipe load readiness is unconditional for HTTP recipes and
  fail-closed.
- F005: recipe Docker loads enforce matching NVIDIA device selection and CUDA
  visibility.
- F007: generated preflight commands use the served model name.
- F010: bounded serve logs use deterministic UTF-8 decoding with replacement.
- F016: controller voice-proxy default path construction imports its required
  standard-library module.
- F017: router Compose ownership is path-independent and fixed to
  `anvil-serving`, with explicit foreign-owner reconciliation.
- F002/F008/F009/F020: serve stacks, ownership diagnostics, explicit
  reconciliation, and fail-closed readiness are live.
- F006/F013/F022: model pull and exact cache inspection/removal are
  space-aware, verifiable, and compatible with the qualified image.
- F011/F012/F014/F018/F019: Dark voice ownership, readiness, audio-only
  benchmarking, topology ports, and lexical WER are aligned and live.
- F015: OCR has measured evidence and a 6 GiB reservation.
- F021: purpose-service functional checks no longer require raw HTTP.
- F023: Workbench lifecycle is readiness-gated and uses a live-qualified
  local image instead of registry `latest`; the companion release-image
  packaging defect is fixed locally.
- F024: Workbench uses private Compose DNS for same-host Serving access, and
  the authenticated sandbox path is verified end to end.
- F025: Workbench builds directly from the companion checkout through the
  product CLI and runs the stable local image tag.
- F026: all 43 stopped test and legacy containers were removed through the
  product CLI; only the five healthy production containers remain.

Focused verification before the final full gate: `159 passed` across recipe,
models, and benchmark evidence tests, `16 passed` for MCP foundation/path
boundaries, and `32 passed` for serve lifecycle/log regression coverage.

Final repository gates after the complete F001-F026 change set:

- focused changed-area tests: `495 passed`
- full suite: `3,121 passed, 2 skipped`
- Ruff: passed
- strict MkDocs: passed
- relative links: 190 tracked Markdown files passed
- full CLI audit: 480 files, zero violations, generated manifest and inventory
  current
- companion Workbench: `1,288 passed`; production web build, Compose
  validation, corrected local image build, and authenticated live sandbox
  passed
