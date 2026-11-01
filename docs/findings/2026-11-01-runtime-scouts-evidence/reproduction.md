# Configuration and reproduction boundary

The operator designated the target selection private. This record therefore
withholds target/draft repositories and revisions, model and image fingerprints,
served identities, endpoint values, container IDs, GPU UUIDs and machine paths.
Public endpoint/clock/model labels are synthetic; null fingerprints are deliberate
redactions, not missing private launch controls. There is no public executable
recipe or claim of independently reproducible model qualification.

| Control | Baseline | Cache-layout candidate | DFlash candidate |
|---|---|---|---|
| Target/runtime | Fixed private FP4 target and pinned vLLM fork | Same | Same |
| Hardware | Two RTX PRO 6000 Blackwell Max-Q; TP2/DCP2 | Same | Same |
| Internal context ceiling | 327,680 | 1,048,576 | 327,680 |
| Maximum sequences / batch | 4 / 4,096 | Same | Same |
| Target KV | FP8 | Same | Same |
| KV budget per GPU | 7,650,410,496 bytes | Same | Same |
| Speculation | MTP3 | MTP3 | DFlash2 K3; draft TP2, TRITON_ATTN, BF16 draft KV, standard rejection |
| Host-memory containment | 52 GiB, no container swap | Same | Same |
| GPU power limit | 300 W per card | Same | Same |
| Request policy | Max reasoning requested; temperature 0; output cap 4,096; unique prompts | Same | Same |

Each experiment had its own before/candidate/restored sequence. Three repeats
of four requests covered nominal 8K/32K and C1/C4. Actual input was about 6K/25K.
Warmup preceded timed cells. Natural one-line summaries were observed without
request canaries or exact output-length enforcement. Native observe-mode
eligibility is not controlled-decode or public model qualification.

To inspect retained data, use `anvil-serving eval benchmark evidence show` on
any timed JSON file. Rebuild each chart with the repository's
`plot_benchmark_matrix.py` and its adjacent graph manifest. The repository test
`tests/test_runtime_scout_publication.py` recomputes published medians and checks
the decision and public artifact hashes without inference.

Native timing envelopes/journals retain their schemas and numeric fields.
Journal references were rehashed after redaction. Telemetry preserves numeric
memory, request-counter and allocation fields while removing container identity.
Private original-to-public mappings are retained by the operator. Exact launch
and restoration identity comparisons remain operator-reviewed receipts, not
publicly verifiable private-asset fingerprints. No cloud credential, account
state or secret file is part of this bundle.
