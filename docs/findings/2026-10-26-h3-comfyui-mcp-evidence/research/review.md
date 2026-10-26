# Stage 3 — adversarial review of the local podcast research

2026-10-26, Windows host local-windows-user. Fresh-context reviewer: /root/podcast_adversarial_review. Reviewed all three angle reports, the stage-2 claim table and their provenance. This reviews the evidence bundle, not a final brief: the parent had not yet written that synthesis. Fresh primary-source checks are identified below; other bounded facts retain the stage-2 citations. No inference, installation, training or actual cast-media evaluation occurred.

## Findings, ordered by severity

### 1. Blocking: the H3 implementation shortlist cannot be the executable recommendation for the stated setting

**VERIFIED, freshly fetched.** The standard H3 grant excludes US execution and supplies no unpaid/personal exception. V.4 also addresses outputs/results outside its territory; output ownership does not override that provision. Stage 2 reports official discussion guidance on global distribution, but that narrower context does not demonstrate this user's permission for US local inference. A community quant, LoRA, smaller encoder or source-video edit does not resolve the grant. Keep the detailed H3 recipes as conditional technical research; the immediate shortlist must work independently of H3. The report should not tell the user to run H3 once in a test before deciding about licensing. [Original agreement, dated August 2, accessed October 26](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE).

### 2. High: a close-up edit is a useful scope choice, not proof of the original three-host task

**UNVERIFIED complete outcome.** LeapTalk's single portrait, LatentSync's largest-face selection, InfiniteTalk's two audio inputs and AnyTalker's third-person input interface solve different subsets. Three independent portraits do not establish common perspective, gaze, lighting, occlusion, listener behavior or stable seating in one studio. AnyTalker 14B's documented 80-GB requirement excludes its ordinary recipe; 1.3B is an earlier single-person-trained release with unmeasured three-host resource/quality behavior. Stage-2 S06/S08/S11/S14–S16/S22 support these distinctions. [AnyTalker release](https://github.com/HKUST-C4G/AnyTalker/blob/e5490b60dc6add439ff5ecec5bd7559606a78d53/README.md), [native Comfy implementation](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_wan.py).

**Required synthesis change:** say the practical recommendation is an editorial decomposition into speaking singles, reactions and authored wides. If simultaneous generated three-host interaction is mandatory, explicitly leave that capability unqualified rather than imply the close-up route fulfills it. Blender supports a shared authored scene, but its asset/rigging work and visual style are a real tradeoff.

### 3. High: preserved recorded audio, speaker attribution and lip sync are separate acceptance criteria

