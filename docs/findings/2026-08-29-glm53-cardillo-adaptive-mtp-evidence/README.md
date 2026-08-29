# GLM-5.3-Flash qualification evidence

Bounded public artifacts for the
[2026-08-29 qualification](../2026-08-29-glm53-cardillo-purtell-qualification.md).
The artifacts contain sanitized request metrics and deterministic check
outcomes. They do not contain credentials, private network identities, model
weights, container logs, or response bodies beyond the bounded benchmark
contract.

## Artifact groups

- Feasibility: `feasibility-input-v0.json` and
  `feasibility-result-v0.md`.
- Adaptive rejection: `preflight-tools-low-r1.json`,
  `preflight-tools-repeat01.json`, `adaptive-coding-agent-v2-low-r3.json`,
  and the two adaptive capacity runs.
- Fixed-K5 262K: full preflight, tools, agent protocols, bounded coding, and
  matched 4K/128K capacity artifacts.
- Fixed-K5 524K: calibrated near-500K retrieval/tool probes, high-reasoning
  tools/coding, and short c16 capacity.
- Vision fixed-K5 262K: complete text/image/OCR preflight, approximately 250K
  retrieval, bounded high-reasoning coding, matched 4K/128K c1 performance,
  and short c16 scheduling.
- No-speculation: full preflight, bounded coding, matched 4K/128K/c16
  capacity, and calibrated 524K retrieval/tool probes.
- Derivative publication copy: `publication-summary.md`. This file is not
  primary measurement evidence.

## SHA-256 manifest

