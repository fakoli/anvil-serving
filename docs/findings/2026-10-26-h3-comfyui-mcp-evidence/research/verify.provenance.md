# Stage 2 verification provenance
 The terminal B03 wording was clarified to mean zero usable results out of three, not zero failures.
- Date/origin: 2026-10-26 UTC, Windows host local-windows-user.
- Agent: /root/podcast_research_verify, stage-2 fresh-context verifier requested by /root under ${OPERATOR_HOME}/.agents/skills/deep-research/SKILL.md. No child agents spawned.
- Model: inherited parent/runtime; system identifies Codex based on GPT-6. Exact serving engine identifier/version/hash and sampler settings are not exposed. No alternate model was selected.
- Scope: original public source verification and calculations only. Owned files: ${CAMPAIGN_ROOT}/research/verified-claims.md and this file.
- Prohibited work avoided: no inference, weights/media download, installation, source execution, service/repository mutation, private-media access or external/user-chat messaging. Parent coordination used collaboration.send_message only.
- Initial Hindsight recall: memory_recall(alias=memory.imported, budget=low, max_tokens=1500), query “local three-host video podcast research MiniMax Hailuo H3 licenses RTX 5090 identity WSL”. Succeeded. Memory provided leads, not proof or execution authority.
- Final retain: concise source-verified corrections sent with date/origin and tags codex/local-windows-user, same alias. FAILED with memory_unavailable: selected memory route is unavailable. No durable fact was written.
- Inputs read: all three angle reports; all three provenance files. H3/speech/identity reports were reread separately after a combined output truncated. Initial identity provenance was not yet present; its finished file/header, full raw metadata/URL audit and end ledger were read after arrival.
- Baseline GPU/RAM/times are parent-supplied facts, accepted as inputs. This agent independently checked arithmetic, not the local measurement traces or quality.

## Logging integrity

The tool interface does not expose a raw provider/chain JSONL export. This is a reconstructed normalized invocation ledger, not raw execution telemetry. Exact timestamps were not preserved for every batch: the earliest displayed audit clock is 13:43:51 UTC; another is 13:49:09 and the report audit is 14:00:18. Source access was on October 26; artifact preparation continued afterward. The source-batch list preserves order and tools/targets. A functions-state logging mistake (mutating a loaded copy without storing it) meant most tentative per-batch clock records did not persist. They are not fabricated here.

The original tool calls and outputs remain in the current chat history. The final source inventory uses canonical citation URLs; GitHub files were generally fetched through the corresponding raw.githubusercontent.com pinned path. Exact alternate/API/fallback requests are listed separately. A citation URL in the inventory is not a claim that the HTML blob path rather than its identical raw path was the network request.

No public search queries were needed: the verifier fetched the researchers' original URLs and followed one original citation to official H3 Q&A. web.run was used for browsing; read-only PowerShell Invoke-WebRequest/Invoke-RestMethod supplied original-source fallback or public JSON metadata. Site/source code was read as text, never run. Inline plox DATA was parsed strictly as JSON. Original public licenses were decoded from GitHub API base64 content, never inferred only from a repository badge.

## Findings requiring correction

- H3 license V.4 expressly includes outputs/results; official August6 global-distribution Q&A has a narrower creator context and tension with unchanged text. No US local-use/personal-use exemption established.
- plox PDD8 and Turbo4 timings have exact FL2VA/unpruned/experimental-merge/AdaLN-fix qualifications absent from shorthand labels.
- TalkVerse's original license is research-only; a “noncommercial” label alone is insufficient.
- LatentSync default supporting-weight terms, LiveAct missing source license, LeapTalk Windows-only integration and synthetic single-person benchmark boundaries remain material.
- Most WRONG table rows reject tempting inferences stage1 already warns against; do not present their count as researcher-error count.

## Normalized tool-call ledger

