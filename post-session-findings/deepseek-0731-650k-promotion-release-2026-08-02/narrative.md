# DeepSeek 0731 long-context promotion and 0.21.1 release retrospective

## Outcome

The session translated a community DeepSeek V4 Flash 0731 recipe to the local
two-card SM120/WSL2 topology, established reproducible 128K, 650K, and 1M
profiles, rejected the superficially successful 1M profile after real Pi
traffic crashed it twice, and promoted the stable 650K profile as
`llm.primary`. Pi on Fakoli Dark, Pi on Fakoli Mini, and OpenClaw on Mini all
passed through the promoted alias with high reasoning as the default. The
router gained a model-specific 32,768-token output cap that clamps oversized
requests with an explicit warning instead of silently mutating or rejecting
them.

The resulting release was 0.21.1. Release 0.21.0 shipped the benchmark,
promotion, client configuration, transactional router activation, and rollback
work. Live deployment then exposed a controller packaging defect: the hardened
controller Compose file did not mount the target and rollback router profiles
newly required by exclusive-mode manifests. PR #338 added the missing read-only
mounts and a manifest-derived regression test, and 0.21.1 restored exact
Windows, Mini, Dark controller, and router version parity.

Final live state was healthy: DeepSeek exclusively owned both GPUs, 65 other
managed GPU workloads were blocked, shared memory was clean, `llm.primary`
reported 650K context and concurrency 16, a high-reasoning smoke returned
`OK`, and OpenClaw discovered all 16 Anvil tools.

## Interaction analysis

This was an operator-led, highly interactive hardware-to-production run. The
operator set the direction and made the important product decisions in short
increments: move display output to the iGPU, preserve workstation CPU and RAM,
retry max sequences 16, compare 650K and 1M, benchmark every attempt, target a
single Pi coding user, use `llm.primary`, default to high reasoning, warn on
output clamping, and keep every other GPU stack blocked during exclusive TP=2
service.

The interventions were valuable because the problem changed as evidence
arrived. The clearest friction signals were the question about the missing
goal, the warning that compilation was consuming too much CPU and memory, the
correction from `llm.pi` to `llm.primary`, and the requirement to fix live
outages before closing the release. These were not cosmetic preferences; they
identified continuity, workstation coexistence, public contract, and release
readiness requirements that the workflow had not made explicit early enough.

## What went well

- The experiment varied the important knobs deliberately while keeping model,
  image digest, quantization, TP size, DSpark depth, and runtime utilization
  pinned. That made the 128K, 650K, and 1M results comparable.
- Moving the display to the AMD iGPU recovered enough device memory to run the
  upstream max-sequences-16 envelope. The workflow measured the resulting
  headroom instead of treating the hardware change as proof.
- The 1M profile was not promoted from its near-985K retrieval and synthetic
  Pi gates. Real Pi requests reproduced fatal B12X workspace allocation twice,
  including with only 5,120 requested output tokens. That client-shaped test
  changed the decision to the stable 650K profile.
- The final client contract was verified end to end on Dark Pi, Mini Pi, and
  Mini OpenClaw, with high reasoning, exact model identity, no silent fallback,
  and a live oversized-output clamp warning.
- Exclusive ownership remained mechanical rather than advisory: the promoted
  TP=2 serve owned both GPUs and the controller blocked 65 competing workloads.
- The release gate was broad and reproducible: 3,500 tests passed with 8
  skipped, plus Linux and Windows CI, Ruff, strict documentation, wheel smoke,
  Twine, and Greptile 5/5.
- When the controller regression appeared, the fix was durable. The regression
  test now derives every required exclusive router profile from the manifest
  and requires an exact read-only controller mount.

## What went wrong

- The session briefly lost its explicit goal, forcing the operator to ask where
  it went. A long experiment should keep the current objective and exit gates
  visible across compaction and hardware restarts.
- An early image build consumed essentially all CPU threads and roughly 60 GB
  of Docker memory. The operator had to request workstation headroom after the
  build started. Resource ceilings should be declared before compilation.
- The first recommendation favored 1M/maxseq16 because bounded retrieval and
  protocol tests passed. Those tests underrepresented real coding-client prompt
  and tool shapes, so the recommendation was overturned only during live Pi
  validation.
- The 0.21.0 release changed the controller's runtime file dependency closure
  without checking that every new router profile was present inside the
  hardened container. CI passed, yet the deployed controller rejected an
  otherwise valid promoted manifest.
- Release and deployment were too tightly coupled in one long session. The
  release artifact was published before a manifest-derived container filesystem
  check and exact endpoint-parity smoke had completed.
- The stock session-retro parser emitted an all-zero report because repeated
  Codex Desktop `session_meta` records were treated as replay-only. This report
  required an independent reconstruction from unique event identifiers.

## Where we got lucky

- The missing controller mounts failed immediately during the live deployment
  while the session was still active and the operator had explicitly required
  outage repair before closure. Without that timing, 0.21.0 could have remained
  published as a broken deployment path.
- The 1M profile's workspace failure appeared under real Pi traffic before it
  became the durable Primary. Its synthetic evidence was strong enough that a
  less representative gate could have promoted it.
- Moving display output to the iGPU recovered just enough VRAM for the desired
  envelope, but the retained post-reserve ledger headroom was only 94 MiB. The
  success depended on a narrow machine state and must not be generalized to
  co-residency or another host.

## Five Whys: why did 0.21.0 break the controller deployment?

1. The controller rejected the promoted exclusive-mode manifest because the
   referenced target and rollback router profiles did not exist in the
   container.
2. They did not exist because the hardened Compose deployment mounted the serve
   manifest but not the newly referenced router profile files.
3. The dependency was new because router activation became transactional with
   exclusive-mode entry and rollback in 0.21.0.
4. CI missed it because Compose and controller tests checked hardening and
   selected files, not the complete file dependency closure derived from the
   serve manifest.
5. The release workflow lacked a single readiness contract that joined source
   version, manifest-referenced files, container mounts, target-host config
   migration, endpoint version parity, and post-deploy client smokes.

The systemic fix is a release-readiness skill that treats the release and live
deployment as one staged evidence chain. It must inventory manifest file
dependencies before publishing, require source/controller/router/remote CLI
version parity, smoke the real client route with adequate visible-answer
headroom, and stop closure when any outage remains.

## Recommendations

### Now

- Use the new `anvil-serving-release-readiness` skill for coordinated releases
  that will be deployed to Mini and Dark. Its pre-publish and post-deploy gates
  make manifest dependency closure and endpoint parity explicit.
- Keep a real Pi/OpenClaw client-shaped smoke as a required promotion gate for
  long-context agentic profiles. Near-limit retrieval and protocol conformance
  are necessary but not sufficient.
- Declare build CPU and memory ceilings before compiling community runtime
  images on an interactive workstation.

### Next

- Add a product command that reports deployment dependency closure for a serve
  manifest and controller Compose file, rather than relying only on a skill and
  static test.
- Extend release validation to render the controller deployment in a disposable
  environment and verify every manifest-referenced file is readable at its
  container path.
- Improve session-retro parsing so repeated Codex Desktop metadata records do
  not erase valid turn, tool, and token evidence.

### Later

- Add a retained, sanitized client-shape corpus for Pi and OpenClaw so future
  long-context profiles can be challenged before any route mutation.
- Automate a bounded post-deploy parity packet covering installed CLI,
  controller, router, route identity, context/concurrency, exclusive ownership,
  shared memory, and OpenClaw MCP discovery.