| SHA-256 | Bytes | File |
|---|---:|---|
| `97fa9b4655867a79e0bdec14dea0f421e6c2244ebe1724710109b967f5d74cdc` | 4,568 | `adaptive-capacity-128k-c1-low-r3.json` |
| `5e4c5621132907a4308dea2cf0fe2b703a531a7ca6529fa2ff8b975c41afb240` | 4,554 | `adaptive-capacity-4k-c1-low-r3.json` |
| `5b0b734a5e12b009e9771c08df16042a70779c9625cf2c047f109a9ef0b3b1bc` | 38,275 | `adaptive-coding-agent-v2-low-r3.json` |
| `3eb3f14a5b7ec5333d02dfb0b0deca0ce9423ea826c41f38b172b9ce4c5d4510` | 8,722 | `feasibility-input-v0.json` |
| `6ba66dea9b99a306ebd474684ab6e9fa36ca17d6a16861b2903372e4cbdc7874` | 1,678 | `feasibility-result-v0.md` |
| `9397a26d71ef3b8809ff3eea43a8a1aa51dc63c2e757e81e6fbfe4747d8525d7` | 12,317 | `fixed-mtp5-524k-capacity-4k-c16-low-r16.json` |
| `abf4297709d73c9ed1d50cad843ddf70523ba4edd61850f263c6431db274b503` | 44,077 | `fixed-mtp5-524k-coding-agent-v2-high-r3.json` |
| `9447d9b44407cf849719cf01397d3d665e5b7100f4de2a6ede8ba8e2762162e2` | 1,663 | `fixed-mtp5-524k-long-tool-500k-actual-low.json` |
| `2aa294de88eece1d9ccc720a549cf3d5abdcfb1dfae8913abf947c5fef5ab159` | 1,502 | `fixed-mtp5-524k-needle-500k-actual-low.json` |
| `519cf811bd0682b3a01d61d16a19e1eb5d57d15caaf2c7afd5a8edfa48a5f368` | 12,463 | `fixed-mtp5-524k-tools-high-r1.json` |
| `757591303efeddc3c2590add8820bb4bb302502b1855824e35d4d61cbbb38e09` | 5,034 | `fixed-mtp5-agent-protocol-low.json` |
| `d954716a00b1b65e72a83d300d3698ff695e68f332d16e24a8a13752d90d4b70` | 4,567 | `fixed-mtp5-capacity-128k-c1-low-r3.json` |
| `d9177e774e138dde42401792a6b3a6a329c369519cda521cefb3a4d1f82f5223` | 4,550 | `fixed-mtp5-capacity-4k-c1-low-r3.json` |
| `ff61345bf770a3a11ac5b8ab71cc0dae95920e326cce426511454d8c9a46d7c0` | 38,961 | `fixed-mtp5-coding-agent-v2-low-r3.json` |
| `1441a92f25e2c02cab21d35874956adcff9240c9367fcd283d03216278adaa78` | 15,101 | `fixed-mtp5-preflight-full-low-headroom.json` |
| `657b77e8ef6c87da636a7bbcc5122b2eb9e5f3ba81049585a4c1e7fcef2305b8` | 12,462 | `fixed-mtp5-tools-low-r1.json` |
| `c36c3c29090418fff44ca22e7463718477079a8153f75c8ed7a7a37e177dd50e` | 1,659 | `nospec-524k-long-tool-500k-actual-low.json` |
| `1f264891361408bfc0ae993b32414953407bbc3ca8aa7c5d3f1141c28ca99125` | 1,659 | `nospec-524k-long-tool-500k-low.json` |
| `0f65b5d1ce5f60c4094e028f1195e5b97653b9088692f18497f0f678dd28c155` | 1,498 | `nospec-524k-needle-500k-actual-low.json` |
| `10957b92feb5406ee5769ca5b51cca26684ac94469cb7f3f0420bf5261d4bf10` | 1,498 | `nospec-524k-needle-500k-low.json` |
| `66f922c7392b73634e48f762830472be2605c3016d7f1266394bf5cbefef5b6a` | 4,577 | `nospec-capacity-128k-c1-low-r3.json` |
| `8ef7cdb586ef0507411fc2915851d31db16edf949024ce4a6e6219204fac6510` | 4,537 | `nospec-capacity-4k-c1-low-r3.json` |
| `926822c8526e3e18e8056842e5fe3c861eae57f6114a24df57b5ac9b72f036f8` | 12,295 | `nospec-capacity-4k-c16-low-r16.json` |
| `3ab794b8c8cd93bae73f8bfab896dca284f0a6c2da7cc7395506297c293b861d` | 38,674 | `nospec-coding-agent-v2-low-r3.json` |
| `f18ceb0cf99ee393e23388e28f5ad28505f6522897a9f10ccea581905786fae7` | 17,930 | `nospec-preflight-full-default.json` |
| `4bbf220ad3f6023d21b7411a0c668cf7b9513e0cc416f6d8f7e9ca929366c570` | 15,263 | `nospec-preflight-full-low-headroom.json` |
| `956e49c78fa8bbf4096d216996a0cdef95469193b9ccd91c1f1efe579cbadf12` | 17,705 | `preflight-tools-low-r1.json` |
| `649345e7dec37b0aadea888541ce94d19a3e1eca6db5f4f02158148bd3a11be3` | 15,240 | `preflight-tools-repeat01.json` |
| `1a351226c91b8328bbfa3cb7dadba2e8a78d435d69cd5fde8fadccdc7d75c852` | 4,571 | `vision-fixed-mtp5-capacity-128k-c1-low-r3.json` |
| `e2652e420c5f6d32ca06ba4c72e77e02b3aad79aa128ee49f9b356e61624918f` | 4,562 | `vision-fixed-mtp5-capacity-4k-c1-low-r3.json` |
| `5ea495fd2553a0ec813b352ed0a281a2f9e8ba84bb0c2463a2505c0441c7c7fd` | 12,308 | `vision-fixed-mtp5-capacity-4k-c16-low-r1.json` |
| `c159a5499a9b4c848cc4784bd60fde2337d84e3d0995a4c124836425ac4abcb7` | 43,838 | `vision-fixed-mtp5-coding-agent-v2-high-r3.json` |
| `3533de60d36d906f5fdc745385bf07f6f19c2c6846799f0cbc4e87cd84d3dc36` | 1,508 | `vision-fixed-mtp5-needle-250k-low.json` |
| `4f95b916e1d6c1d52e2095eb808b2ab7729560b26e815c8c95e351b4079c3f74` | 21,842 | `vision-fixed-mtp5-preflight-all-low.json` |

The manifest hashes all primary artifacts retained at qualification close.
The narrative, this index, and derivative publication summary are versioned by
Git instead.