```json
[
  {
    "sequence": 1,
    "date": "2026-10-26",
    "tool": "functions.exec → ALL_TOOLS + exec_command",
    "scope": "Discover shared memory tools; read complete deep-research SKILL.md. No agent spawn."
  },
  {
    "sequence": 2,
    "date": "2026-10-26",
    "tool": "functions.exec → Promise.allSettled",
    "scope": "memory.imported focused recall; read all three stage-1 reports and list research directory. Recall succeeded. Combined output truncated; separate full report reads followed."
  },
  {
    "sequence": 3,
    "date": "2026-10-26",
    "tool": "exec_command ×3",
    "scope": "Separately read angle-h3.md, angle-speech.md and angle-identity.md in full."
  },
  {
    "sequence": 4,
    "date": "2026-10-26",
    "tool": "exec_command ×2 + clock",
    "scope": "Read H3 and speech provenance headers; first displayed audit clock 13:43:51 UTC."
  },
  {
    "sequence": 5,
    "date": "2026-10-26",
    "tool": "web.open + clock",
    "scope": "Original H3 pinned license/card, Cobijada raw, FLUX license raw, Qwen license. H3/Qwen succeeded; Cobijada inaccessible and FLUX raw401; later original-source fallback succeeded."
  },
  {
    "sequence": 6,
    "date": "2026-10-26",
    "tool": "web.open + clock",
    "scope": "FLUX3 card/license blob/release, Klein4B card; LeapTalk pinned benchmark/licensing/setup."
  },
  {
    "sequence": 7,
    "date": "2026-10-26",
    "tool": "web.find + clock",
    "scope": "FLUX license entertainment, release timing, card16steps; LeapTalk96.5/70.6/RSS."
  },
  {
    "sequence": 8,
    "date": "2026-10-26",
    "tool": "web.open + clock",
    "scope": "AnyTalker README; LatentSync README/detector/card; InsightFace; LiveAct official/fork benchmark/NOTICE."
  },
  {
    "sequence": 9,
    "date": "2026-10-26",
    "tool": "web.open + clock",
    "scope": "Five primary speech papers; Tight Studio report; native Comfy nodes_wan; InfiniteTalk README."
  },
  {
    "sequence": 10,
    "date": "2026-10-26",
    "tool": "web.find + clock",
    "scope": "Paper tables/protocols/H200; AnyTalker80GB/stage1; native two_speakers; LatentSync18GB; InsightFace terms; InfiniteTalk minute drift."
  },
  {
    "sequence": 11,
    "date": "2026-10-26",
    "tool": "web.open + clock",
    "scope": "Hand-Tie source/changelog/prompting; Union2/swap raw (web inaccessible); Relay validation; multiframe docs; Fizgig FLUX3/H3/RefMod guides; original RefMod README."
  },
  {
    "sequence": 12,
    "date": "2026-10-26",
    "tool": "web.find + exec_command + clock",
    "scope": "Hand-Tie guard compatibility, speaker failure, Relay98.54GiB/9.48seconds, guide visibility, training peaks/window, failed voice transfer; identity-provenance presence check (not yet present)."
  },
  {
    "sequence": 13,
    "date": "2026-10-26",
    "tool": "exec_command Invoke-WebRequest + clock",
    "scope": "Fresh HTTP200 fallback: Cobijada/Union2/Swap pinned cards. Also FlashHead measured revision, LeapTalk weights/code license, MuseTalk README, Fizgig install and LiveAct later guide. Returned bounded matching source passages."
  },
  {
    "sequence": 14,
    "date": "2026-10-26",
    "tool": "exec_command Invoke-RestMethod + clock",
    "scope": "HF blob metadata for Comfy/Kijai/Swap/guide/AnyTalker/LeapTalk/LatentSync. Exact original LiveAct pinned Git tree, 80entries/untruncated, no license match."
  },
  {
    "sequence": 15,
    "date": "2026-10-26",
    "tool": "web.open + clock",
    "scope": "AA and Arena T2V/I2V, toki_mwc original report, Nuos raw results, plox page, Bluepoint JSON, original5090lab notes."
  },
  {
    "sequence": 16,
    "date": "2026-10-26",
    "tool": "web.open bounded + clock",
    "scope": "FLUX license/card clauses; InsightFace current license update; Tight Studio methods and result table."
  },
  {
    "sequence": 17,
    "date": "2026-10-26",
    "tool": "web.find + clock",
    "scope": "FLUX improve/output clauses, Relay residency text (no exact string match), Bluepoint883.298, lab353.4 (web exact match initially missed), tokiZCR.660. Original source context exposed official H3 Q&A contradiction."
  },
  {
    "sequence": 18,
    "date": "2026-10-26",
    "tool": "web.click + clock",
    "scope": "toki original link14 → H3 official discussion12. Page hides some messages; full API was then fetched."
  },
  {
    "sequence": 19,
    "date": "2026-10-26",
    "tool": "exec_command Invoke-RestMethod + clock",
    "scope": "H3 discussion12 full events. Retrieved dated MiniMax-org Australia/globalYouTube and Germany-applicant/globaldistribution replies. Broad printed output truncated; decisive early replies were present. No permission extrapolation."
  },
  {
    "sequence": 20,
    "date": "2026-10-26",
    "tool": "exec_command Invoke-WebRequest + arithmetic + clock",
    "scope": "Pinned FLUX3/H3 model+driver, Automagic3, sharedLoRA andH3family presets. JS recomputed parameter counts/state estimates/headroom/file deltas."
  },
  {
    "sequence": 21,
    "date": "2026-10-26",
    "tool": "web.open + clock",
    "scope": "Blender release/GPU/license (web402), Rhubarb README/license, NG README, Kdenlive editing, SpriteFright blogs (web402), ContextGen paper/repo, NightTaleREADME."
  },
  {
    "sequence": 22,
    "date": "2026-10-26",
    "tool": "exec_command Invoke-WebRequest HTML fallback + web.find + clock",
    "scope": "Original Blender/Cycles/GPU/license/Sprite/Text/Marker/Alpha URLs allHTTP200; HTML stripped for bounded passages. NG5.2 test matrix, Kdenlive audio separation, NightTale untested, ContextGen64.66 checked."
  },
  {
    "sequence": 23,
    "date": "2026-10-26",
    "tool": "web.open + clock",
    "scope": "LipForcing/EchoMimic/TalkVerse/Wan2.2/StableAvatar sources; PDDcard; LightXtree (web inaccessible); kitchenPR191; trainerissues1050/1017."
  },
  {
    "sequence": 24,
    "date": "2026-10-26",
    "tool": "web.find + clock",
    "scope": "LipForcing37GB/unreleased1.3B; Echo12G; TalkVerse license label; WanS2V80GB; StableAvatar3GB exact string not found; quantlayer errors; training1050;1017; PDD rank; H3 clipgrid. Displayed clock13:49:09 UTC."
  },
  {
    "sequence": 25,
    "date": "2026-10-26",
    "tool": "web.open + exec_command + clock",
    "scope": "UpsamplerREADME/YAML(web inaccessible), olderTurboREADME, DiffusersFLUX3blog, Fizgigv5.2/v5.4.1, MotionContext57/48. Read complete provenance contents via Get-ContentRaw to extract file metadata/URL inventories/endnotes; identity provenance now present."
  },
  {
    "sequence": 26,
    "date": "2026-10-26",
    "tool": "exec_command + web.find + clock",
    "scope": "Read identity provenance header; upsampler17frame caveat; Diffusers memory/timing; MotionContext30%flicker/rejected latentfix/cleaninstall."
  },
  {
    "sequence": 27,
    "date": "2026-10-26",
    "tool": "exec_command Invoke-WebRequest + HFmetadata + clock",
    "scope": "ploxsource script/asset discovery (no external data script found); LightX released file inventory and originalUnion2branch byte metadata."
  },
  {
    "sequence": 28,
    "date": "2026-10-26",
    "tool": "exec_command Invoke-WebRequest + clock",
    "scope": "plox inline source number and DATA assignment discovery."
  },
  {
    "sequence": 29,
    "date": "2026-10-26",
    "tool": "exec_command JSON parse + clock",
    "scope": "Safely parse inline DATA as JSON, never execute site JavaScript; first inspected meta,rows were nested under prompts."
  },
  {
    "sequence": 30,
    "date": "2026-10-26",
    "tool": "exec_command JSON parse + clock",
    "scope": "Inspect prompt.rows for183.9/67.4/37.1. Confirm exact PDDunprunedFL2VA and experimentalDARE-TIES+AdaLNport variant corrections."
  },
  {
    "sequence": 31,
    "date": "2026-10-26",
    "tool": "exec_command JSON parse + clock",
    "scope": "ploxbaseline raw four-run median; originallab timings/refine rejection passages via directHTTP."
  },
  {
    "sequence": 32,
    "date": "2026-10-26",
    "tool": "web.open + clock",
    "scope": "LightX2V5090InfiniteTalkconfig; LongCatAvatar1.5README; TalkVerse guessed LICENSE404; pinnedmultiframetemplate; Circlestoneassistant; Dockerlimits."
  },
  {
    "sequence": 33,
    "date": "2026-10-26",
    "tool": "exec_command GitHub license API + clock",
    "scope": "Decode original license content for AnyTalker/InfiniteTalk/LatentSync/MuseTalk/RhubarbNG/TalkVerse. Original LiveAct license endpoint404. TalkVerse LICENSE.txt research-only text verified."
  },
  {
    "sequence": 34,
    "date": "2026-10-26",
    "tool": "apply_patch",
    "scope": "Create verified-claims.md (89rows); no other content files edited."
  },
  {
    "sequence": 35,
    "date": "2026-10-26",
    "tool": "web.open/find + clock",
    "scope": "LatentSyncissue355/pipeline; InfiniteTalkweightcard; currentFlashHeadcomponentcard; AnyTalkermiddle-binding; Bluepointprogressive513.814. Some combined tool output truncated, decisive prior/source-specific results retained."
  },
  {
    "sequence": 36,
    "date": "2026-10-26",
    "tool": "functions state + exec_command",
    "scope": "Prepare37readability revisions; Get-Item and one deliberately truncated local read were non-evidence checks."
  },
  {
    "sequence": 37,
    "date": "2026-10-26",
    "tool": "exec_command + apply_patch",
    "scope": "Read own report in memory; apply37table-row readability replacements."
  },
  {
    "sequence": 38,
    "date": "2026-10-26",
    "tool": "clock + exec_command",
    "scope": "14:00:18 UTC audit: own report89claimrows, no missing Markdown reference definitions,45000bytes before final small header clarification."
  },
  {
    "sequence": 39,
    "date": "2026-10-26",
    "tool": "apply_patch + memory_retain",
    "scope": "Clarify WRONG rows often reject tempting inferences already cautioned against by stage1. Shared durable retention attempted; FAILED memory_unavailable/selected route unavailable. Initial recall had succeeded."
  },
  {
    "sequence": 40,
    "date": "2026-10-26",
    "tool": "apply_patch + final local checks",
    "scope": "Write this provenance and verify both allowed outputs. Terminal artifact metadata/hash checks recorded in final delivery."
  }
]
```

