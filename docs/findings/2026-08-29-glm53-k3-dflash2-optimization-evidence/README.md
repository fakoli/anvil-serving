# GLM-5.3-Flash K3/DFlash2 optimization artifacts

Sanitized local evidence for the dual-RTX-PRO-6000-Max-Q campaign finalized on
2026-08-30. The authoritative narrative is the
[dated finding](../2026-08-30-glm53-k3-dflash2-1m-optimization.md). These files
contain synthetic benchmark prompts and outputs, loopback endpoints, and the
exact served identity; they contain no credentials or reachable private
network identity.

> **Publication redaction:** Operator-specific absolute corpus paths in the two
> linked image-only artifacts were replaced with the stable repository-relative
> corpus path. Corpus identity and content hash, measurements, model outputs,
> and event ordering are unchanged.

## Exact identity

- Target:
  `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1@319d66a8b53092b491f698440ecea781e4ddd4e4`
- Draft:
  `incoai/GLM-5.3-Flash-DFlash2@dc77ff1c99eeb2df044ee3d4f0094eb033fee410`
- Runtime image:
  `sha256:001a45bd71bcf908a8c07459570bdb8c5e0a205d085f29ac7f3201529fa3eb75`
- Hardware: 2x RTX PRO 6000 Blackwell Max-Q, 96 GB each, TP=2/DCP=2,
  PCIe without NVLink, Docker Desktop/WSL2

## Research and feasibility

- [External priors](external-priors.json)
- [Preload feasibility input](feasibility-input-preload.json)
- [Preload feasibility result](feasibility-result-preload.md)

The feasibility calculation classified the selected K3 target plus DFlash2
draft as a benchmark survivor at the required 250K+8,192/C2 envelope. It did
not claim runtime compatibility or quality; those are established by the
artifacts below.

## Incumbent baseline

- [Full functional preflight](baseline-k4-fixedk5-262k-preflight-full-low.json)
- [Bounded quality](baseline-k4-fixedk5-262k-quality-all-high-r3.json)
- [Image-only corpus](baseline-k4-fixedk5-262k-image-corpus-c1-r2.json)
- c1 depth sweep:
  [4K](baseline-k4-fixedk5-262k-c1-ctx4096-r5.json),
  [32K](baseline-k4-fixedk5-262k-c1-ctx32768-r5.json),
  [65K](baseline-k4-fixedk5-262k-c1-ctx65536-r5.json),
  [131K](baseline-k4-fixedk5-262k-c1-ctx131072-r5.json),
  [240K](baseline-k4-fixedk5-262k-c1-ctx240000-r5.json)
- short concurrency:
  [C2](baseline-k4-fixedk5-262k-c2-ctx4096-r8.json),
  [C4](baseline-k4-fixedk5-262k-c4-ctx4096-r12.json),
  [C8](baseline-k4-fixedk5-262k-c8-ctx4096-r16.json),
  [C16](baseline-k4-fixedk5-262k-c16-ctx4096-r32.json)
- long C2:
  [131K](baseline-k4-fixedk5-262k-c2-ctx131072-r4.json),
  [240K](baseline-k4-fixedk5-262k-c2-ctx240000-r2.json)

## Selected K5/2048 profile

- [Full functional preflight including 250K target](challenger-k3-dflash2-k5-1m-preflight-full-low-250k.json)
- [Exact 950K-target retrieval](challenger-k3-dflash2-k5-1m-needle-950k-low.json)
- [Bounded quality](challenger-k3-dflash2-k5-1m-quality-all-high-r3.json)
- [Image-only corpus](challenger-k3-dflash2-k5-1m-image-corpus-c1-r2.json)
- [Restored direct acceptance](winner-k5-2048-restored-preflight-low.json)
- [Authenticated routed acceptance](winner-k5-routed-preflight-low.json)
- [Rejected forced-thinking-off control](winner-k5-2048-restored-preflight.json)
- c1 depth sweep:
  [4K](challenger-k3-dflash2-k5-1m-c1-ctx4096-r5.json),
  [32K](challenger-k3-dflash2-k5-1m-c1-ctx32768-r5.json),
  [65K](challenger-k3-dflash2-k5-1m-c1-ctx65536-r5.json),
  [131K](challenger-k3-dflash2-k5-1m-c1-ctx131072-r5.json),
  [240K](challenger-k3-dflash2-k5-1m-c1-ctx240000-r5.json),
  [500K](challenger-k3-dflash2-k5-1m-c1-ctx500000-r3.json)
- short concurrency:
  [C2](challenger-k3-dflash2-k5-1m-c2-ctx4096-r8.json),
  [C4](challenger-k3-dflash2-k5-1m-c4-ctx4096-r12.json),
  [C8](challenger-k3-dflash2-k5-1m-c8-ctx4096-r16.json),
  [C16 run 1](challenger-k3-dflash2-k5-1m-c16-ctx4096-r32.json),
  [C16 run 2](challenger-k3-dflash2-k5-1m-c16-ctx4096-r32-repeat2.json)
- long C2:
  [131K](challenger-k3-dflash2-k5-1m-c2-ctx131072-r4.json),
  [240K](challenger-k3-dflash2-k5-1m-c2-ctx240000-r2.json),
  [500K](challenger-k3-dflash2-k5-1m-c2-ctx500000-r2.json)

## Alternate and rejected settings

- K3 alternate:
  [4K c1](challenger-k3-dflash2-k3-1m-c1-ctx4096-r5.json),
  [C16 run 1](challenger-k3-dflash2-k3-1m-c16-ctx4096-r32.json),
  [C16 run 2](challenger-k3-dflash2-k3-1m-c16-ctx4096-r32-repeat2.json),
  [bounded preflight](challenger-k3-dflash2-k3-1m-preflight-bounded-low.json)
- K5 with 4,096-token scheduler chunks, rejected:
  [4K c1](challenger-k3-dflash2-k5-bt4096-1m-c1-ctx4096-r5.json),
  [C16](challenger-k3-dflash2-k5-bt4096-1m-c16-ctx4096-r32.json)

## Integrity and publication

- [SHA-256 manifest](sha256sums.txt)
- [Publication summary](publication-summary.md)

The machine-readable benchmark artifacts are the measurement authority. The
finding and publication summary are derivative views. External priors are not
local results, and the client/routing promotion evidence is retained privately
because it contains real operator topology.
