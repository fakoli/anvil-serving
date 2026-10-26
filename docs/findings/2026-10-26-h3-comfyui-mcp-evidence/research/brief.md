# Local podcast pipeline: evidence and next experiments

Research closed 2026-10-26. Source investigation, independent verification of 89 decisive claims, and a fresh adversarial review precede this parent-authored synthesis. This is a shortlist, not a claim that the candidates have run on this PC. The retained H3 trial measurements are local; all other model timings below are external evidence.

**Measurement boundary:** the 31,402/30,106 MiB peaks and 143.78/182.76-second
planning baseline below were supplied from earlier isolated private trials. Their
raw captures are not published in this bundle, so those prior numbers are not
independently auditable here. They are distinct from the later
[MCP measurements](../measurements.json), which retain their own raw phase rows.

## Answer

Keep the original recorded podcast as the master timeline. Build reusable host and studio assets, generate short shots, accept them before upscaling, and assemble them against that timeline. For the next neural experiment, **LeapTalk on native Windows is the strongest hardware-matched discovery for a single speaking host**. Compare it with **MuseTalk on an existing single-host source shot** when preserving performance and set pixels matters more than inventing gestures. Neither demonstrates three independently speaking people together in one generated room.

For guaranteed seating, cameras, prop geometry and legible studio signs, use **one authored Blender studio and deterministic compositing/editing**. Rhubarb can supply mouth cues to rigged characters; this trades generative photorealism for asset-authoring work. Use the existing **FLUX.2 Klein 4B** image path as the reference-keyframe baseline. FLUX 3 Image Klein is a newer reference/layout candidate for private hobby use under its full terms; do not assume its generated images are unrestricted training material for another similar model.