## Canonical claim-source inventory

All access dates are 2026-10-26; release/run/revision dates are recorded beside claims in verified-claims.md. Historical source dates are not substituted for fetch dates.

```json
[
  {
    "label": "h3license",
    "citation_url": "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE",
    "access_date": "2026-10-26"
  },
  {
    "label": "h3card",
    "citation_url": "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/42ed227ee7df40d41602854ae760620d6eb651fe/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "h3qa",
    "citation_url": "https://huggingface.co/MiniMaxAI/MiniMax-H3/discussions/12",
    "access_date": "2026-10-26"
  },
  {
    "label": "cobijada",
    "citation_url": "https://huggingface.co/Winnougan/Cobijada_Minimax-H3_Hybrid_Pruned_ComfyUI/blob/d14d58723efadf84de98bc2c3a3613cfedea4d04/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "fluxlicense",
    "citation_url": "https://huggingface.co/black-forest-labs/flux-3-image-klein-base/blob/main/LICENSE.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "fluxrelease",
    "citation_url": "https://bfl.ai/blog/flux-3-image-klein",
    "access_date": "2026-10-26"
  },
  {
    "label": "fluxcard",
    "citation_url": "https://huggingface.co/black-forest-labs/flux-3-image-klein",
    "access_date": "2026-10-26"
  },
  {
    "label": "klein4",
    "citation_url": "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B",
    "access_date": "2026-10-26"
  },
  {
    "label": "qwenlicense",
    "citation_url": "https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE",
    "access_date": "2026-10-26"
  },
  {
    "label": "latcode",
    "citation_url": "https://github.com/bytedance/LatentSync/blob/main/LICENSE",
    "access_date": "2026-10-26"
  },
  {
    "label": "latweights",
    "citation_url": "https://huggingface.co/ByteDance/LatentSync-1.6",
    "access_date": "2026-10-26"
  },
  {
    "label": "insight",
    "citation_url": "https://github.com/deepinsight/insightface#license",
    "access_date": "2026-10-26"
  },
  {
    "label": "muse",
    "citation_url": "https://github.com/TMElyralab/MuseTalk/blob/0a89dec45a0192b824e3cf4daf96c239440c5ed8/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "leaplicense",
    "citation_url": "https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/LICENSING.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "leapcode",
    "citation_url": "https://github.com/zhangrongxiang/LeapTalk/blob/5d8fef8dac3d1c9d3b3d5d9cceb6c76acc81e2c1/LICENSE",
    "access_date": "2026-10-26"
  },
  {
    "label": "leapweights",
    "citation_url": "https://huggingface.co/z-rx/leaptalk/blob/b33b788fd1c9b3df11627e00c6a0415272d829dc/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "livenotice",
    "citation_url": "https://github.com/5461gpt/SoulX-LiveAct-RTX5090/blob/917fc4599d5970332b6866be9ac19cbce9d0b6fa/NOTICE.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "talklicense",
    "citation_url": "https://github.com/snap-research/TalkVerse/blob/main/LICENSE.txt",
    "access_date": "2026-10-26"
  },
  {
    "label": "talk",
    "citation_url": "https://github.com/snap-research/TalkVerse",
    "access_date": "2026-10-26"
  },
  {
    "label": "bllicense",
    "citation_url": "https://www.blender.org/about/license/",
    "access_date": "2026-10-26"
  },
  {
    "label": "rhlicense",
    "citation_url": "https://github.com/DanielSWolf/rhubarb-lip-sync/blob/master/LICENSE.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "leapsetup",
    "citation_url": "https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/SETUP.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "leapbench",
    "citation_url": "https://github.com/hiroki-abe-58/ComfyUI-LeapTalk/blob/041d7a7ec979489619a446478157055383d20532/docs/BENCHMARKS.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "leappaper",
    "citation_url": "https://arxiv.org/html/2608.00079v1",
    "access_date": "2026-10-26"
  },
  {
    "label": "lat",
    "citation_url": "https://github.com/bytedance/LatentSync/blob/a229c3948406bc2cf6eaf4873e662e70c6a04746/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "latface",
    "citation_url": "https://github.com/bytedance/LatentSync/blob/a229c3948406bc2cf6eaf4873e662e70c6a04746/latentsync/utils/face_detector.py",
    "access_date": "2026-10-26"
  },
  {
    "label": "lat5090",
    "citation_url": "https://github.com/bytedance/LatentSync/issues/355",
    "access_date": "2026-10-26"
  },
  {
    "label": "infnative",
    "citation_url": "https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_wan.py",
    "access_date": "2026-10-26"
  },
  {
    "label": "inf",
    "citation_url": "https://github.com/MeiGen-AI/InfiniteTalk/blob/50aa0a94184315407a991ae804d9b58d6d311ba8/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "infpaper",
    "citation_url": "https://arxiv.org/html/2508.14033v1",
    "access_date": "2026-10-26"
  },
  {
    "label": "inf5090",
    "citation_url": "https://github.com/ModelTC/LightX2V/blob/main/configs/infinitetalk/5090/infinitetalk_mutli_distilled.json",
    "access_date": "2026-10-26"
  },
  {
    "label": "any",
    "citation_url": "https://github.com/HKUST-C4G/AnyTalker/blob/e5490b60dc6add439ff5ecec5bd7559606a78d53/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "anypaper",
    "citation_url": "https://arxiv.org/html/2511.23475v1",
    "access_date": "2026-10-26"
  },
  {
    "label": "live",
    "citation_url": "https://github.com/Soul-AILab/SoulX-LiveAct/blob/ac8579b907756f228e975922a5159765c93c6a34/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "livebench",
    "citation_url": "https://github.com/5461gpt/SoulX-LiveAct-RTX5090/blob/917fc4599d5970332b6866be9ac19cbce9d0b6fa/docs/FP4_PREQUANTIZED_BENCHMARK.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "liveguide",
    "citation_url": "https://github.com/5461gpt/SoulX-LiveAct-RTX5090/blob/917fc4599d5970332b6866be9ac19cbce9d0b6fa/docs/RTX5090_REPRODUCTION_GUIDE_ZH-TW.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "tight",
    "citation_url": "https://github.com/tight-studio/open-source-talking-avatar-benchmark/blob/f0fe080c14b1d814c1ca0d0ce493db04f74868bc/REPORT.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "longcat",
    "citation_url": "https://github.com/meituan-longcat/LongCat-Video/blob/6b3f4b8582a8bc3f20f795735f5383716c4ba794/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "echo",
    "citation_url": "https://github.com/antgroup/echomimic_v3",
    "access_date": "2026-10-26"
  },
  {
    "label": "lip",
    "citation_url": "https://github.com/cvlab-kaist/LipForcing",
    "access_date": "2026-10-26"
  },
  {
    "label": "multi",
    "citation_url": "https://github.com/Comfy-Org/docs/blob/main/tutorials/video/minimax/minimax-h3-multiframe.mdx",
    "access_date": "2026-10-26"
  },
  {
    "label": "multijson",
    "citation_url": "https://github.com/Comfy-Org/workflow_templates/blob/67984637eefcd4825dcf5c134e39034785be1682/templates/video_minimax_h3_multiframe_reference.json",
    "access_date": "2026-10-26"
  },
  {
    "label": "comfyapi",
    "citation_url": "https://huggingface.co/api/models/Comfy-Org/MiniMax-H3?blobs=true",
    "access_date": "2026-10-26"
  },
  {
    "label": "w6",
    "citation_url": "https://github.com/Comfy-Org/comfy-kitchen/pull/191",
    "access_date": "2026-10-26"
  },
  {
    "label": "handcode",
    "citation_url": "https://github.com/sandpies/ComfyUI-Hand-Tie-Clips/blob/073104edd381b7b56d2f2ae80bbe3d9578f764ed/h3_ref_chain.py",
    "access_date": "2026-10-26"
  },
  {
    "label": "handlog",
    "citation_url": "https://github.com/sandpies/ComfyUI-Hand-Tie-Clips/blob/073104edd381b7b56d2f2ae80bbe3d9578f764ed/CHANGELOG.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "handprompt",
    "citation_url": "https://github.com/sandpies/ComfyUI-Hand-Tie-Clips/blob/073104edd381b7b56d2f2ae80bbe3d9578f764ed/PROMPTING.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "pdd",
    "citation_url": "https://huggingface.co/alibaba-pai/MiniMax-H3-Acc-LoRAs",
    "access_date": "2026-10-26"
  },
  {
    "label": "lightapi",
    "citation_url": "https://huggingface.co/api/models/lightx2v/Minimax-h3-Turbo?blobs=true",
    "access_date": "2026-10-26"
  },
  {
    "label": "turbo",
    "citation_url": "https://github.com/ModelTC/Minimax-H3-Turbo/blob/02e26d591f7a04d5d1a074c9566d5dd4f22f6225/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "lab",
    "citation_url": "https://github.com/yuichi-suzuki-highdrama/minimax-h3-local-pipeline-notes/blob/55509fd16464f57512b6c35bf09255d74fa4fdd6/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "union",
    "citation_url": "https://huggingface.co/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union-2.0/blob/7d2c95de2e351ed6a0b360af45f62daa04c26f88/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "swap",
    "citation_url": "https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA/blob/96ea3407b230b6f7df71c58c70e31df96bd7e533/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "relay",
    "citation_url": "https://github.com/akatz-ai/h3-relay/blob/e2a7665b6eb8ea37a88c0de2c9c6b69a8f7c9220/VALIDATION.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "up",
    "citation_url": "https://github.com/sandpies/ComfyUI-H3-Video-Upsampler/blob/3403ef7a81742fb76eb1633bdd5bb748ad85fd53/README.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "h3train",
    "citation_url": "https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/MINIMAX_H3.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "assistant",
    "citation_url": "https://huggingface.co/circlestone-labs/MiniMax-H3-Image-Training-Adapter",
    "access_date": "2026-10-26"
  },
  {
    "label": "refmod",
    "citation_url": "https://github.com/Luisacaotica/ComfyUI-MiniMaxH3Mod",
    "access_date": "2026-10-26"
  },
  {
    "label": "refmodfiz",
    "citation_url": "https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/REFMOD_HOWDOI.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "mc57",
    "citation_url": "https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context/issues/57",
    "access_date": "2026-10-26"
  },
  {
    "label": "mc48",
    "citation_url": "https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context/issues/48",
    "access_date": "2026-10-26"
  },
  {
    "label": "fluxhf",
    "citation_url": "https://huggingface.co/blog/flux-3",
    "access_date": "2026-10-26"
  },
  {
    "label": "fluxtrain",
    "citation_url": "https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/FLUX3.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "fizinstall",
    "citation_url": "https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/INSTALL.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "train5090",
    "citation_url": "https://github.com/ostris/ai-toolkit/issues/1050",
    "access_date": "2026-10-26"
  },
  {
    "label": "fiz52",
    "citation_url": "https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/RELEASE_NOTES_v5.2.0.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "fiz541",
    "citation_url": "https://github.com/shootthesound/Fizgig/blob/20331b14ae514013be8617d21e1cfa6fef592f9f/docs/RELEASE_NOTES_v5.4.1.md",
    "access_date": "2026-10-26"
  },
  {
    "label": "contextpaper",
    "citation_url": "https://arxiv.org/html/2510.11000v2",
    "access_date": "2026-10-26"
  },
  {
    "label": "context",
    "citation_url": "https://github.com/nenhang/ContextGen",
    "access_date": "2026-10-26"
  },
  {
    "label": "cycles",
    "citation_url": "https://developer.blender.org/docs/release_notes/4.4/cycles/",
    "access_date": "2026-10-26"
  },
  {
    "label": "rh",
    "citation_url": "https://github.com/DanielSWolf/rhubarb-lip-sync",
    "access_date": "2026-10-26"
  },
  {
    "label": "rhng",
    "citation_url": "https://github.com/Premik/blender_rhubarb_lipsync_ng",
    "access_date": "2026-10-26"
  },
  {
    "label": "kden",
    "citation_url": "https://docs.kdenlive.org/en/cutting_and_assembling/editing.html",
    "access_date": "2026-10-26"
  },
  {
    "label": "sprite",
    "citation_url": "https://studio.blender.org/blog/exploring-production-files/",
    "access_date": "2026-10-26"
  },
  {
    "label": "spriteedit",
    "citation_url": "https://studio.blender.org/blog/sprite-fright-edit-bundle/",
    "access_date": "2026-10-26"
  },
  {
    "label": "night",
    "citation_url": "https://github.com/bensblueprints/lora-training-pipeline",
    "access_date": "2026-10-26"
  },
  {
    "label": "aat",
    "citation_url": "https://artificialanalysis.ai/video/leaderboard/text-to-video",
    "access_date": "2026-10-26"
  },
  {
    "label": "aai",
    "citation_url": "https://artificialanalysis.ai/video/leaderboard/image-to-video",
    "access_date": "2026-10-26"
  },
  {
    "label": "arenat",
    "citation_url": "https://arena.ai/leaderboard/video/text-to-video",
    "access_date": "2026-10-26"
  },
  {
    "label": "arenai",
    "citation_url": "https://arena.ai/leaderboard/video/image-to-video",
    "access_date": "2026-10-26"
  },
  {
    "label": "toki",
    "citation_url": "https://zenn.dev/toki_mwc/articles/minimax-h3-rtx5090-benchmark",
    "access_date": "2026-10-26"
  },
  {
    "label": "nuos",
    "citation_url": "https://github.com/Nuos/rtx509032g-minimax-h3-comfyui/blob/0409aa4b18767cd7d18de66442671e6dfe7f2ea4/benchmarks/results.json",
    "access_date": "2026-10-26"
  },
  {
    "label": "plox",
    "citation_url": "https://plox-1.github.io/h3-speedups/",
    "access_date": "2026-10-26"
  },
  {
    "label": "blue",
    "citation_url": "https://bluepointdigital.github.io/minimax-h3-benchmarks/data/gallery-results.json",
    "access_date": "2026-10-26"
  },
  {
    "label": "latpaper",
    "citation_url": "https://arxiv.org/html/2412.09262v2",
    "access_date": "2026-10-26"
  },
  {
    "label": "musepaper",
    "citation_url": "https://arxiv.org/html/2410.10122v3",
    "access_date": "2026-10-26"
  },
  {
    "label": "docker",
    "citation_url": "https://docs.docker.com/engine/containers/resource_constraints/",
    "access_date": "2026-10-26"
  }
]
```

