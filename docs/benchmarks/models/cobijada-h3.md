# Cobijada H3

<!-- benchmark-dossier/v2 -->
## Current status and review date

!!! info "Decision snapshot"

    - **Product role:**bounded local video generation and guided finishing candidate.
    - **Selected or best-qualified configuration:**pinned H3 hybrid/pruned checkpoint plus INT8 text encoder; guided2× finish;ComfyUI0.42.0;concurrency1.
    - **Measured hardware:**one RTX5090,32607MiB,Windows/Docker Desktop/WSL2.
    - **Evidence:**functional MCP smoke;124frames/24fps,864×480→1728×960;full decode and scoped teardown passed.
    - **Decision:**no-promotion;public templates unavailable;private deployment enabled under existing authorization.
    - **Important limitation:**no recorded-master/cast/long-form qualification;output-rights clearance unresolved.
    - **Review dates:**evidence cutoff and review2026-10-26.

### Review narrative

The local recipe is retained for further short-shot experiments. It establishes transport,asset handoff,
bounded execution and cleanup; it does not establish a complete three-host podcast generator.

## Immutable identity

The [six-asset lock](../../findings/2026-10-26-h3-comfyui-mcp-evidence/asset-lock.json) binds exact repositories,revisions,filenames,SHA256 and lengths.
The [runtime identity](../../findings/2026-10-26-h3-comfyui-mcp-evidence/runtime-identity.json) pins the final wheel and derived image.
ComfyUI Git metadata is **Not recorded in retained evidence**;retain the immutable base image.
The original H3 territorial/output terms still apply to community derivatives;generated media is held privately.

## Tested hardware and topology

One local RTX5090;no second measured GPU. Windows96GB installed RAM,WSL72GiB configured;
worker64GiB RAM and72GiB combined RAM+swap. GPU peaks include desktop use.
Cold and warm operations and different package revisions are not matched speed arms.

## Engine, quantization, KV, context, and concurrency recipe

[Public named-workflow recipe](../../H3-COMFYUI-MCP.md).
Generation864×480,124frames,24fps,20steps;upscale1728×960,denoise0.1,two Euler steps,
guide1,25000-token tiles. One job at a time. GPU reservation31583MiB plus1024MiB reserve.
Offline flags,read-only assets,reviewed node allowlist and Windows RAM/commit supervisor remain required.
KV/context-token metrics are not applicable to this locked video profile.

## Evidence by measurement class

### Actual MCP smoke

- **Status:**functional,completed.
- **Measured:**generation31301MiB sampled whole-GPU peak;final cold upscale30133MiB and174.973s native execution;one sample each.
- **Limits:**no matched speed/quality comparison,no cast or recorded speech input;AAC identity requires separate stream-copy finishing.
- **Evidence:**[finding](../../findings/2026-10-26-h3-comfyui-mcp.md),[summary](../../findings/2026-10-26-h3-comfyui-mcp-evidence/summary.json),[raw index](../../findings/2026-10-26-h3-comfyui-mcp-evidence/README.md).

## Decision and promotion state

Retain as an operator-owned bounded experimental workflow. Public descriptors remain unavailable.
No reference fleet model/route or global model recommendation changed. A held-out three-host excerpt
and rights clearance are the next gates;training LoRAs is deferred until reference conditioning fails.

## Failures and gotchas

- **Memory:**short-profile generation leaves only1306MiB sampled whole-card headroom;do not start competing GPU models.
- **Timing:**initial base MCP generation0.038s is invalid;native log144.77s is retained and tracking is fixed.
- **Guard:**transient Windows atomic replacement caused a safe idle stop;bounded retry and final cold rerun passed. Full reboot recovery untested.
- **Quality:**bit-identical audio is not correct mouth movement;static sample review is not full listening acceptance.
- **Identity/rights:**exact Comfy Git revision unknown;community quantization does not remove upstream restrictions.

## Dated run history

-2026-10-26—[MCP cold generation,guided upscale,corrections and closure](../../findings/2026-10-26-h3-comfyui-mcp.md).