H3-specific reference, recorded-audio, control and acceleration recipes are technically promising, but are not the complete hosted H3 system or proven podcast workflows. Its standard license excludes the stated US execution setting and also contains an output/results territorial clause. An official Q&A about an Australian creator's worldwide uploads does not demonstrate separate permission for this US setting. Preserve this unresolved permission issue; do not publish generated H3 clips or claim unrestricted eligibility from the community checkpoint's label. [Pinned H3 agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE), [official discussion](https://huggingface.co/MiniMaxAI/MiniMax-H3/discussions).

## Ranked useful components

| Priority | Candidate and reusable recipe | Hardware evidence | What remains unproved |
| --- | --- | --- | --- |
| 1, speech experiment | **LeapTalk**, author-linked Comfy integration `041d7a7e`: one approved portrait + aligned speaker audio; native Windows, Python 3.12.13, torch 2.7.1/cu128, SDPA, no compile, guidance 1.0. Use its pinned Model_Pro/TAEHV recipe and component notice. | External Windows RTX 5090 run: 96.5 s output in 70.6 s queue-to-done; 8.1 GiB allocated, 11.1 GiB reserved CUDA, about 14.5 GB whole GPU. A short 10.4 s job took 30.3 s. | Single portrait, synthetic voices/portraits; no cast evaluation or human listening in that report. WSL2 explicitly unsupported by this integration. Long-shot timing does not predict many short cuts. |
| 2, preserve source shots | **MuseTalk 1.5**, original audio-file and reusable-avatar preparation recipe `0a89dec4`; use a single visible host/source clip. Retain the original body/camera/studio performance and edit its mouth region. | Publisher demonstrates V100 throughput and a much slower 4-GB Windows run. Its own U-Net file is 3.167 GiB; that excludes auxiliary models and workspaces. | Exact Blackwell stack and whole-process RAM/VRAM need qualification. Fine teeth, facial hair and jitter are documented limitations. Full dependency licensing still needs checking. |
| 3, shared studio/control | **Blender + Rhubarb Lip Sync NG + Kdenlive**: canonical studio, three character assets, explicit cameras/occlusion, per-host mouth cues, original-master edit. | Native local tools; Blender supports RTX 50-series. Scene memory depends on assets/render settings; no neural-weight-sized fit assumption is needed. | Character rigging, acting, style matching and manual correction cost. This creates an authored scene, not a demonstrated photoreal neural replacement. |
| 4, identity/keyframes | **FLUX.2 Klein 4B** first: official 4-step, guidance-1, CPU-offload reference recipe; compare held-out host views. Then **FLUX 3 Image Klein** distilled 16-step reference/layout inference if needed. | Klein4B publisher reports about 13 GB inference VRAM. FLUX3 creator reports RTX5090 rank-16/1-MP training 12.7 GB BF16 / 9.1 GB INT8 / 6.2 GB NF4, with experimental support. | Those training figures do not measure this cast or multi-reference inference. Image identity is not temporal identity. Klein4B is Apache; FLUX3 has additional terms, including cross-model improvement restrictions. |
| 5, expressive alternative | **InfiniteTalk V2V**, pinned original source `50aa0a94`, or official Comfy template: preserve a source performance, use native single/two-speaker audio/masks, sequential stages and bounded clips. | A current LightX2V 5090 config exists; its 300-s target is a configuration, not a measured success. | Exact 5090 peaks and long quality unmeasured here. Two speakers do not establish three. Full-frame generation can drift more than mouth editing. |

Sources: [LeapTalk benchmark](https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/BENCHMARKS.md), [setup](https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/SETUP.md), [component notice](https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/LICENSING.md), [MuseTalk](https://github.com/TMElyralab/MuseTalk/blob/0a89dec45a0192b824e3cf4daf96c239440c5ed8/README.md), [Blender GPU support](https://docs.blender.org/manual/en/latest/render/cycles/gpu_rendering.html), [Rhubarb NG](https://github.com/Premik/blender_rhubarb_lipsync_ng), [Kdenlive editing](https://docs.kdenlive.org/en/cutting_and_assembling/editing.html), [Klein4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B), [FLUX3 recipe](https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/FLUX3.md), [FLUX3 terms](https://huggingface.co/black-forest-labs/flux-3-image-klein-base/blob/main/LICENSE.md), [InfiniteTalk](https://github.com/MeiGen-AI/InfiniteTalk/tree/50aa0a94184315407a991ae804d9b58d6d311ba8). Accessed October 26, 2026; live branches are not immutable pins.

Do not load these alongside H3. At the supplied H3 generation peak only 1,205 MiB of whole-card memory remains; guided finishing leaves 2,501 MiB. Run stages sequentially and release their processes/models, rather than treating every fitting file as a fitting combined pipeline.

## H3 configurations, in order of information gained

1. **Native multiframe Ref2VA, then smaller text encoder.** Reuse the official multiframe template at `67984637`: 864×480,124 frames,24 fps,20 steps, Turbo off. Semantic host/set references go to `ref_images`; timeline anchors go to AddGuide as well when the encoder must understand them. AddGuide alone is not visible to Qwen. At 24 fps, 1.5 s is frame 36. Compare the present INT8 encoder with the published NVFP4+AWQ encoder as one controlled variable. Its stored size drops 25.277→14.610 GiB, a 10.667-GiB reduction; that is not a demonstrated equal reduction in sampler peak or equal reference fidelity. [Official workflow](https://github.com/Comfy-Org/workflow_templates/blob/67984637eefcd4825dcf5c134e39034785be1682/templates/video_minimax_h3_multiframe_reference.json), [Comfy components](https://huggingface.co/Comfy-Org/MiniMax-H3/tree/e5eb578a89295337b8ff433a035929ce0279e0b6).
2. **Recorded master + Hand-Tie motion_context.** Pin `073104ed`. Use `master_audio_file`, motion_context/auto, realism_boost off and audio_refine off. The source rejects master audio with experimental hand_tie pinning. Retained sound does not prove correct mouth attribution. Its publisher reports roughly 5–6 Turbo or 7–8 non-Turbo refined hops before texture changes; use explicit restart cuts between incompatible camera compositions. [Implementation and recipes](https://github.com/sandpies/ComfyUI-Hand-Tie-Clips/tree/073104edd381b7b56d2f2ae80bbe3d9578f764ed).
3. **Matched Ref8/PDD8 acceleration.** Use the adapter's exact FL2VA/Ref2VA family, shifts, step schedule and supported base. Keep the accepted low-denoise guided finish as control. The plox 37.1-s result is an experimental silveroxides FL2VA merge, not vanilla Ref4; its 67.4-s PDD8 row uses unpruned FL2VA INT8 and its own PDD head. Neither predicts this pruned hybrid's performance. [PDD adapters](https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs), [LightX inventory](https://huggingface.co/lightx2v/Minimax-h3-Turbo/tree/30f485ed874aa44c8183348ad361c5eabad318da), [original speed workbench](https://plox-1.github.io/h3-speedups/).
4. **ControlNet Union2 only if structural controls are still needed.** Its quantized branch adds 4.220 GiB of stored weights, and ten control blocks require the V2 post_norm configuration; V1 config can silently discard/misplace weights. DiT+branch stored sum≈23.750 GiB leaves activations, references, patch handling and workspaces unknown. It does not control audio. [Original V2 card](https://huggingface.co/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union-2.0/blob/7d2c95de2e351ed6a0b360af45f62daa04c26f88/README.md).
5. **Source-performance swap / staged Relay** after a single-host trial. A publisher's 58-s monolithic H3→LTX→RIFE graph used 110.32 GiB cgroup RAM plus 62.31 GiB swap: outside this 64/72-GiB container envelope. Later staged runs are useful design evidence, but the ten-shot demo totals only 9.48 s. Do not advertise it as ten full-length scenes or measured three-host continuity. [Pinned validation](https://github.com/akatz-ai/h3-relay/blob/e2a7665b6eb8ea37a88c0de2c9c6b69a8f7c9220/VALIDATION.md).

Character LoRA training is a later experiment, after held-out reference conditioning shows a persistent identity failure. Fizgig and Musubi support H3 adaptation; inference success does not qualify training. An exact Windows5090 AI-Toolkit report has a first-step Triton hang or ~40-GB BF16 staging/OOM, while another 96-GB host report exceeded 120 GB during caching. An image LoRA is not automatically compatible with a video family or a different H3 hybrid. [Fizgig guide](https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/MINIMAX_H3.md), [contrary hardware report](https://github.com/ostris/ai-toolkit/issues/1050).

## Quantitative planning

Local supplied baseline: 124/24 = **5.1667 s** output. Generation143.78s + finishing182.76s =326.54s, or **63.20 compute seconds/output second**. Finishing is **55.97%** of that total. A no-retry linear projection is 63.20 compute hours/output hour; this is a planning calculation, not a new benchmark or guaranteed episode throughput. Halving generation alone lowers total time only22.02%; select accepted takes before paying for finishing. Time per **accepted** second must include rejects, retries and overlap; that yield is currently unknown.

The tested 1728×960×124 RGB array occupies0.575GiB at uint8 or2.299GiB at float32, before copies/latents/workspaces. One minute at24fps occupies6.674GiB even as RGB uint8. Staging clips to disk and releasing heavy processes addresses a real scaling cost. The container's64GiB RAM and72GiB combined RAM+swap ceilings remain separate from Windows96GB installed RAM and WSL72GiB RAM allocation. [Docker resource semantics](https://docs.docker.com/engine/containers/resource_constraints/).

Preserve timestamps when mixing24-fps H3 and25-fps speech paths. Relabeling250frames from25to24fps makes10s become10.4167s (+4.17%); over an hour that adds150s and loses alignment. Duration-preserving conversion has its own visual tradeoffs and requires seam inspection. Keep the original audio master; the native video writers encode AAC again, so waveform passthrough is not compressed-bitstream identity.

## Benchmarks, sentiment and larger-project evidence

Two preference systems favor hosted H3 image conditioning: Artificial Analysis listed H3(768p) I2V+audio1140±16, nominal rank1 with rank interval1–6; its T2V+audio entry1158±15, rank4 interval2–8. Arena's displayed September21 list had I2V1495±5/rank1 and T2V1460±9/rank8. Different scales, tasks and overlapping intervals preclude a universal winner. Neither system measures this community quant, three hosts or recorded-master continuity. [AA I2V](https://artificialanalysis.ai/video/leaderboard/image-to-video), [AA T2V](https://artificialanalysis.ai/video/leaderboard/text-to-video), [Arena I2V](https://arena.ai/leaderboard/video/image-to-video), [Arena T2V](https://arena.ai/leaderboard/video/text-to-video), observed October26.

Practical evidence is more mixed than the leaderboards: current authors supply reusable node graphs and multi-shot relays, while source reports also document wrong-speaker mouth motion, texture/color drift, failed sparse refinements and memory exhaustion. These are specific failure reports, not a representative sentiment survey. H3's larger Relay showcase and Blender's conventional multi-shot production assets demonstrate project organization; they do not establish the same visual/task quality. LeapTalk's long single-person test is stronger exact-hardware evidence than a config advertising an untested30minutes. [Motion-context contrary result](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context/issues/57), [Blender production assets](https://studio.blender.org/films/sprite-fright/).

Speech-quality benchmarks add useful evidence, provided each stays within its own task and protocol:

| Original study | Matched result | Interpretation |
| --- | --- | --- |
| InfiniteTalk paper v1, August 2025; 40 HDTF source clips with changed speech | MuseTalk CSIM .933, LatentSync .916, InfiniteTalk .775; Sync-C 7.17/8.99/9.35 respectively | Source/mouth editors preserve identity pixels better in this study; stronger sync confidence does not make whole-frame regeneration more faithful. |
| MuseTalk paper v3, March 2025; 26 HDTF clips, no restoration | MuseTalk FID 6.52 / CSIM .86 / LSE-C 6.53; LatentSync 8.41 / .84 / 7.90 | Better image/identity scores coexist with lower lip-sync confidence. These are older tested releases and a different protocol from the preceding row. |
| LeapTalk paper v1, July 2026; sampled HDTF/CelebV-HQ, H200 batch 1 | Pro FID 21 / FVD 197 / Sync-C 8.38; Lite 38 / 285 / 8.14 | This supports a quality/speed trade within that paper. Its H200 fps is not RTX 5090 throughput or a three-person result. |
| AnyTalker v1, November 2025; 20 held-out two-person InteractiveEyes clips | 1.3B Sync-C 4.56 / FVD 467.84; 14B 6.99 / 424.15 | Paper quality for 14B cannot be assigned to the smaller, single-person-trained release. Listener eye-motion amplitude is not semantic interaction correctness. |

Sources: [InfiniteTalk](https://arxiv.org/html/2508.14033v1), [MuseTalk](https://arxiv.org/html/2410.10122v3), [LeapTalk](https://arxiv.org/html/2608.00079v1), [AnyTalker](https://arxiv.org/html/2511.23475v1). Do not pool these rows into one leaderboard; crop, dataset, checkpoint and edit/generate scope differ.

## Candidates deferred or ruled out at their documented settings

| Candidate | Decision and evidence boundary |
| --- | --- |
| AnyTalker14B | Its documented FP16 multi-person recipe needs≥80GB VRAM: policy/physical nonfit for this32GB card at that recipe. Smaller1.3B stage1 was trained on single-person data; three-person quality/peaks remain unqualified. |
| LongCat Avatar1.5 default tested recipe | External H200 run measured46,827MiB. Reject that exact configuration here; other offload paths remain unknown. |
| Lip Forcing released14B recipe | Publisher reports≈37GB with cached text/≈50GB with runtime T5; the smaller1.3B release was not checked off. |
| LatentSync1.6 | Technical comparison, not default shortlist: largest-face behavior, current5090 runtime reports, and research-only InsightFace supporting weights need resolution. |
| SoulX-LiveAct | Research watch: exact5090 fork exists, but3-s low-resolution and later GUI figures differ, source-code grant was missing, and the long-run resource claim is unqualified. |
| Qwen-Image2.1, TalkVerse | Their researched licenses specify noncommercial research/evaluation; personal podcast production is not automatically that category. |
| ContextGen default | Documented35–40GB inference exceeds the32GB card at that setting; useful research on identity/layout failures, not a fitting default. |

Original sources and exact claim status: [verified-claims.md](verified-claims.md), with source links for every row. These are documented-recipe exclusions, not proof that no future quant/offload implementation can work.

## Claims verified and remaining weaknesses

| Decisive claim | Status | Supporting passage/fact or remaining gap |
| --- | --- | --- |
| Native Windows LeapTalk has exact5090 measurements | VERIFIED external | Original benchmark's hardware, queue-to-done and whole-GPU fields; real cast/three-host quality not tested. |
| Existing H3 and finish fit this measured short recipe | VERIFIED supplied local | Retained isolated-run performance/memory evidence; this research did not rerun it. MCP qualification is separate. |
| Audio preservation proves correct speaker lip sync | WRONG | Separate mouth motion/attribution acceptance is required; current graphs do not prove it. |
| Three independent portraits prove one coherent three-host room | WRONG | Shared perspective, occlusion, gaze and listener behavior are untested. |
| Smaller files establish peak fit | WRONG | They omit activations, staging, workspaces and decoded clips. |
| Complete local three-host long-form production is ready | UNVERIFIED | No matched source or local cast evaluation found. |

The adversarial findings are incorporated: preserve the unresolved license question, separate source-audio fidelity from mouth attribution, do not pool tasks/variants, count failed attempts and short-job startup, maintain24/25-fps timing, and distinguish editorial decomposition from the original simultaneous-room ambition. Transitive rights, actual character likeness, crosstalk, production-length drift and final user quality remain open. Raw research-event exports were unavailable; provenance is an explicit reconstructed ledger, not fabricated raw telemetry.

## Smallest next qualification

Use one held-out excerpt containing all three hosts, a silent listener, a turn transition, crosstalk and a return to an earlier camera. Preserve the master and isolated aligned speaker stems where they exist; diarization does not separate overlapping voices. Compare the same approved singles/source shots, then an authored wide. Evaluate likeness, correct mouth/speaker, studio geometry/text, occlusion and first/middle/last/join frames while listening. Record startup, processing, finishing, whole-GPU/RAM/commit peaks, every failure and accepted footage yield. No new model was installed for this research.

Supporting source reports: [H3 angle](angle-h3.md), [speech angle](angle-speech.md), [identity/studio angle](angle-identity.md), [89-claim verification](verified-claims.md), [adversarial findings](review.md), [provenance](brief.provenance.md).
