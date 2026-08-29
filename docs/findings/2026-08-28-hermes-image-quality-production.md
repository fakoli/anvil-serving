# Hermes image-quality profiles and production enablement

**Date:** 2026-08-28

**Scope:** production Anvil media gateway on Fakoli Dark, managed on-demand
ComfyUI worker on one RTX 5090 in Fakoli Mid Mod, and the real Hermes client on
Fakoli Mini; c1 text-to-image execution

**Decision:** the exact FLUX.2 Klein workflow is production-enabled through
three fixed quality profiles; `available=true`, `promoted=false`. The Wan2.2
video workflow remains unavailable with `quality_failed`; no fallback exists.

<!-- benchmark-result-card/v1 -->
## Result card

> Real Hermes produced eight independently reviewed FLUX.2 images through the
> authenticated MCP gateway, including all three fixed quality profiles and
> five cold approval/resume paths. Six passed strict bounded prompt-adherence
> review; two additional draft samples completed technically but exposed
> material-style and exact-count limitations. Video remains fail-closed.

| Setup | Qualified value |
|---|---|
| Model | FLUX.2 Klein 4B FP8 `5b4408e5`; Qwen3 4B encoder and FLUX.2 VAE `5f526678`; immutable graph `991b63b8...2e4f` |
| Hardware | one NVIDIA RTX 5090, 32,607 MiB, sm_120; Fakoli Dark gateway, Fakoli Mid Mod resource owner, Fakoli Mini Hermes client |
| Runtime | Anvil Serving `0.36.0`, executable fix-forward line through `5ea1edc`; ComfyUI v0.33.4 at `7a131a3a`; CUDA 13.0; PyTorch 2.13.0+cu130 |
| Managed recipe | `image.flux2-klein-4b-fp8-v1`; lifecycle-owned `serves.comfyui.toml`; worker absent when idle |
| Measurement path | real Hermes natural-language turn → exact eight-tool skill → authenticated MCP gateway → bounded controller approval → managed ComfyUI → authenticated artifact origin |
| Profiles | `draft` 512×512, `standard` 768×768, `high` 1024×1024; four steps each; prompt and optional seed are caller-controlled; c1 |
| Evidence | `functional`, routed/client acceptance, bounded `capacity`, bounded independent `quality`; production cutover and teardown |
| Decision | image workflow available at the three exact profiles; arbitrary graph/size/model input rejected; video remains `quality_failed` |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Independent image review | 6/8 strict pass | one warm image per profile plus five cold `draft` samples; six prompts; every artifact technically valid |
| Warm gateway E2E | 1.352 / 1.242 / 1.650 s | `draft` / `standard` / `high`; one natural Hermes request per profile |
| Warm generation phase | 0.101 / 0.128 / 0.189 s | gateway phase telemetry for the same three requests; not caller wall time |
| Warm Hermes caller wall | 38.354 / 24.420 / 41.962 s | complete agent turns, including model reasoning and tool use; one request per profile |
| Cold lifecycle E2E | 2,045.626 s | one `draft`; includes repeated fix-forward failures, approval/build/resume, queue, and generation; not steady-state latency |
| Cold retry caller wall | 47.788 s | identical Hermes request after approval/start completed; 363,074-byte PNG |
| Final hardened cold E2E | 908.936 s | `b46f6ce` plus skill `1.0.4`; server-issued exact resume bundle, 335.751 s approval wait, 898.152 s accepted-to-queued, 10.696 s queue, 0.087 s generation |
| Final post-approval Hermes wall | 43.091 s | exact `created=false` same-job resume, terminal polling, native image inspection, and English completion |

**Why it matters:** Hermes can now satisfy an ordinary image request without
learning ComfyUI graphs or infrastructure details. The user selects a small
quality vocabulary, while the gateway retains workflow identity, lifecycle
authority, authentication, idempotency, artifact ownership, and latency
telemetry.

**Important caveat:** Eight images over six prompts establish only a bounded
production smoke. Two strict draft reviews failed: an intended origami whale
read as a metallic fish/whale, and an intended glass lighthouse rendered as an
opaque tower with two rather than exactly three birds. The result does not
establish broad image quality, text rendering, hands, counting, material
fidelity, or monotonic visual improvement between tiers. The first cold E2E
number intentionally includes multiple defects found and repaired during the
same run and must not be presented as normal generation latency.

