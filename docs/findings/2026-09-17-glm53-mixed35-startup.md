# GLM mixed 3.5-bpw EXL3: startup stopped by host-memory exhaustion

**September 17, 2026 · compatibility-only, failed startup · rejected configuration / no promotion**

The mixed 3.5-bpw candidate did not become ready. Loading the pinned checkpoint
exhausted system RAM and all 8 GiB of swap on the dual RTX PRO 6000 Max-Q host.
Linux killed a desktop process and then a model worker; GNOME shut down, while
the operating system remained on the same boot. No candidate inference request
was submitted, so this run provides no quality, context, or speed comparison.
The previously selected 4-bpw configuration remains the recommendation.

## Configuration and scope

| Field | Attempted configuration |
|---|---|
| Model | `satgeze/GLM-5.3-Flash-EXL3-TR3-3.5bpw` |
| Checkpoint revision | `62587d6015184a26773a4ed1b751a9c5fa469cd6` |
| Served name | `glm53-mixed35-v84-nospec-scout` |
| Hardware | Primary Node; native Linux; two 96 GB RTX PRO 6000 Blackwell Max-Q GPUs; 96 GB installed host RAM, approximately 89.7 GiB usable; 8 GiB swap |
| Runtime | Pinned v84-derived vLLM image plus five mixed-trellis source overlays at `8fc95f0da72072a49a697f2164410b851a4e7377`; [exact image digests and files](2026-09-17-glm53-mixed35-startup-evidence/identity.json) |
| Quantization / KV | Mixed K3/K4 EXL3 experts; NVFP4 DS-MLA KV |
| Parallelism | TP2, DCP1, no expert parallelism, no speculation |
| Limits | 327,680 configured tokens, C1 scheduler, batch 2,048, prefill block 64, GPU memory fraction 0.970 |
| Execution | One managed recipe startup; co-resident desktop and benchmark client; no host RAM/swap ceiling |

The [quantizer's report](https://huggingface.co/satgeze/GLM-5.3-Flash-EXL3-TR3-3.5bpw)
was an external prior. Its smaller checkpoint and GPU correspondence did not
establish host-loader fit. CPU import checks for the derived runtime passed;
that did not establish successful GPU startup. This was a whole-configuration
scout: KV format, parallelism, concurrency, and runtime differed from the
selected 4-bpw control.

## Failure and its impact

The retained [kernel and desktop excerpts](2026-09-17-glm53-mixed35-startup-evidence/failure-excerpts.log)
show:

- **14:10:29 UTC:** only 224 KiB swap remained. The global OOM killer killed
  ChatGPT. GNOME reported loss of Xwayland and began shutting down.
- **14:10:54 UTC:** swap was exhausted. The two tensor-parallel workers had
  approximately 77 GiB combined resident memory; Linux killed one worker with
  40,151,892 KiB anonymous RSS.
- **14:11:03 UTC:** vLLM reported engine initialization failure. The managed
  [startup excerpts](2026-09-17-glm53-mixed35-startup-evidence/startup-excerpts.log)
  retain progress through 87 of 120 checkpoint shards and the terminal error.

This establishes host-memory exhaustion during loading. It does not isolate
which loader allocation retained the memory, demonstrate GPU-memory exhaustion,
or establish that every mixed-quant runtime would fail. The apparent reboot
was a graphical-session shutdown; OS uptime and the kernel boot record remained
continuous. Root-filesystem occupancy was investigated separately and is not
identified as the cause of this OOM event.

## Restoration and decision

The failed candidate was removed through managed recipe lifecycle. The exact
saved baseline recipe was reloaded, with its model, image, GPU assignment, and
router configuration preserved. [Restoration evidence](2026-09-17-glm53-mixed35-startup-evidence/restoration.json)
records direct and authenticated routed coding, JSON, and tool checks. This is
container recreation acceptance, not an OS reboot test or renewed full
qualification.

Reject this unbounded startup configuration and retain the selected 4-bpw
baseline. Retry is blocked on managed RAM/swap containment, a desktop reserve,
and investigation of loader peak memory. The
[containment ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-09-17-recipe-host-memory-containment.md)
also requires recovery that survives loss of the initiating desktop. The model
remains unqualified; none of the planned long-context, coding-quality, capacity,
or speculative-decoding cells ran. No model was promoted.

## Evidence

Use the [evidence index](2026-09-17-glm53-mixed35-startup-evidence/README.md),
[artifact manifest](2026-09-17-glm53-mixed35-startup-evidence/artifact-manifest.json),
[decision summary](2026-09-17-glm53-mixed35-startup-evidence/summary.json), and
[predeclared plan](2026-09-17-glm53-mixed35-startup-evidence/run-plan.json).
Public logs are bounded excerpts: private host identifiers and unrelated
process inventories are removed; timestamps and load-bearing numeric fields
are preserved. Full operational evidence remains private.