## Exact additional, API and fallback URLs requested

- https://huggingface.co/MiniMaxAI/MiniMax-H3/raw/42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE
- https://huggingface.co/MiniMaxAI/MiniMax-H3/raw/42ed227ee7df40d41602854ae760620d6eb651fe/README.md
- https://huggingface.co/Winnougan/Cobijada_Minimax-H3_Hybrid_Pruned_ComfyUI/raw/d14d58723efadf84de98bc2c3a3613cfedea4d04/README.md
- https://huggingface.co/Winnougan/Cobijada_Minimax-H3_Hybrid_Pruned_ComfyUI/resolve/d14d58723efadf84de98bc2c3a3613cfedea4d04/README.md
- https://huggingface.co/black-forest-labs/flux-3-image-klein-base/raw/main/LICENSE.md
- https://huggingface.co/Qwen/Qwen-Image-2.1/raw/main/LICENSE
- https://huggingface.co/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union-2.0/raw/7d2c95de2e351ed6a0b360af45f62daa04c26f88/README.md
- https://huggingface.co/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union-2.0/resolve/7d2c95de2e351ed6a0b360af45f62daa04c26f88/README.md
- https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA/raw/96ea3407b230b6f7df71c58c70e31df96bd7e533/README.md
- https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA/resolve/96ea3407b230b6f7df71c58c70e31df96bd7e533/README.md
- https://huggingface.co/Soul-AILab/SoulX-FlashHead-1_3B/raw/59119b6c681230c3eeee157e224ae1941746711e/README.md
- https://huggingface.co/z-rx/leaptalk/raw/b33b788fd1c9b3df11627e00c6a0415272d829dc/README.md
- https://huggingface.co/api/models/Kijai/MiniMax-H3-experimental?blobs=true
- https://huggingface.co/api/models/akatz-ai/MiniMax-H3-Character-Swap-LoRA?blobs=true
- https://huggingface.co/api/models/sandpies/ComfyUI-H3-Video-Upsampler?blobs=true
- https://huggingface.co/api/models/zzz66/AnyTalker-1.3B?blobs=true
- https://huggingface.co/api/models/z-rx/leaptalk?blobs=true
- https://huggingface.co/api/models/ByteDance/LatentSync-1.6?blobs=true
- https://api.github.com/repos/Soul-AILab/SoulX-LiveAct/git/trees/ac8579b907756f228e975922a5159765c93c6a34?recursive=1
- https://huggingface.co/api/models/MiniMaxAI/MiniMax-H3/discussions/12
- https://raw.githubusercontent.com/shootthesound/Fizgig/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/flux3/model.py
- https://raw.githubusercontent.com/shootthesound/Fizgig/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/flux3/driver.py
- https://raw.githubusercontent.com/shootthesound/Fizgig/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/minimax/model.py
- https://raw.githubusercontent.com/shootthesound/Fizgig/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/minimax/driver.py
- https://raw.githubusercontent.com/shootthesound/Fizgig/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/training/automagic3.py
- https://raw.githubusercontent.com/shootthesound/Fizgig/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/families/lora.py
- https://raw.githubusercontent.com/shootthesound/Fizgig/20331b14ae514013be8617d21e1cfa6fef592f9f/src/fizgig/families/minimax.py
- https://raw.githubusercontent.com/DanielSWolf/rhubarb-lip-sync/master/README.adoc
- https://raw.githubusercontent.com/Premik/blender_rhubarb_lipsync_ng/master/README.md
- https://raw.githubusercontent.com/Francis-Rings/StableAvatar/38625187dc10913ba4facc32341a841ce0a8f29b/README.md
- https://raw.githubusercontent.com/Wan-Video/Wan2.2/main/README.md
- https://github.com/ostris/ai-toolkit/issues/1017
- https://huggingface.co/sandpies/ComfyUI-H3-Video-Upsampler/raw/4592d2b187255c433376bb426c0eec0e9ecdee35/lora/training_config.yaml
- https://huggingface.co/api/models/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union-2.0?blobs=true
- https://raw.githubusercontent.com/snap-research/TalkVerse/main/LICENSE
- https://raw.githubusercontent.com/bytedance/LatentSync/a229c3948406bc2cf6eaf4873e662e70c6a04746/latentsync/pipelines/lipsync_pipeline.py
- https://api.github.com/repos/HKUST-C4G/AnyTalker/license
- https://api.github.com/repos/MeiGen-AI/InfiniteTalk/license
- https://api.github.com/repos/bytedance/LatentSync/license
- https://api.github.com/repos/TMElyralab/MuseTalk/license
- https://api.github.com/repos/Premik/blender_rhubarb_lipsync_ng/license
- https://api.github.com/repos/snap-research/TalkVerse/license
- https://api.github.com/repos/Soul-AILab/SoulX-LiveAct/license