Evidence manifest:
[README.md](2026-08-28-hermes-image-quality-production-evidence/README.md) ·
Publication summary:
[publication-summary.md](2026-08-28-hermes-image-quality-production-evidence/publication-summary.md)

## Product contract

The production gateway exposes the same eight bounded media tools through MCP
and A2A. Hermes receives only those tools and an environment-reference-only
configuration authenticated with the router credential, never either
controller credential. Its media skill completes the generation turn, reports
an approval requirement with the server-issued complete resume bundle when the
worker is cold, and retries the exact workflow, version, quality profile,
parameters, and idempotency key after approval. It requires `created=false`
and the original job identity,
continues through nonterminal states, and selects reply language from the
current request rather than tool output or session history. It does not
diagnose infrastructure, submit a raw ComfyUI graph, choose a model filename,
or silently substitute another workflow.

The final deployed skill is `1.0.5`. It makes the mandatory fail-closed
cancellation for a missing or inexact server resume bundle an explicit
exception to the normal user-request-only cancellation rule. It also names the
five legal replay inputs separately from the job and approval correlation
fields, and preserves an explicit empty profile for workflows that declare no
quality profiles. These client and generic gateway hardenings do not relabel
the measured image requests below, which used skill `1.0.4` on the `b46f6ce`
runtime.

Image requests accept a prompt, an optional seed, and one of three profiles:

| Profile | Width × height | Steps | Intended use |
|---|---:|---:|---|
| `draft` | 512×512 | 4 | fastest bounded preview |
| `standard` | 768×768 | 4 | default balanced result |
| `high` | 1024×1024 | 4 | largest qualified output |

The profile, dimensions, graph, runtime, model assets, node set, and output
format are server-owned. A direct width override failed with
`quality_profile_parameter_override` before execution. A video request failed
with `quality_failed` and did not fall back to image or another generator.

Native MCP image results are bounded to 6 MiB and the framed bridge to 10 MiB.
The largest observed result was 1,495,385 bytes. Video artifacts are
resource-only even after a future workflow passes its independent quality
gate.

## Method

1. Rotate the production gateway credential, distribute only environment
   references, prove the current token returns 200, and prove retired and
   incorrect tokens return 401 before dispatch.
2. Deploy the image-enabled router and the bounded lifecycle/resource
   controllers without replacing or restarting the unrelated Qwen service.
3. Verify modern MCP discovery exposes exactly eight media tools and that the
   Hermes profile has the exact matching allowlist with fallback disabled.
4. Ask real Hermes for the same cinematic observatory scene once at each
   quality profile. Retain caller wall time, gateway phase telemetry, artifact
   size and digest, and an independent visual disposition.
5. Tear the worker down, submit a new `draft` request through Hermes, approve
   its declared lifecycle action, build/start the managed worker, and replay
   the exact request and idempotency key to completion.
6. Confirm teardown through the managed controller, then verify the worker is
   absent, the reservation has no owner or committed memory, and the resource
   controller has no orphan descendants.
7. Exercise negative controls for authentication, arbitrary dimensions, video
   quality state, duplicate approval, and idempotent replay.
8. Deploy the final executable revision, reconcile the Hermes skill, and run a
   second cold natural-language request through awaiting approval, exact
   same-job reattachment, terminal polling, native image return, teardown, and
   independent strict prompt-adherence review.

## Warm profile results

All three warm requests used the prompt “A cinematic glass observatory on a
mossy cliff, a copper telescope visible inside, blue hour, warm interior
lights.” The images were distinct, decodable PNGs at their exact declared
dimensions.

| Profile | Dimensions | Bytes | SHA-256 | Gateway E2E | Generation | Hermes wall | Review |
|---|---:|---:|---|---:|---:|---:|---|
| `draft` | 512×512 | 321,710 | `67867fe5...cd56` | 1.352 s | 0.101 s | 38.354 s | pass; complete scene, minor simplified geometry |
| `standard` | 768×768 | 766,691 | `6d88d55d...d063` | 1.242 s | 0.128 s | 24.420 s | pass; strong adherence, minor roof/railing perspective |
| `high` | 1024×1024 | 1,495,385 | `7c3874ca...2a7e` | 1.650 s | 0.189 s | 41.962 s | pass; detailed and coherent, mildly ambiguous glass extension |

