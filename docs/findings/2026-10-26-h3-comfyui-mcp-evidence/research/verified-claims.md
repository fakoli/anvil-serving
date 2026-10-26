# Stage 2 — verified claims for the local three-host podcast investigation

Access/verification date: 2026-10-26 UTC. Verifier: /root/podcast_research_verify, fresh context. Read angle-h3.md, angle-speech.md, angle-identity.md and their provenance. Source-only: no weights, inference, installation, training, media generation or service changes. Exact tool/URL ledger: verify.provenance.md.

**VERIFIED means the original cited source supports the bounded claim, not that I reproduced its experiment or qualified this machine. WRONG identifies a source/variant mismatch or a materially incomplete reading. UNVERIFIED identifies the missing evidence.** Source assertions, implementation facts, supplied measurements and calculations remain distinct.

Many WRONG rows test a tempting inference that stage 1 already cautions against. They are not a count of mistakes made by the researchers. Actual corrections/additions to carry forward are listed next; the rest preserves the boundary between the source evidence and an overclaim.

## Corrections that must reach the synthesis

1. The H3 agreement restricts Outputs/results outside the Applicable Territory as well as model use. Its unchanged V.4 cannot be summarized as a model-download-only restriction. An official August 6 Q&A reply permits an Australian creator's global YouTube distribution; that creates a text/guidance tension, not a demonstrated exemption for US local execution. No personal/noncommercial exemption for US execution was found.
2. The 37.1-second plox "LightX4" result belongs to a silveroxides experimental DARE-TIES FL2VA Turbo 4–8-step merge at strength 0.90 with an AdaLN port fix. The 67.4-second PDD8 row uses an unpruned FL2VA INT8 base and its own PDD node/head. Neither number benchmarks the supplied pruned Cobijada Ref2VA recipe or a vanilla official Ref4 release.
3. LeapTalk's exact Windows RTX 5090 source is unusually useful, but it is a one-portrait, synthetic-input test. Its 96.5-second/70.6-second result includes process startup; it is not a promise for a full episode, three hosts, arbitrary recorded voices, cuts or WSL. The integration explicitly excludes WSL2 in that release.
4. LatentSync's usual InsightFace supporting weights are research-only; ordinary personal production permission for that complete default bundle is not established. TalkVerse's original LICENSE.txt also explicitly requires noncommercial research, a narrower condition than merely being unpaid. LiveAct has a verified source-code-license gap.
5. No fetched evidence qualifies the complete three-host, recorded-master, consistent-studio, repeated-cut podcast. A functioning ten-shot graph, an image identity score, an audio passthrough, a short single-face run and three independent concurrent portraits are different evidence.

## Territorial and component licenses