## Access failures and verification limits

- web raw Cobijada, Union2 and Swap were inaccessible; identical pinned resolve text returned HTTP200 through PowerShell. FLUX license raw returned401; original HF blob exposed full license text. Blender/Sprite web extraction returned402; direct public HTTPS returned200. No paywall/account bypass was used.
- LightX HF tree was inaccessible to web; public model API returned the exact revision/file inventory. Upsampler training YAML was inaccessible to web and was not separately fetched in this verification; its claimed rank64/five-frame YAML fields remain stage1-only, although README and metadata were independently verified.
- A guessed TalkVerse LICENSE URL404ed; original GitHub license API located LICENSE.txt and returned its actual research-only terms. Original LiveAct licenseAPI404 and exact untruncated pinned tree support the missing-file claim.
- Exact StableAvatar3GB string was not recovered. CharacterSheet workflow branches, Qwen2.1 specification/stress-test details and TaoMate-H3's multi-GPU recipe were not independently rechecked; the report explicitly leaves those unverified rather than promoting them.
- Some broad source/JSON outputs truncated. Decisive benchmark variants were retrieved with bounded direct source parsing; no claim rests on unseen truncated tail text.
- End-to-end peaks for the custom hybrid/three-host workload, actual host recordings, full-episode identity/lip sync/cuts and all transitive asset licenses were not qualified.