These are single observations. The non-monotonic caller wall times reflect a
complete agent turn, not just GPU work, and do not rank the profiles. The very
short generation phases are warm/cached gateway observations and do not
replace the earlier cold direct qualification.

## Cold approval, build, resume, and teardown

The cold request asked for “A small copper observatory dome emerging from a
foggy pine forest at dawn” at `draft` with a fixed seed. It entered the durable
approval state without starting the worker. The approved managed start built
the pinned worker, passed its storage and `/system_stats` checks, and gave the
job lifecycle ownership. Hermes then replayed the same request and idempotency
key; the existing job advanced to completion instead of creating another
execution.

The 363,074-byte, 512×512 PNG had SHA-256
`ebb6f351c07c2109bea995ffb7dd03e48c40667b53d2b5e0aa8bc1b056c1b3ce`.
Independent review passed it: the copper-toned dome, layered pine forest, fog,
and dawn light were clear and coherent. The dome was slightly pink from the
sunrise and somewhat pavilion-like without an obvious telescope slit; distant
trees repeated, but no major rendering defect was present.

Recorded job phases were 0.061 seconds submission, 2,035.526 seconds from
acceptance to queued, 10.0 seconds queued, 0.1 seconds generation, and
2,045.626 seconds E2E. The approval-wait instrument recorded 151.78 seconds of
the final approval segment. The much larger accepted-to-queued interval spans
the complete fix-forward sequence below, so it is an incident/lifecycle
measurement rather than a normal cold-start service objective.

Managed teardown then removed the worker. The final resource view showed the
worker absent, zero media memory committed, 28,511 MiB free inside the media
envelope, no lifecycle owner, and no orphan build/start process.

## Final exact-revision regressions

Four later cold `draft` requests closed the immutable deployment and client
behavior gates. A 305,978-byte sample completed in 719.3 seconds E2E and passed
independent review. A 373,257-byte silver-whale sample completed in 695.344
seconds E2E but failed strict review because the subject read as a metallic
fish/whale rather than folded origami, despite the salt flat, sunrise, and tiny
blue balloon adhering. Its phase telemetry was 93.631 seconds approval wait,
684.225 seconds accepted-to-queued, 11.012 seconds queue, and 0.108 seconds
generation.

The final `bb798b0` regression used a complete resume bundle for a red glass
lighthouse request. Hermes returned `created=false` for the original job,
continued from queued to terminal in the same turn, inspected the native PNG,
replied in English, and reported every latency field. The 337,286-byte PNG had
SHA-256 `4eb0d995...207c`; recorded phases were 0.065 seconds submission,
169.714 seconds approval wait, 642.609 seconds accepted-to-queued, 9.888 seconds
queue, 0.110 seconds generation, and 652.607 seconds E2E. Strict visual review
failed because the tower was predominantly opaque rather than glass and only
two of the requested three white birds were visible. This is retained negative
quality evidence, not relabeled as a pass. Managed teardown again restored the
worker-absent, zero-commit, 28,511-MiB-free baseline.

The final measured `b46f6ce` regression hardened that boundary further. The
router assembled the complete seven-field resume bundle from validated current
request fields plus durable job, selected-profile, and approval identity;
skill `1.0.4` copied it literally instead of reconstructing it. After the
approved 475.983-second managed prepare, a fresh Hermes one-shot reattached
with `created=false`, polled queued to completed, inspected one native image,
and replied in English in 43.091 seconds. The 250,151-byte, 512×512 PNG had
SHA-256 `78c147bb...0076`; authenticated HTTP bytes, the MCP image block, and
artifact metadata matched exactly. Recorded phases were 0.062 seconds
submission, 335.751 seconds approval wait, 898.152 seconds
accepted-to-queued, 10.696 seconds queue, 0.087 seconds generation, and
908.936 seconds E2E. Independent review passed: one cobalt-blue ceramic
sphere was centered on a matte white pedestal against the requested plain
pale-gray studio background. Teardown took 12.563 seconds and restored the
worker-absent, zero-commit, 28,511-MiB-free baseline.