**VERIFIED implementation conflict; perceptual success UNVERIFIED.** Hand-Tie's master recording path rejects hand_tie pinning, realism boost and audio refinement; motion_context/auto with both switches off is the relevant supported combination. That audio lock does not prove the right person moves their mouth. LatentSync's group-frame selection likewise does not bind three stems. The speech report correctly notes that diarization does not separate overlapping voices. Stage-2 H07–H09/S05/S08 remain essential. [Hand-Tie source](https://github.com/sandpies/ComfyUI-Hand-Tie-Clips/blob/073104edd381b7b56d2f2ae80bbe3d9578f764ed/h3_ref_chain.py).

**Missing input fact:** the evidence does not establish that clean per-host stems exist. Conditioning a host on the full mixed track can animate other speakers' words. Preserve the original master and all timing; use isolated aligned recordings where available and explicitly bound manually attributed segments otherwise. Crosstalk remains a separate unresolved case. Final AAC muxing is not archival sample identity.

### 4. High: exact GPU evidence still has runtime and workload boundaries

**VERIFIED, freshly fetched.** LeapTalk's external October 3 test ran on native Windows 5090; the measured integration excludes WSL2. It produced 96.5 seconds in 70.6 seconds, but 10.4 seconds took 30.3 seconds because one-shot startup matters. Inputs were two fictional portraits and synthetic voices. Review used frames/proxies and no listening. CUDA peak was 8.1 GiB allocated/11.1 reserved, versus about 14.5 GB whole GPU. Warm successful timings omit refused/stopped jobs under high system commit. Consequently neither its long-job throughput nor small allocator number is a promise for dozens of short real-host cuts. [Benchmark](https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/BENCHMARKS.md), [setup](https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/SETUP.md).

Nominate this as the strongest exact-hardware single-person experiment, not the best proven podcast visual quality. Native Windows is a separate environment from the existing WSL/container pipeline. A measured native path can be fully local without being an in-container plug-in replacement.

### 5. High: container limits and decoded footage can dominate a seemingly fitting GPU recipe

**VERIFIED arithmetic; exact new workload UNVERIFIED.** Supplied whole-card headroom is only 1,205 MiB at base sampling and 2,501 MiB during finishing. A 64-GiB container does not inherit the machine's entire 96-GB RAM. Stage-2 H15 documents Relay's monolithic 58-second example at 110.32 GiB cgroup memory plus 62.31 GiB swap; its short staged demonstration is not a same-length reproduction. [Relay validation](https://github.com/akatz-ai/h3-relay/blob/e2a7665b6eb8ea37a88c0de2c9c6b69a8f7c9220/VALIDATION.md), [Docker resource semantics](https://docs.docker.com/engine/containers/resource_constraints/).

At 1728×960, 124 RGB frames occupy **0.575 GiB as uint8 or 2.299 GiB as float32**, before copies, masks and model workspaces. One minute at 24 fps is **6.674 GiB uint8**. These are array-size calculations, not measured application usage. Staged disk outputs and releasing each heavy process are defensible design choices; selecting smaller weight files alone does not prove peak fit. Host RAM, swap/commit and whole-GPU usage need separate measurements.

### 6. Medium: episode compute math must include the accepted finish and failure yield

**VERIFIED calculation from supplied measurements, not a new benchmark.** Duration is 124/24 = 5.1667 seconds. Base 143.78 seconds gives RTF **27.83**; finish 182.76 gives **35.37**; combined 326.54 gives **63.20**. A linear, no-retry projection is therefore 63.20 compute hours per output hour, or 6.32 hours for six output minutes. It is neither a guarantee nor a hardware limit: content, warm starts, overlap, retries, compression and later optimizations can change it.

Finishing is **55.97%** of this pipeline's measured total. Halving base time alone yields combined RTF **49.29**, a 22.02% total-time reduction. Even a hypothetical zero-cost base leaves RTF35.37. This makes selective finishing after acceptance more useful than citing a sampler speedup as an episode speedup. Report attempted and accepted seconds and time per accepted second; failure yield is currently unknown.

### 7. Medium: the 24/25-fps timebase mismatch needs an explicit rule

**VERIFIED arithmetic and source rates; proposed handling.** H3 is 24 fps while the LeapTalk recipe is 25 fps. Reinterpreting 250 frames from 25 to 24 fps turns 10 seconds into **10.4167 seconds**; doing that across an hour adds **150 seconds**. Do duration-preserving frame-rate conversion against timestamps, retain the original master timeline, and inspect lips at cuts/chunk joins. Duplicating/dropping/interpolating frames has visual tradeoffs; no method was tested here. The point is to prevent a simple speed reinterpretation from silently breaking recorded-audio alignment.

### 8. Medium: license-simple shortlist needs component-level qualifiers

Stage-2 L08–L13 shows that code licenses, model metadata and detector weights are separate: LatentSync's usual InsightFace assets and AnyTalker's detector introduce research-only terms; LiveAct's source grant is absent; TalkVerse explicitly restricts research use. Do not put them in the immediate production shortlist without preserving these gaps. MuseTalk's own permission is clearer, but not a transitive audit.

**Freshly checked FLUX3 nuance:** private hobby use is explicitly included, while end-user/production and qualifying-user language make a later public podcast plan a distinct scope. Its section4.b also restricts improving another model with similar function. Do not propose FLUX3-generated turnaround sheets as unrestricted training data for other models. This does not establish a ban on ordinary reference-conditioned inference. FLUX.2 Klein4B is the simpler Apache baseline. [FLUX3 original license](https://huggingface.co/black-forest-labs/flux-3-image-klein-base/blob/main/LICENSE.md), [Klein4B card](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B).

### 9. Medium: task-matched comparisons matter more than leaderboard rank

Stage2 correctly limits AA/Arena to their entries and protocols, plox to its exact FL2VA merges, papers to their evaluated releases/datasets, and LightX2V's 300-second config to an available file. None constitutes a matched Cobijada Ref2VA or recorded three-host benchmark. The 0/3 usable multi-character Japanese-dialogue result in the original 5090 H3 report is material contrary evidence; it is not a universal failure rate. [Original experiment](https://zenn.dev/toki_mwc/articles/minimax-h3-rtx5090-benchmark).

Use an engineering shortlist rather than a single quality ranking. A fixed rendered studio, a mouth editor and a full-frame speech generator cannot be ranked fairly by pooling unrelated FID/Elo/Sync-C numbers.

### 10. Lower: provenance is substantial, but raw logs and local validation are unavailable

The angle provenance and stage-2 provenance disclose reconstructed ledgers, missing raw chain-event exports, output truncations and source fallbacks. Preserve those disclosures. Some URLs target current main branches; the parent should retain the cited dated snapshots and pinned versions instead of silently calling everything latest. The review did not audit private baseline traces, inspect generated media, rerun source code or certify every dependency license. No final prose existed for this reviewer to line-edit.

## Defensible shortlist

1. **Strongest deterministic studio route:** fixed Blender scene/cameras/character assets + Rhubarb mouth cues + Kdenlive original-master edit. Real local component capability; substantial authoring effort and style/acting quality remain unmeasured. Useful when exact set geometry and simultaneous host placement dominate.
2. **Strongest exact-5090 speech experiment:** native Windows LeapTalk, one host per approved portrait/shot, sequential jobs and fixed-background/cutaway edit. Start from its pinned measured recipe. Exact cast likeness, recorded voices, masks/compositing and cross-cut consistency need acceptance tests.
3. **Best source-shot preservation comparison:** MuseTalk on one visible host/source clip, retaining the studio and body performance. LatentSync1.6 remains a technical comparison pending supporting-weight permission and a working Blackwell stack. Neither is a native three-face solution.
4. **More expressive optional branch:** InfiniteTalk V2V for source-guided gestures and one/two speakers; exact 5090/container resources and long consistency require qualification. AnyTalker1.3B stays exploratory for three voices, not a production recommendation.
5. **Keyframe/identity support:** FLUX.2 Klein4B permissive baseline; FLUX3 reference generation for the stated private hobby case under its full terms. Train separate identity LoRAs only after held-out references fail consistently. These tools do not supply temporal identity or studio geometry by themselves.

H3's native Ref2VA guides, supported master-audio motion_context path, matched PDD/Ref8, Union2 and source-performance swap remain a conditional technical appendix. No reviewed evidence proves a fully local, coherent three-host production pipeline for this cast and hardware.

## Smallest acceptance evidence still missing

Use a held-out real excerpt that includes each speaker, a silent listener, a turn transition, crosstalk and at least one return to an earlier camera. Judge identity against source photographs, mouth timing while listening, wrong-speaker motion, studio props/text, occlusion and first/middle/last/seam frames. Fix the editorial timing first, compare the same excerpt across viable branches, and preserve all failures. Report full wall time, startup/load/processing separately, whole-GPU and allocator peaks, process RAM plus container/commit limits, and accepted footage per attempt. A future test needs real media and selected licensed components; this review did not run it.

Skipped: downloads, installs, inference, training, media inspection and exhaustive transitive-license audit. Risks: complete three-host quality is unproved; H3's stated US grant mismatch, timing mistakes, source-variant mismatches and unmeasured resource peaks can invalidate a seemingly successful demo.