| ID | Claim | Status, source passage and consequence |
|---|---|---|
| L01 | H3 standard grant excludes the US, including personal use | **VERIFIED.** [H3 license, August 2, 2026][h3license], definitions I.3/I.5 and II: US is excluded; grant is “Solely within the Applicable Territory.” The text supplies no unpaid/hobby exception. With US use and no separate permission supplied, H3 generation/training is not an executable recommendation. |
| L02 | H3 output ownership removes geographic restrictions | **WRONG.** [Same agreement][h3license] excludes outputs from Model Derivatives and disclaims ownership, but V.4 separately includes “Outputs or results outside the Applicable Territory.” Ownership language is not a territorial permission. |
| L03 | H3 Q&A clearly resolves worldwide output distribution for every user | **UNVERIFIED.** [Original discussion #12 and its full API events][h3qa] confirm an August 6 MiniMax-org reply to an Australia/global-YouTube question: “Yep, you don't need apply.” August 13 also gives global-distribution/attribution guidance to an excluded-territory applicant. The agreement remains unchanged. These replies do not demonstrate authorization for this user's US local execution or settle all V.4 applications. |
| L04 | Cobijada Apache metadata replaces H3 terms | **WRONG.** [Pinned derivative card][cobijada] explicitly retains underlying MiniMax terms. Its body resolves the misleading Apache metadata. It selects REF AdaLN blocks 25–49 into an FL-based network; this is not the full hosted H3 system. |
| L05 | FLUX 3 permits this private hobby use | **VERIFIED, subject to the whole license.** [Original Kommunity v1.0][fluxlicense] expressly includes “private entertainment, hobby projects”; grant is worldwide. Section 4.b restricts using outputs/model to improve a different AI model with similar function. Do not call it Apache or imply a blanket commercial training/distribution right. Public/end-user and production uses have additional language; the source establishes the stated private hobby case, not every later distribution plan. |
| L06 | FLUX.2 Klein 4B is Apache-2.0 | **VERIFIED.** [Official 4B card][klein4] declares Apache-2.0. Its permissive license and ~13 GB advertised inference minimum make it a sensible comparison baseline; neither claim establishes image quality for these hosts or a training peak. |
| L07 | Qwen-Image 2.1 noncommercial covers any unpaid creative work | **WRONG.** [Original September 20 license][qwenlicense] defines Non-Commercial as “for research or evaluation purposes only.” Ordinary hobby podcast production is not established by that definition. Do not reuse predecessor Apache terms. |
| L08 | LatentSync code and 1.6 weights are both Apache | **WRONG.** [Code LICENSE][latcode] is Apache-2.0; [official 1.6 weight card][latweights] says openrail++. Its source face detector imports InsightFace; [original InsightFace terms][insight] restrict the provided pretrained models to “non-commercial research purposes only.” Default-bundle production rights remain unresolved. |
| L09 | MuseTalk own code/weights permit personal production | **VERIFIED for its own components.** [Original README][muse] says MIT code and trained model available for any purpose, including commercial; it separately preserves component and test-data conditions. This does not clear every detector/parser asset automatically. |
| L10 | LeapTalk's measured recipe uses an Apache Pro base, not restricted FlashHead Lite/LTX | **VERIFIED at source level.** [Wrapper component notice][leaplicense], [original LeapTalk LICENSE][leapcode], [pinned LeapTalk weight card][leapweights] and original FlashHead card at measured revision confirm Apache code/LoRA/audio projection/Pro/Wan plus TAEHV MIT. The “Lite” decoder in this integration is TAEHV, not FlashHead Model_Lite/LTX. Current FlashHead component correction should remain attached to this distinction; no runtime byte comparison was repeated. |
| L11 | InfiniteTalk/AnyTalker code/own weights have Apache terms | **VERIFIED for the named primary artifacts.** Original repositories' LICENSE.txt files are Apache-2.0; current official HF cards identify Apache weights. AnyTalker still invokes InsightFace; its detector-weight restriction is separate. No broad transitive-license certification was performed. |
| L12 | LiveAct model metadata licenses its source tree | **WRONG.** [Fork NOTICE][livenotice] records the missing source license. Fresh original GitHub license API returns 404 and the exact upstream commit's untruncated 80-entry tree has no LICENSE/NOTICE match. Apache model metadata is not evidence of a source-code grant. |
| L13 | TalkVerse merely requires no monetization | **WRONG as a permission inference.** [Original LICENSE.txt][talklicense] limits sample code to noncommercial research and defines research as academic study/instruction or noncommercial research/testing/validation. The [README][talk] applies the Snap license to code/data/weights. General personal creative production is not established. |
| L14 | Blender/Rhubarb can support private local production | **VERIFIED.** [Blender license][bllicense] permits artwork use, including commercial. [Rhubarb original license][rhlicense] is MIT and says generated lip-sync data belongs to the user; NG integration's original LICENSE is MIT. Asset/media rights and the user's actual mouth rig were not assessed. |

## Exact speech paths, runtime and memory

| ID | Claim | Status, source passage and limit |
|---|---|---|
| S01 | LeapTalk has a reproducible external Windows RTX 5090 recipe | **VERIFIED.** [Pinned setup][leapsetup] identifies Windows 11 native, driver 595.95, Python 3.12.13, torch 2.7.1+cu128, SDPA; no FlashAttention/Sage/compile needed in measured configuration. It says WSL2 runtimes are unsupported and Linux real-weight runs were untested. This is external recipe evidence, not this machine's installation. |
| S02 | LeapTalk produced 96.5 s in 70.6 s | **VERIFIED, narrowly.** [October 3 benchmark][leapbench] table gives 2,414 frames, 87 chunks, 70.6 s queue-to-done for 96.5 s input, one-shot process/loading per job. Two fictional portraits and synthetic English voices were the test inputs. Model-load substage uses warm file cache; do not relabel all one-shot numbers as a wholly cold filesystem run. |
| S03 | LeapTalk needs only 8.1 GiB of whole-card VRAM | **WRONG.** [Same benchmark][leapbench] distinguishes 8.1 GiB allocated, 11.1 GiB reserved CUDA, ~14.5 GB whole GPU including desktop, ~5.1 GiB direct-run process-tree RSS and Windows commit effects. Use the appropriate measurement, not the smallest number. |
| S04 | LeapTalk duration-independent memory and fast warm jobs prove episode readiness | **UNVERIFIED.** The source supports bounded chunk-loop memory for tested inputs through 96.5 s, not a full episode. Warm timing tables include only completed jobs; memory-guard refusals/stops and high background commit are separately disclosed. No viewer streaming is implemented; result arrives at job end. |
| S05 | LeapTalk exact original audio and human-validated lip sync | **WRONG if “exact” means archival samples or perceptual qualification.** [Benchmark][leapbench] says audio is muxed as AAC; raw frame parity for selected fixtures concerns pre-encoding pixels, not MP4/audio identity. Author reviewed frames/proxies; nobody listened. Small boundary steps and guidance-2 artifacts were reported. Keep the untouched master separately. |
| S06 | LeapTalk is a one-person portrait path | **VERIFIED.** [Setup/recipe][leapsetup] and [original paper][leappaper] support 512-class single talking head/reference-anchored chunks. No native three-host studio, arbitrary camera/gesture-control or cut-continuity recipe is demonstrated. Proposing one speaker per job is an engineering decomposition. |
| S07 | LatentSync 1.6 minimum is 18 GB and 512 training | **VERIFIED publisher specification.** [Original README][lat] lists 18 GB inference for 1.6 versus 8 GB for 1.5 and June 11, 2025 512×512 release. This is not an exact 5090 runtime qualification or a total host-RAM bound. |
| S08 | LatentSync default supports three separately bound faces | **WRONG.** [Original detector][latface] selects the largest eligible face (area comparison). Multi-host crop/track/mask/paste-back is proposed extra pipeline work. Original pipeline also accumulates decoded/results for a clip; chunked denoising alone does not prove constant end-to-end RAM. |
| S09 | LatentSync/ MuseTalk 32 GB capacity proves Blackwell compatibility | **UNVERIFIED.** Their sizes/minima cannot qualify custom kernels/dependencies. [LatentSync issue 355][lat5090] is contrary firsthand 5090 trouble, not proof all current recipes fail. MuseTalk's 3050 Ti/4 GB run and V100 speed in [README][muse] are different hardware/configurations. |
| S10 | MuseTalk is a fast source-face edit baseline with known detail limits | **VERIFIED as publisher evidence.** [README][muse] documents 256×256 face regions, reusable avatar preparation/audio inference, 30+ fps V100 and ~5 min/8 s on a Windows 3050 Ti 4 GB. Mustache/lip detail and jitter limitations are explicit. No exact 5090 timing was verified. |
| S11 | Native Comfy InfiniteTalk independently binds three audio streams | **WRONG.** [Original nodes_wan.py][infnative] schema provides single_speaker/two_speakers and two masks/audio outputs. Native two-person support is real; it is not a three-person interface. |
| S12 | InfiniteTalk is a useful V2V gesture/dubbing path with long still drift | **VERIFIED publisher/implementation scope.** [Original README][inf] supports V2V/I2V and warns still-driven colors shift more after a minute. [Paper][infpaper] uses 81-frame chunks with nine prior frames. Source camera fidelity is approximate; controlled V2V is not pixel preservation. |
| S13 | LightX2V has already demonstrated 300 s on a 5090 | **WRONG.** [Original configuration][inf5090] contains 300 s, 720p, four steps, multi/para audio, FP8-SGL and block offload. A target configuration file is not a completed measured 300-second run. Exact Comfy three-host RAM/VRAM remains unknown. |
| S14 | AnyTalker has a genuine third-person audio binding | **VERIFIED interface capability.** [Original README][any] maps audio left to right including middle/third person and pad mode. This proves interface availability, not a qualified 32 GB three-host render. |
| S15 | Released AnyTalker 1.3B delivers paper 14B multi-person quality | **WRONG.** [Original README][any] explicitly marks released 1.3B as stage 1, exclusively single-person trained, and 14B multi-person as a later checkpoint. 14B documented inference needs at least 80 GB VRAM with half precision. No pinned quant/offload 32 GB exception was verified. |
| S16 | AnyTalker 1.3B exact three-host resource fit is established | **UNVERIFIED.** HF metadata confirms 7,602,583,002-byte 1.3B and 83,952,080,773-byte 14B files; these are serialized sizes, not working sets. Source torch2.6/cu126/FlashAttention recipe is not Blackwell qualification. Full clip audio drives frame count; streaming/constant RAM is not demonstrated. |
| S17 | LiveAct has official one-5090 throughput and a smaller quant experiment | **VERIFIED at stated scope.** [Original README][live] reports 6 generated fps with offload/FP8 KV. [Fork benchmark][livebench] is one 3-second, 256×416, 24-fps WSL2 input, CPU T5, torch2.8/cu128; 20.72 GiB allocated and warm total 774.50 s, not real-time. 1.333 s/5.07 s is only its last warm block. |
| S18 | LiveAct later near-1 RTF profile has the same measured peak and long test | **UNVERIFIED.** Later [guide][liveguide] changes to 3 steps/15 fps and reports first deliverable ~2.11 s after ~60 s warmup and two later chunk RTFs. It recommends ≥64 GB RAM/80 GiB disk. That does not transfer the earlier 20.72 GiB peak or establish a second clean-machine/episode reproduction. |
| S19 | LongCat Avatar1.5 default tested path fits 32 GB | **WRONG.** [Independent deployment report][tight] measured 46,827 MiB on H200, eight-step INT8, one 3.63 s sample. [Original release][longcat] supports continuation, Whisper-Large and multistream modes; different offload recipes may change fit, but no exact 5090 qualification was verified. |
| S20 | EchoMimicV3-Flash's 12G minimum is disproved by a 33,726 MiB run | **WRONG.** [Author README][echo] states a 12G path; [independent report][tight] tested a different default 768-area path. Neither settles exact 5090 low-memory/offload quality. This remains a comparison lead, not a proved nonfit or proved fit. |
| S21 | Lip Forcing's small student is released and fits 32 GB | **WRONG.** [Original release/checklist][lip] has 14B released, 1.3B unchecked. H200 streaming claim is ~37 GB with cached text, ~50 GB with runtime T5, and up to ~0.5 s output truncation. Current released recipe exceeds this card's documented budget. |
| S22 | FlashHead three concurrent streams solves one coherent three-host scene | **WRONG.** Original card describes independent portrait concurrency; its Pro 25+ fps figure uses two 5090s. Neither proves common gaze, occlusion, studio geometry or independent voice binding in a shared shot. |
| S23 | StableAvatar ~3 GB and TaoMate-H3 qualify this requirement | **UNVERIFIED.** Original StableAvatar source was fetched but this verifier did not independently recover the exact advertised 3-GB setting/runtime artifact; TaoMate-H3's multi-Hopper lead was not independently rechecked. Neither has verified 5090 three-host recorded-master proof here. Do not rank them on such assumed proof. |

## H3 local/hosted identity and proposed recipes

All technical candidates below retain L01–L04. These are capability facts, not instructions to execute H3 for the stated US/no-permission project.

| ID | Claim | Status, source passage and limit |
|---|---|---|
| H01 | Local Base is the complete hosted H3/Context-IR/2K system | **WRONG.** [Pinned official card][h3card] states Base 768p; Context-IR is hosted and absent from open release; official full Regenerate-2K uses APIs. Cobijada INT8 plus a community compiler/upscaler has no Arena equivalence evidence. |
| H02 | Ref2VA accepts up to 9 images/3 videos/3 audios | **VERIFIED specification.** [Official card][h3card]: ≤12 mixed files; each A/V 2–15 s, aggregate video ≤15 s and aggregate audio ≤15 s. Output envelope 4–15 s/24 fps/32 kHz stereo. This is not a long episode guarantee. |
| H03 | Native timeline guides and semantic refs are different wires | **VERIFIED.** [Original multiframe docs][multi] says AddGuide-only pictures are “not visible to the text encoder.” [Pinned template][multijson] has no zero-frame guide, uses explicit later frame indices, and documents round(seconds×24). The proposed four-reference host/studio mapping has not been tested. |
| H04 | Pinned template is a real pruned INT8/NVFP4-TE workflow | **VERIFIED source artifact.** [Template][multijson] and HF metadata supply the native reference workflow. Default 0.4 MP/20-step path and optional Turbo are an available recipe, not an observed three-host workload. |
| H05 | Smaller encoder saves ~10.667 GiB of stored weights | **VERIFIED arithmetic/metadata.** [Comfy HF blobs][comfyapi] list INT8 TE 27,141,342,152 bytes versus NVFP4+AWQ 15,687,142,551; difference 10.66755 GiB. Generator/storage resident peaks do not decrease automatically by that amount. |
| H06 | W6A8 is faster and quality-equivalent to INT8 | **WRONG.** [Original PR191][w6] layer test gives INT8 .477 ms/.0150 output relL2 versus W6A8-g32 .627 ms/.0256. Smaller file is verified; end-to-end speed, identity/speech/video quality equivalence is unverified. Group-specific files must not be interchanged blindly. |
| H07 | Hand-Tie supports arbitrary combinations with recorded master | **WRONG.** [Pinned code][handcode] rejects hand_tie+master_audio_file and rejects realism_boost or audio_refine with master audio. motion_context/auto, boost off, audio_refine off is the supported source path. The angle's #L4887 anchor is not the actual current guard line in freshly fetched text; use function/guard identity. |
| H08 | Master-audio lock proves lip synchronization | **UNVERIFIED.** Code fixes audio latent/recording and denoises video, but no retrieved controlled recorded-three-host lip-sync test establishes correct mouths/attribution. Preserved soundtrack alone is insufficient. |
| H09 | Hand-Tie continuity and speaker failures are bounded publisher observations | **VERIFIED.** [Changelog][handlog] reports about 5–6 Turbo or 7–8 non-Turbo refined clips before texture changes. [Prompting tests][handprompt] report two mouths speaking on two seeds; a closed-mouth listener action fixed those seeds. Rapid turns remain hard. Ten-hop experimental previs is a different path, not master-audio proof. |
| H10 | PDD and newer Ref8 artifacts exist | **VERIFIED.** [Alibaba card][pdd] gives separate rank/alpha-64 BF16 FL2VA and Ref2VA adapters. [LightX API][lightapi] confirms Ref4 v1.0 and Ref8 v1.0/v1.1 768p files at the reported revision. [Older table][turbo] distinguishes shifts across variants; later filenames do not establish their training recipes. |
| H11 | Windows 5090 staged PDD/upscale/refine has firsthand evidence | **VERIFIED as creator notes.** [Pinned notes][lab] include 130.6 + 35.7 + 10.1 + 140.2 + 36.8 = 353.4 seconds for an 8-second example, a denoise ~0.75 Ref4 finish and later ~330-second clip. Sparse refine was rejected on appearance; PDD8/0.25 took ~490 seconds without winning. Anime-oriented inputs, 128 GB host and different components prevent a podcast comparison. |
| H12 | Union2 adds stronger geometry control and a mandatory new config | **VERIFIED.** [Original V2 card][union] lists eight controls and ten branch blocks versus five; control_apply_audio=false. It explicitly warns old v1 config silently drops/misplaces weights; use post_norm V2 config. Structural conditioning is not voice/identity evidence. |
| H13 | Union2 branch fit is demonstrated by adding files | **WRONG.** Original branch13,551,637,688 bytes=12.621 GiB; quantized Comfy branch4,531,220,608=4.220 GiB. DiT+quantized branch stored sum≈23.750 GiB is a lower bound, not activations/workspaces/patch residency. No matched 5090 three-host peak exists. |
| H14 | Character SwapV1.5 exact source-performance recipe exists | **VERIFIED.** [Pinned original card][swap] specifies Ref2VA INT8, V1.5 strength1, separate FL2V Turbo8v1.0-768p strength1, eight steps/CFG1/er_sde/normal. This unusual cross-variant pairing is publisher-documented, not an accidental recipe merge. Four selected comparisons do not establish aggregate quality or lip-sync preservation. |
| H15 | Relay demonstrates larger projects fitting supplied RAM limits | **WRONG as a general fit claim.** [Validation][relay] reports the four-shot 58-second monolithic graph at 1,648.97 seconds, 23,037 MiB GPU, 98.54 GiB RSS, 110.32 GiB cgroup and 62.31 GiB swap. It exceeds supplied container allowances. Cached staged finishing reached 22.25 GiB RAM; the fresh ten-shot functional graph totals only 9.48 seconds and uses one H3 step. No three-host production qualification. |
| H16 | Guided upsampler repairs semantic errors | **UNVERIFIED.** [Original README][up] documents source-guided two-step denoise 0.1, 192-pixel overlap, a plain DiT under the guide and restoration of original audio. Its visual comparison is informal. From-noise use flickers every 17 frames. Preserving wrong source geometry is a mechanism-based risk, not a measured repair statistic. |
| H17 | H3 LoRA training is unavailable | **WRONG.** [Fizgig guide][h3train] and pinned source support pruned INT8/Ref2VA, block selection, short video/audio data and assistants. Reference-only first is an efficiency judgment. Training on the exact Cobijada revision remains unqualified. |
| H18 | Circlestone assistant can be mixed with guidance preservation | **WRONG for this assistant.** [Original card][assistant] says fuse during training, omit at inference and do not use CFG-augmented/guidance-preservation loss. Limited mixed-video testing is not a universal assistant or LoRA-transfer proof. |
| H19 | RefMods are trained weight LoRAs with guaranteed identity/voice | **WRONG.** [Original README][refmod] describes reference latents and failed speaker-identity transfer. [Fizgig guide][refmodfiz] optimizes reference data with the model frozen. A tiny saved file does not imply negligible attention-token cost or runtime memory. |
| H20 | Closed MotionContext issues establish fixed drift or regression | **WRONG as a blanket conclusion.** [Issue 57][mc57] records a rejected five-clip latent experiment that worsened drift; the user's decoded-frame method retained about 30% flicker. [Issue 48][mc48] says clean installation resolved an unexplained problem, not a confirmed general Comfy 0.37 regression. |

## Image/identity, training state and conventional production

| ID | Claim | Status, source passage and limit |
|---|---|---|
| I01 | FLUX3 is a released5.6B/up-to10-reference image candidate | **VERIFIED publisher specification.** [October15 release][fluxrelease] and [official distilled card][fluxcard] separate Base training and16-step/no-CFG inference, Qwen3-VL4B encoder,full scene captions and soft bounding boxes. Boxes do not hard-mask pixels; text/outside-edit content may fail. This is not cast accuracy proof. |
| I02 | FLUX3 generates any ten-reference scene on5090 in4 s | **WRONG.** [NVIDIA measurement as reported by BFL][fluxrelease] is INT8,distilled16-step,1024² text-to-image in ComfyUI. It does not report a ten-reference time/peak. BFL quality Elo gains are internal comparisons; no cross-board scale transfer. |
| I03 | FLUX3 reported memory numbers agree across runtimes | **WRONG.** [BFL card][fluxcard] reports31/26/25GB B200 reference-code BF16/FP8/NVFP4,upsampler off. [Diffusers report][fluxhf] distinguishes H10020.72GiB allocated table,23.8GiB end-to-end timing table,RTX6000 NVFP4 15.3GiB. Different implementation/task/hardware; no exact four-reference5090 peak established. |
| I04 | Fizgig FLUX3 training has exact5090 external memory evidence | **VERIFIED publisher report, not reproduced.** [Pinned guide][fluxtrain] says rank16/1MP measured12.7GB BF16,9.1GB INT8,6.2GB NF4 and calls support/presets experimental. Missing measurement API/environment/confidence and multiple-reference costs remain material. |
| I05 | Rank-16 FLUX3 bookkeeping estimate | **VERIFIED calculation under stated assumptions.** Pinned model/driver show hidden width 3,072, MLP ratio 3, three streams × five blocks plus 28 joint blocks, and six linear targets. 43 × 16 × 58,368 = 40,157,184 adapter parameters; 14 bytes/parameter = 0.52359 GiB. FP32 weights/gradients/EMA plus 8-bit optimizer is a storage scenario, not peak VRAM. Pure T2I may leave reference-module gradients/state unallocated. |
| I06 | H3 rank-8 identity-window arithmetic | **VERIFIED calculation under stated assumptions.** Source widths 5,376/7,168/14,336 and qkv/out/fc1/fc2 targets give 745,472 parameters/block; 30 blocks = 22,364,160; 50 blocks = 37,273,600. AdamW+EMA at 20 bytes/parameter is 0.4166–0.6943 GiB. Automagic3 uses history 8, packed bits and factored states; fused backward and actual gradient/EMA allocation change live residency. The ~0.28–0.47 GiB figure is bookkeeping, not measured training memory. |
| I07 | Ordinary 33B full-training scale is a LoRA peak | **WRONG.** 33 billion × 2 / 2³⁰ = 61.467 GiB and ×18 / 2³⁰ = 553.206 GiB are arithmetically correct nominal scenarios. They are neither pruned LoRA nor rotating-window training. Adapter states, activations and host staging remain separate costs. |
| I08 | Fizgig H3 identity window, video grid and RAM recipes exist | **VERIFIED publisher/source recipe.** [Guide][h3train] gives blocks 20–49, 24 fps, 5+17n frame lengths, dimensions divisible by 32 and 32 kHz stereo. The 32 GB tier uses 0.5 MP shorter clips or 0.25 MP longest clips. [Install guide][fizinstall] suggests ~32 GB system RAM for caching/training and ~48 GB with previews. These assume its selected components and do not qualify this custom hybrid/container. |
| I09 | Successful inference proves training works on 5090 | **WRONG.** [Issue 1050][train5090] documents Windows 5090 first-step hangs with Triton and ~40 GB BF16 staging without it, despite working inference. Different trainer/version: it does not disprove current Fizgig. Custom-hybrid training activations, workspaces and host peaks remain unknown. |
| I10 | Creator likeness/EMA scores prove general identity lock | **WRONG.** [v5.2][fiz52] and [v5.4.1][fiz541] describe creator dataset/seed face-recognition ablations. The 56.5→61.5 score and 3.9→1.6 spread support that comparison, not population accuracy, held-out temporal identity, lip sync or three-host correctness. |
| I11 | ContextGen gives controlled multi-instance failure evidence | **VERIFIED, model-specific.** [Paper][contextpaper] studies layout, attribute leakage and overlap; overall 64.66 versus 63.71/64.11 belongs to its heterogeneous-input study. It is not a FLUX3/H3 score. [Repository][context] specifies 35–40 GB default inference, exceeding this GPU. Its training costs/quality cannot be transferred to FLUX3. |
| I12 | Blender supports exact studio geometry, text and cuts | **VERIFIED features; result unbuilt.** [Cycles 4.4][cycles] adds Blackwell support; current manuals document text, camera-marker binding, Alpha Over and OptiX. Fixed assets preserve authored counts/signs/cameras by construction. Scene cost, rig/style matching, asset preparation and accepted renders are unmeasured. |
| I13 | Rhubarb and NG preserve source voices and integrate with Blender | **VERIFIED features.** [Core README][rh] analyzes recordings and outputs cues; [NG README][rhng] documents armatures, shape keys and NLA. Windows Blender 5.2 matrix: 67 passed, zero failures/errors, four skipped. These are software tests, not perceptual speech scores. It does not generate emotion or separate overlapping speakers. |
| I14 | Kdenlive can cut video around the recorded mix | **VERIFIED documented behavior.** [26.08 manual][kden] describes multicam video cuts without changing audio tracks and common-audio/timecode alignment. This preserves the editorial timebase if used correctly; it does not correct generated lip motion. |
| I15 | Sprite Fright supplies relevant larger-project evidence | **VERIFIED at appropriate scope.** [Production files, November 24, 2021][sprite] describe linked character/prop/set assets and shot lighting; [edit bundle, November 25][spriteedit] states 49 GB, 135 edit versions and OTIO. Conventional production structure is demonstrated; automated host recreation and this 5090's render speed are not. |
| I16 | NightTale proves 87 completed character LoRAs | **WRONG.** [Pipeline README][night] claims an 87-character/14-game roster but labels Krea2 training untested/TBD and full-roster times estimates. It supplies a template/worklog, not proof of 87 finished LoRAs or long-form video. |
| I17 | Character-sheet workflows establish real unseen host views | **UNVERIFIED.** Muse CharacterSheet, Qwen2.1 and sheet adapters were stage-1 discoveries; this verifier did not independently inspect every branch/adapter file. Generated turnaround plausibility does not establish recovery of real unseen anatomy. These cannot justify ranking above actual source references. Qwen2.1's independently verified research-only license remains decisive. |

## Benchmarks and resource accounting

| ID | Claim | Status, source passage and limit |
|---|---|---|
| B01 | AA rates H3 highly in audio-inclusive video | **VERIFIED snapshot.** [T2V v2.1][aat]: H3 (768p), 1158±15, 6,484 samples, rank 4/range 2–8. [I2V v1.1][aai]: 1140±16, 4,209 samples, rank 1/range 1–6. Entry-level human preferences do not score Cobijada/quant/Ref2VA/three hosts. Overlapping intervals do not establish a clear gap; overlap alone is not a formal pairwise significance test. |
| B02 | Arena scores qualify the local quant | **WRONG inference.** [T2V][arenat]: 1460±9, 11,417 votes, rank 8; [I2V][arenai]: 1495±5, 57,112 votes, rank 1; displayed September 21. Exact checkpoint, precision and Context-IR use are unspecified. Arena and AA Elo scales cannot be pooled. |
| B03 | Original 5090 H3 report supports short Ref2VA and Turbo limits | **VERIFIED firsthand report, not reproduced.** [toki_mwc][toki] gives 480²/53.645 s, 720²/136.683 s, 260 frames/394.924 s and old pruned four-step failure. It also records multi-character Japanese dialogue with 0/3 usable results, material contrary evidence. No generated samples are public. Do not generalize its old conversion failure to all newer adapters. |
| B04 | Nuos/ChrisJohnson timings compare directly with supplied Ref2VA | **WRONG.** [Fork raw JSON][nuos] scopes ten-step FL2VA/T2V/I2V, 125 GiB host RAM and complete prompt execution. 3.042-second output: 53.03 s cold/23 s warm; 5.167-second output: 85.23 s warm. Different mode, references, steps, runtime and host prevent claiming a speedup for this workflow. |
| B05 | plox baseline is a 181.7-second warm median | **VERIFIED data.** [Original inline JSON][plox] lists warm runs 179.5, 185.0, 183.6, 179.8 s; middle average 181.7. It uses 25 Euler steps, 1344×768, 124 frames, one 5090 and pruned INT8 FL2VA. One fixed shot/seed; quality pips are AI still-frame ratings. |
| B06 | plox speedups describe vanilla proposed Ref2VA adapters | **WRONG variant mapping.** [Row data][plox]: 0079h/67.4 s uses unpruned FL2VA INT8, its PDD head and strength 1; 0079j/37.1 s uses silveroxides experimental 4–8-step DARE-TIES FL2VA merge at 0.90 plus AdaLN port fix. Both have watched=null. Timings are supported, substituted artifacts and holistic podcast quality are not. |
| B07 | Sparse attention always accelerates heavy Ref2VA | **WRONG.** [Bluepoint data][blue] gives protected Sol 883.298 s versus matched Sage 687.564 s, 28.5% longer including tuning. A separate progressive case is 513.814 versus 667.655 s, 23.04% less. Hardware is unidentified; operator/fixed-seed evidence, not a 5090 benchmark. |
| B08 | Speech-paper tables form one latest-release ranking | **WRONG.** Fresh papers confirm the reported numbers. [InfiniteTalk][infpaper] uses 40 clips/dataset at 480²; HDTF LatentSync FID 16.09/Sync-C 8.99 versus MuseTalk 14.20/7.17. [LatentSync v2][latpaper], March 13, predates 1.6. [MuseTalk v3][musepaper] uses 26 HDTF+10 VFHQ videos. Different crops, edit/generation tasks, versions and protocols prevent pooling. |
| B09 | LeapTalk paper's 200 fps applies to one 5090 | **WRONG.** [Paper v1][leappaper] specifies H200, batch 1 and 512²: Lite 200 fps, Pro 55 fps, Pro HDTF FID 21/FVD 197. Appendix separates A100 results. One-5090 Windows evidence is S01–S05, not this throughput. |
| B10 | AnyTalker interactivity is a three-host semantic score | **WRONG.** [Paper][anypaper] InteractiveEyes has exactly two faces and ~10-second clips. 1.3B interactivity/Sync-C: 0.97/4.56; 14B: 1.01/6.99. Interactivity measures listener-eye motion amplitude, not semantic reaction or word attribution; greater than real motion need not mean better acting. |
| B11 | Tight Studio supplies controlled quality or episode performance | **WRONG.** [Independent report][tight] uses one portrait/3.63-second narration, model-specific settings and different GPUs. Loading/compilation are timed, downloads excluded; whole-GPU memory is polled every 0.5 s. It demonstrates those deployment paths, not controlled human preference, 5090 fit or long-form performance. |
| R01 | Supplied baseline headroom calculations | **VERIFIED arithmetic, parent-supplied measurements.** 32,607/1024 = 31.842773 GiB; base margin 32,607−31,402 = 1,205 MiB = 1.176758 GiB; finish margin = 2,501 MiB = 2.442383 GiB. Base+finish 143.78+182.76 = 326.54 s for 124/24 = 5.1667 s. Local measurements/quality were not rerun or raw traces audited here. |
| R02 | Serialized file sizes prove workload fit | **WRONG.** HF byte metadata proves storage, not allocated/reserved/NVML GPU usage, RSS/commit/cgroup or activation peaks. Incumbent component files total about 47.989 GiB, requiring sequential/offloaded residency. Added references, patches, audio and SAM workspaces remain unmeasured. |
| R03 | Installed 96 GB RAM removes the container's 64 GiB limit | **WRONG.** [Docker docs][docker] distinguish hard RAM and combined memory+swap allowances. Supplied 64/72 GiB container and 72 GiB WSL+8 GiB swap limits do not grow with installed Windows RAM. No live cgroup inventory was performed by this child. |
| R04 | Full three-host podcast is qualified | **UNVERIFIED.** Missing evidence: a held-out recorded three-host excerpt with independent voice binding, quiet listeners/crosstalk, studio prop correctness, repeated cuts, first/middle/last/seam review, blind likeness/lip-sync judgment, failure rate and cold/warm wall time plus GPU/process/container memory. No fetched source provides that complete case for this cast and machine. |

## Consequences for the parent ranking

Calculation source files for I05–I07: [FLUX3 model](https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/flux3/model.py), [FLUX3 driver](https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/flux3/driver.py), [H3 model](https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/minimax/model.py), [H3 driver](https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/minimax/driver.py), [Automagic3](https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/training/automagic3.py), [shared LoRA dtype](https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/families/lora.py).

- Keep the overall ranking as **engineering nominations**, not a demonstrated model-quality leaderboard. LeapTalk has the closest exact-hardware single-person evidence, but changing portrait style/voices/cuts still requires qualification. LatentSync is a technical comparison lead with a default supporting-weight rights gap; MuseTalk's own terms are clearer but its exact Blackwell stack and cast quality are unknown.
- A fixed Blender studio plus Rhubarb and editing has the strongest evidence for deterministic asset/layout/recorded-audio control, with real asset/rigging/animation labor. That is a different visual approach, not a quality-equivalent automatic substitute.
- FLUX3 reference keyframes and optional Base LoRAs are current, concrete candidates; Klein4B is a useful permissive baseline. Their multi-host image-binding/held-out views remain untested.
- H3 recipes belong in technical capability/watch evidence under the supplied US/no-permission condition. Do not nominate H3 generation or training as the next executable test, or imply an encoder/quant/upscaler solves the license.
- Do not infer that no such complete pipeline can exist. The demonstrated conclusion is that this reviewed evidence does not establish one.

Skipped: inference, installs, asset/media inspection, training and local qualification. Material risks: no complete three-host proof, component-license gaps, exact-variant benchmark errors, Windows/WSL differences and unmeasured host/GPU peaks.

[h3license]: https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE
[h3card]: https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/42ed227ee7df40d41602854ae760620d6eb651fe/README.md
[h3qa]: https://huggingface.co/MiniMaxAI/MiniMax-H3/discussions/12
[cobijada]: https://huggingface.co/Winnougan/Cobijada_Minimax-H3_Hybrid_Pruned_ComfyUI/blob/d14d58723efadf84de98bc2c3a3613cfedea4d04/README.md
[fluxlicense]: https://huggingface.co/black-forest-labs/flux-3-image-klein-base/blob/main/LICENSE.md
[fluxrelease]: https://bfl.ai/blog/flux-3-image-klein
[fluxcard]: https://huggingface.co/black-forest-labs/flux-3-image-klein
[klein4]: https://huggingface.co/black-forest-labs/FLUX.2-klein-4B
[qwenlicense]: https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE
[latcode]: https://github.com/bytedance/LatentSync/blob/main/LICENSE
[latweights]: https://huggingface.co/ByteDance/LatentSync-1.6
[insight]: https://github.com/deepinsight/insightface#license
[muse]: https://github.com/TMElyralab/MuseTalk/blob/0a89dec45a0192b824e3cf4daf96c239440c5ed8/README.md
[leaplicense]: https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/LICENSING.md
[leapcode]: https://github.com/zhangrongxiang/LeapTalk/blob/5d8fef8dac3d1c9d3b3d5d9cceb6c76acc81e2c1/LICENSE
[leapweights]: https://huggingface.co/z-rx/leaptalk/blob/b33b788fd1c9b3df11627e00c6a0415272d829dc/README.md
[livenotice]: https://github.com/5461gpt/SoulX-LiveAct-RTX5090/blob/917fc4599d5970332b6866be9ac19cbce9d0b6fa/NOTICE.md
[talklicense]: https://github.com/snap-research/TalkVerse/blob/main/LICENSE.txt
[talk]: https://github.com/snap-research/TalkVerse
[bllicense]: https://www.blender.org/about/license/
[rhlicense]: https://github.com/DanielSWolf/rhubarb-lip-sync/blob/master/LICENSE.md
[leapsetup]: https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/SETUP.md
[leapbench]: https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/BENCHMARKS.md
[leappaper]: https://arxiv.org/html/2608.00079v1
[lat]: https://github.com/bytedance/LatentSync/blob/a229c3948406bc2cf6eaf4873e662e70c6a04746/README.md
[latface]: https://github.com/bytedance/LatentSync/blob/a229c3948406bc2cf6eaf4873e662e70c6a04746/latentsync/utils/face_detector.py
[lat5090]: https://github.com/bytedance/LatentSync/issues/355
[infnative]: https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_wan.py
[inf]: https://github.com/MeiGen-AI/InfiniteTalk/blob/50aa0a94184315407a991ae804d9b58d6d311ba8/README.md
[infpaper]: https://arxiv.org/html/2508.14033v1
[inf5090]: https://github.com/ModelTC/LightX2V/blob/main/configs/infinitetalk/5090/infinitetalk_mutli_distilled.json
[any]: https://github.com/HKUST-C4G/AnyTalker/blob/e5490b60dc6add439ff5ecec5bd7559606a78d53/README.md
[anypaper]: https://arxiv.org/html/2511.23475v1
[live]: https://github.com/Soul-AILab/SoulX-LiveAct/blob/ac8579b907756f228e975922a5159765c93c6a34/README.md
[livebench]: https://github.com/5461gpt/SoulX-LiveAct-RTX5090/blob/917fc4599d5970332b6866be9ac19cbce9d0b6fa/docs/FP4_PREQUANTIZED_BENCHMARK.md
[liveguide]: https://github.com/5461gpt/SoulX-LiveAct-RTX5090/blob/917fc4599d5970332b6866be9ac19cbce9d0b6fa/docs/RTX5090_REPRODUCTION_GUIDE_ZH-TW.md
[tight]: https://github.com/tight-studio/open-source-talking-avatar-benchmark/blob/f0fe080c14b1d814c1ca0d0ce493db04f74868bc/REPORT.md
[longcat]: https://github.com/meituan-longcat/LongCat-Video/blob/6b3f4b8582a8bc3f20f795735f5383716c4ba794/README.md
[echo]: https://github.com/antgroup/echomimic_v3
[lip]: https://github.com/cvlab-kaist/LipForcing
[multi]: https://github.com/Comfy-Org/docs/blob/main/tutorials/video/minimax/minimax-h3-multiframe.mdx
[multijson]: https://github.com/Comfy-Org/workflow_templates/blob/67984637eefcd4825dcf5c134e39034785be1682/templates/video_minimax_h3_multiframe_reference.json
[comfyapi]: https://huggingface.co/api/models/Comfy-Org/MiniMax-H3?blobs=true
[w6]: https://github.com/Comfy-Org/comfy-kitchen/pull/191
[handcode]: https://github.com/sandpies/ComfyUI-Hand-Tie-Clips/blob/073104edd381b7b56d2f2ae80bbe3d9578f764ed/h3_ref_chain.py
[handlog]: https://github.com/sandpies/ComfyUI-Hand-Tie-Clips/blob/073104edd381b7b56d2f2ae80bbe3d9578f764ed/CHANGELOG.md
[handprompt]: https://github.com/sandpies/ComfyUI-Hand-Tie-Clips/blob/073104edd381b7b56d2f2ae80bbe3d9578f764ed/PROMPTING.md
[pdd]: https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs
[lightapi]: https://huggingface.co/api/models/lightx2v/Minimax-h3-Turbo?blobs=true
[turbo]: https://github.com/ModelTC/Minimax-H3-Turbo/blob/02e26d591f7a04d5d1a074c9566d5dd4f22f6225/README.md
[lab]: https://github.com/yuichi-suzuki-highdrama/minimax-h3-local-pipeline-notes/blob/55509fd16464f57512b6c35bf09255d74fa4fdd6/README.md
[union]: https://huggingface.co/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union-2.0/blob/7d2c95de2e351ed6a0b360af45f62daa04c26f88/README.md
[swap]: https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA/blob/96ea3407b230b6f7df71c58c70e31df96bd7e533/README.md
[relay]: https://github.com/akatz-ai/h3-relay/blob/e2a7665b6eb8ea37a88c0de2c9c6b69a8f7c9220/VALIDATION.md
[up]: https://github.com/sandpies/ComfyUI-H3-Video-Upsampler/blob/3403ef7a81742fb76eb1633bdd5bb748ad85fd53/README.md
[h3train]: https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/MINIMAX_H3.md
[assistant]: https://huggingface.co/circlestone-labs/MiniMax-H3-Image-Training-Adapter
[refmod]: https://github.com/Luisacaotica/ComfyUI-MiniMaxH3Mod
[refmodfiz]: https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/REFMOD_HOWDOI.md
[mc57]: https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context/issues/57
[mc48]: https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context/issues/48
[fluxhf]: https://huggingface.co/blog/flux-3
[fluxtrain]: https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/FLUX3.md
[fizinstall]: https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/INSTALL.md
[train5090]: https://github.com/ostris/ai-toolkit/issues/1050
[fiz52]: https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/RELEASE_NOTES_v5.2.0.md
[fiz541]: https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/RELEASE_NOTES_v5.4.1.md
[contextpaper]: https://arxiv.org/html/2510.11000v2
[context]: https://github.com/nenhang/ContextGen
[cycles]: https://developer.blender.org/docs/release_notes/4.4/cycles/
[rh]: https://github.com/DanielSWolf/rhubarb-lip-sync
[rhng]: https://github.com/Premik/blender_rhubarb_lipsync_ng
[kden]: https://docs.kdenlive.org/en/cutting_and_assembling/editing.html
[sprite]: https://studio.blender.org/blog/exploring-production-files/
[spriteedit]: https://studio.blender.org/blog/sprite-fright-edit-bundle/
[night]: https://github.com/bensblueprints/lora-training-pipeline
[aat]: https://artificialanalysis.ai/video/leaderboard/text-to-video
[aai]: https://artificialanalysis.ai/video/leaderboard/image-to-video
[arenat]: https://arena.ai/leaderboard/video/text-to-video
[arenai]: https://arena.ai/leaderboard/video/image-to-video
[toki]: https://zenn.dev/toki_mwc/articles/minimax-h3-rtx5090-benchmark
[nuos]: https://github.com/Nuos/rtx509032g-minimax-h3-comfyui/blob/0409aa4b18767cd7d18de66442671e6dfe7f2ea4/benchmarks/results.json
[plox]: https://plox-1.github.io/h3-speedups/
[blue]: https://bluepointdigital.github.io/minimax-h3-benchmarks/data/gallery-results.json
[latpaper]: https://arxiv.org/html/2412.09262v2
[musepaper]: https://arxiv.org/html/2410.10122v3
[docker]: https://docs.docker.com/engine/containers/resource_constraints/