## Local artifact verification

Report creation and follow-up reads confirmed89claimrows and all reference-style links defined. Arithmetic was recomputed without model code. Only the two owned files were created/edited. Report SHA256 before final header note was64CA128F5C418B290B25AE703FDE779E81CABE7E917CA380FC1154223F6CD00B; final hashes are checked after this file is saved.

Provenance prepared through 2026-10-26 14:03:50 UTC. Skipped local inference/install/training; the principal risk is treating source evidence as complete three-host qualification.

### Terminal verification addendum — 14:04 UTC

One final read-only Invoke-WebRequest batch fetched the exact LatentSync pipeline and pinned multiframe JSON again. The source appends decoded results and concatenates them before restore; template parsing confirms Ref2VA pruned INT8, NVFP4+AWQ encoder, both stated VAEs, res_multistep/simple and20steps. Exact request URLs:

- https://raw.githubusercontent.com/bytedance/LatentSync/a229c3948406bc2cf6eaf4873e662e70c6a04746/latentsync/pipelines/lipsync_pipeline.py
- https://raw.githubusercontent.com/Comfy-Org/workflow_templates/67984637eefcd4825dcf5c134e39034785be1682/templates/video_minimax_h3_multiframe_reference.json

Get-Item/Get-Content/Get-FileHash verified both output files and89reportclaimrows; no missing reference-style link definitions. Additional explicit pinned source links were added to the report's calculation notes. Final report SHA256: 58D16CB017CEC108C696FBB958FDE9C685652AEFCC02E0A41A870840B36679E9. Provenance's final hash is supplied in delivery rather than recursively embedded in itself.