## Defects fixed forward during the production gate

The full production gate exposed twenty-three actionable defects: seventeen on
the live path, four in post-run gateway/skill/release review, and two in evidence
review. Each was repaired in the product or evidence surface with regression
coverage before the gate closed. Jobs whose replies could not preserve an
exact bundle were canceled; later fresh requests proved the corrected path:

1. The parent controller had a 30-second child timeout while the child allowed
   300 seconds. Child timeout plus a bounded margin is now propagated.
2. The controller image did not include Docker Buildx. The pinned plugin is
   now part of the managed controller image.
3. A resource-scoped `serves up` attempted to co-locate the router. Controller
   starts now use the explicit no-router path.
4. A failed approved start stranded the job without a fresh approval path.
   The same job/key may now mint a new one-use approval after failure, while an
   in-flight start cannot mint a competing approval.
5. Buildx tried to write its client state under a read-only home. The
   controller uses writable tmpfs-backed Docker configuration state.
6. A five-minute cold-build timeout was too short and killed only the direct
   child, leaving Compose/Buildx descendants. The bounded timeout is now 1,800
   seconds, and timeout handling terminates the whole process tree on POSIX and
   Windows.
7. A successful remote apply nested `applied=true`, but the lifecycle receipt
   reported false. Apply status is now normalized from the controller
   envelope.
8. Fleet status probed runtime-relative upstreams from the command host and
   falsely reported an outage. Live probes now execute from the installed
   router runtime, report a safe perspective/kind, and retain a typed mismatch
   diagnostic for configured-file evidence.
9. The managed Hermes bridge defaulted to the controller credential even
   though its endpoint is the router MCP gateway. The bridge now defaults to
   the least-authority router credential; real discovery returned exactly eight
   tools and a second reconcile had no drift.
10. The cold approval reply omitted a durable resume bundle, while a resumed
    one-shot could select stale session evidence or stop after saying it would
    poll. Skill `1.0.1` now requires the complete bundle, rejects any job or
    `created` mismatch, forbids session reconstruction, and keeps nonterminal
    states inside the tool loop.
11. The first `1.0.1` approval-boundary reply switched an English request to
    Chinese. Skill `1.0.2` now derives language only from the current request;
    the exact same-job completion reply was English.
12. One generic controller credential crossed both the router-to-lifecycle and
    lifecycle-to-resource edges. Dedicated, independently rotated credentials
    now protect each edge, and every cross-use attempt returns 401.
13. Multi-cycle approval telemetry selected the first completed approval
    interval even though the public contract describes the final completed
    interval. Latency now selects the latest completed cycle, with a two-cycle
    regression test.
14. The artifact tool catalog said inspection returned no bytes while eligible
    bounded images already returned native MCP image content. The description
    now states the image/video and size boundary exactly.
15. Skill `1.0.2` said a cold resume bundle was preserved but omitted the
    literal mapping. The affected job was canceled, and the contract now
    refuses approval unless every field is emitted.
16. Skill `1.0.3` emitted an incomplete mapping: it omitted the job identity
    and abbreviated the idempotency key. That job was also canceled rather
    than resumed from reconstructed state.
17. Revision `b46f6ce` makes the gateway assemble and return the exact
    `resumeBundle` from validated request fields plus durable job/profile/
    approval identity; skill `1.0.4` must copy that object literally and
    cancels the job if it cannot. A fresh cold live regression passed the
    complete same-job path.
18. Adversarial review found that skill `1.0.4` also said cancellation was
    allowed only on a user request, contradicting its mandatory fail-closed
    cancellation for a missing or inexact resume bundle. Skill `1.0.5` makes
    that safety action an explicit exception.
19. A valid profileless workflow could reserve a cold job and then lose its
    resume boundary because an empty profile was treated as invalid. Revision
    `5ea1edc` preserves the validated empty profile, accepts it in the MCP
    schema, and proves cold replay and cancellation through real operations.
