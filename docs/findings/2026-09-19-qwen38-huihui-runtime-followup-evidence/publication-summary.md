# Publication summary: Huihui NVFP4 compatible-runtime follow-up

<!-- benchmark-publication-summary/v1 -->

This is derivative local copy. The [dated finding](../2026-09-19-qwen38-huihui-runtime-followup.md) and native evidence are authoritative; no external post was sent.

## Canonical facts

- **Candidate:** pinned Huihui NVFP4 `18144690`, NInfer `70434721`, RTX 5090, Windows/WSL2 direct 8K/C1, vision enabled, INT8 KV.
- **Recovery:** smoke 2/2, no-spec core 9/9, MTP3 core 9/9, MTP3 preflight 7/7, and MTP3 vision 12/12.
- **Isolated MTP comparison:** matched strict 32-word seed-43 n12 no-spec/MTP3 cells: 71.4/176.2 tok/s mean decode and 908/524 ms mean E2E.
- **Workload-matched cross-profile comparison:** GGUF passed preflight 4/4 and core 9/9, with 107.7 tok/s mean decode and 2,572 ms mean E2E. It uses a different checkpoint, engine/runtime, and quantization, so it is not causal speculation evidence.
- **32K:** MTP3 preflight 7/7, core 9/9, session 3/3, and strict 24K-input 6/6; no no-spec 32K pair.
- **Footprint:** post-workload GPU use including desktop/driver is Huihui MTP3 21,852 MiB and GGUF 19,022 MiB; not peak/RSS. The footprint requirement is missed.
- **Decision:** keep working candidate; `no-promotion`, `promoted=false`. Full finalist, endurance, application, deployment, and live-client acceptance remain unqualified.
- **Restoration:** verified; media services are HTTP 200, temporary containers absent, original incumbent and three manifest hashes unchanged.

## Short post draft

```text
Local RTX 5090 follow-up: a compatible runtime recovered Huihui NInfer tool behavior and MTP3 improved a matched short-output cell. It still uses about 15% more post-workload GPU memory than GGUF, so no promotion. Evidence: dated local finding.
```

## Screenshot alt text

RTX 5090 8K/C1 chart: Huihui NInfer MTP3 has higher mean decode and lower mean E2E than its no-spec control. Separate panels compare a different GGUF profile that uses less post-workload GPU memory; no promotion is granted.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Compatible runtime recovers tool contract | original and repaired 8K/C1 probes | [original](quality-original-tools.json); [repaired](quality-schemafix-off.json) |
| MTP3 improves isolated short-output cell | 8K/C1, seed 43, strict 32 words, n12 each | [no-spec](capacity-nospec-32.json); [MTP3](capacity-mtp3-32.json); [graph](benchmark-graph-data.json) |
| GGUF comparison is cross-profile and lower footprint | workload-matched 8K/C1 n12; distinct checkpoint/runtime/quant | [GGUF](capacity-incumbent-32.json); [configuration](configuration.json) |
| 32K does not establish whether the 8K gain extends | MTP3 only, strict 24K-input n6 | [32K](capacity-mtp3-32k-24k-input.json) |
| No promotion and verified restoration | missing finalist/client gates; managed post-run checks | [finding](../2026-09-19-qwen38-huihui-runtime-followup.md); [restoration](restoration.json) |
