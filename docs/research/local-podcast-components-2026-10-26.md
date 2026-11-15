# Local podcast components: public research

**Reviewed:** 2026-10-26. These are external research leads and reusable recipes,
not claims about an operator's installed generator or local qualification.

Preserve the recorded master timeline and separate speaker stems when available.
Use held-out singles and a shared studio wide to test likeness, speaking-mouth
attribution, listener behavior, geometry, occlusion and cuts. Diarization labels
speakers; it does not separate overlapping voices.

| Candidate | Reusable recipe and external hardware evidence | Qualification boundary |
| --- | --- | --- |
| LeapTalk | [Pinned native-Windows integration benchmark](https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/BENCHMARKS.md): one portrait and aligned audio; Python 3.12.13, torch 2.7.1/cu128, SDPA, no compile. RTX 5090: 96.5 seconds output in 70.6 seconds queue-to-done; 8.1 GiB allocated / 11.1 GiB reserved CUDA, about 14.5 GB whole GPU. | Single synthetic portrait/voice evidence; no three-host or human-listening acceptance. This integration explicitly excludes WSL2. A 10.4-second job took 30.3 seconds, so long-run timing does not predict short-shot overhead. |
| MuseTalk | [Pinned source-shot mouth-editing recipe](https://github.com/TMElyralab/MuseTalk/blob/0a89dec45a0192b824e3cf4daf96c239440c5ed8/README.md) preserves most source pixels and performance. | Exact Blackwell Windows stack and whole-process peaks remain unqualified. U-Net file size is not total VRAM. Inspect auxiliary-model terms and fine mouth details. |
| Blender + Rhubarb Lip Sync NG + Kdenlive | One authored studio, explicit cameras, shared character assets, per-host mouth cues and master-audio assembly. [Rhubarb integration](https://github.com/Premik/blender_rhubarb_lipsync_ng), [Blender production assets](https://studio.blender.org/films/sprite-fright/). | Predictable geometry requires authoring and rigging; it is not evidence of photoreal neural replacement. Scene memory depends on assets and render settings. |
| FLUX.2 Klein 4B | [Publisher reference recipe and Apache terms](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B): four steps, guidance 1 and offload; publisher reports about 13 GB inference VRAM. | Reference images do not establish temporal identity or video LoRA compatibility. Test held-out views before training. |
| InfiniteTalk V2V | [Pinned single/two-speaker recipe](https://github.com/MeiGen-AI/InfiniteTalk/tree/50aa0a94184315407a991ae804d9b58d6d311ba8): source performance, aligned audio and masks, sequential bounded clips. | A 5090 configuration exists; its 300-second target is not measured success. Two-speaker support does not establish three-host quality, peaks or long-run continuity. |

## Multiple benchmark perspectives

Keep datasets, checkpoints and tasks separate; the following papers do not form
one pooled leaderboard.

| Original study | Matched evidence | Interpretation |
| --- | --- | --- |
| [InfiniteTalk v1](https://arxiv.org/html/2508.14033v1), 40 HDTF source clips with changed speech | MuseTalk / LatentSync / InfiniteTalk CSIM .933 / .916 / .775; Sync-C 7.17 / 8.99 / 9.35 | Identity preservation and sync confidence measure different properties; full-frame regeneration can drift. |
| [MuseTalk v3](https://arxiv.org/html/2410.10122v3), 26 HDTF clips without restoration | MuseTalk FID 6.52, CSIM .86, LSE-C 6.53; LatentSync 8.41, .84, 7.90 | Better image/identity scores can coexist with lower sync confidence; this differs from the preceding protocol. |
| [LeapTalk v1](https://arxiv.org/html/2608.00079v1), H200 batch one | Pro FID 21 / FVD 197 / Sync-C 8.38; Lite 38 / 285 / 8.14 | Within-paper quality/speed evidence; H200 throughput is not 5090 throughput or multi-person validation. |

Practical reports and reusable graphs are stronger evidence of a working recipe
than popularity alone. They remain demonstrations with selected inputs, not a
representative sentiment survey or proof of a complete three-host production.

## Smallest informative next test

Use the same held-out excerpt with a silent listener, a turn change, overlap and
a return to an earlier camera. Review first/middle/last frames and joins while
listening. Record startup, processing, finishing, GPU/host/container peaks, failed
attempts and accepted footage yield. Preserve timestamps when mixing 24- and
25-fps paths: relabeling 250 frames from 25 to 24 fps stretches 10 seconds to
10.4167 seconds; it is not a duration-preserving conversion.