20. Skill `1.0.4` said to replay the complete seven-field bundle even though
    `job_id` and `approval_transaction_id` are not submission inputs. Skill
    `1.0.5` names the exact five submission fields and reserves the other two
    for same-job and operator-approval correlation; the protocol regression
    proves full-bundle rejection and five-field replay.
21. The evidence incorrectly said every fix preceded completion of the same
    request even though two malformed-bundle jobs were deliberately canceled.
    It now distinguishes those canceled jobs from the fresh passing requests.
22. The evidence called the complete resume payload persisted even though raw
    request parameters are not stored. It now accurately separates validated
    current request fields from durable job/profile/approval identity.
23. The Windows CI checkout converted both Hermes skill copies to CRLF while
    the fail-closed packaged-skill loader validated an LF frontmatter prefix.
    The repository now pins both skill trees to LF in `.gitattributes`, and a
    hermetic regression proves LF stability plus packaged/example byte identity.

The final behavior is a fix-forward result, not a waiver of those failures.

## Safety and negative controls

| Control | Result |
|---|---|
| Router credential | authenticated MCP discovery passed; both media-controller credentials and the generic controller credential returned 401 |
| Lifecycle credential | authenticated health and prepare/teardown passed; resource and generic controller credentials returned 401 |
| Resource credential | authenticated health and managed serve operations passed; lifecycle and generic controller credentials returned 401 |
| Hermes tool scope | exactly eight media tools; environment references only |
| Caller width override | rejected before execution |
| Video request | `quality_failed`; no fallback or execution |
| Cold worker before approval | remained absent |
| Approval replay | one durable job and one owner; no competing start |
| Exact Hermes resume | server-issued complete bundle; `created=false`; same job; terminal polling in-turn |
| Hermes reply language | English request produced English terminal response under measured skill `1.0.4`; final skill `1.0.5` retains the rule |
| Final router/skill smoke | `5ea1edc` router healthy; skill `1.0.5`; exact seven-field cold reply; explicit Hermes cancellation reached `canceled` |
| Native artifact return | MCP image content, authenticated resource bytes, size, digest, and 512×512 PNG header matched exactly |
| Router fleet probe | installed router perspective; 9/9 configured routes returned HTTP 200 |
| Post-run teardown | worker absent; reservation reclaimed; no orphan descendants |
| Existing Qwen service | retained throughout the router/controller cutover |

## Release security boundary

The final public candidate passed the semantic scanner over 2,007 tracked
files with zero findings, its non-ignored untracked surface had zero findings,
and the repository-pinned Gitleaks image reported zero current-candidate
signatures. The private operator candidate also had zero Gitleaks signatures.
Its semantic scan still reports the real topology identities and operator paths
that the private repository exists to retain; those expected private-policy
matches did not cross into the public candidate.

A separate verified scan of all public commits reachable at the release gate is
deliberately reported as `findings remain`, not clean. It found 21 historical
signatures: 14 synthetic fixture or non-secret environment/provenance matches,
six derivative copies in
historical generated search indexes, and one credential-shaped `deviceToken`
field in a historical temporary-loopback OpenClaw smoke artifact. That field is
redacted in the current artifact, its historical value is absent from both
current repositories, and it is unrelated to the production router credential
rotated for this deployment. No Git-history rewrite or force-push was performed;
that destructive operation requires separate authorization and coordination.

## Evidence boundary

This finding establishes that the exact production image contract works from
real Hermes through MCP, the router gateway, lifecycle approval, an on-demand
ComfyUI worker, and authenticated artifact return. It also establishes exact
profile dimensions, telemetry fields, fail-closed overrides, idempotent cold
resume, and teardown on one RTX 5090.

It does not establish concurrency above one, broad or comparative image
quality, exact counting or material fidelity, monotonic improvement by profile,
arbitrary aspect ratios, a cold-start latency objective, or a usable video
generator. The two retained strict draft failures make those limitations
explicit. The Wan2.2 workflow remains `quality_failed` and unavailable until a
new immutable workflow version passes a separate temporal and perceptual
corpus. Six strict passes and two retained failures remain a small bounded
sample, not a general quality score.
