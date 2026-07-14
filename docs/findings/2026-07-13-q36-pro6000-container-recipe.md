# q36 on RTX PRO 6000: container recipe and first characterization

**Point-in-time record, 2026-07-13.** The actual `ambud/q36` engine built and
served successfully on Fakoli Dark's RTX PRO 6000 Blackwell Max-Q. This is the
first physical PRO 6000 result for a project whose pinned README described that
GPU as architecturally compatible but not yet physically tested. The result is
an experiment, not a Heavy-tier promotion or routing change.

- Overall operational result: **PASS**
- Arithmetic smoke result: **PASS**, with and without MTP
- Reasoning-heavy result: **FAIL at the tested 1,024, 2,048, and 4,096 output-token budgets**
- Recommendation: **needs more data; do not promote**
- Container recipe: `examples/fakoli-dark/q36/README.md`
- Raw evidence: [2026-07-13-q36-pro6000-container-recipe-evidence](2026-07-13-q36-pro6000-container-recipe-evidence/reproduction.md)
- Bounded engine logs: [runtime-log-excerpts.txt](2026-07-13-q36-pro6000-container-recipe-evidence/runtime-log-excerpts.txt)

## Source and configuration

The implementation follows q36's pinned [README](https://github.com/ambud/q36/blob/458eb018997565445f0ce0a4887ed7cdfeab756b/README.md)
and [engine reference](https://github.com/ambud/q36/blob/458eb018997565445f0ce0a4887ed7cdfeab756b/docs/ENGINE.md).
The engine is deliberately specialized for Qwen3.6-35B-A3B MXFP4 GGUF on
Blackwell; the production ThinkingCap FP8 checkpoint is not load-compatible.
Consequently, the requested thinking-heavy check used a reasoning-heavy prompt
on the q36-supported model rather than relabeling a vLLM ThinkingCap run.

| Field | Tested value |
|---|---|
| Engine | q36 commit `458eb018997565445f0ce0a4887ed7cdfeab756b` |
| Model | `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` |
| Model revision | `5bc3e238d916f48a861bac2f8a1990a0e9b7e98d` |
| File / quantization | `Qwen3.6-35B-A3B-MXFP4_MOE.gguf`, MXFP4 MoE |
| Model SHA-256 | `e1a4925d2ea132576daa9cb980b1102b970d919d896936b7b6e681ef5bc3d3f6` |
| Host | Fakoli Dark, Windows, Docker Desktop/WSL2 |
| GPU | NVIDIA RTX PRO 6000 Blackwell Max-Q, 97,887 MiB, compute capability 12.0 |
| CUDA | 13.1.2 devel and runtime images, both digest-pinned |
| Build target | `compute_120a`, `sm_120a`, per-thread default stream |
| Baseline context / KV | 32,768 / FP16 |
| Baseline state cache | disabled with `--no-state-cache` |
| Server concurrency | one request at a time on slot 0, per q36's current server docs |
| Endpoint | `http://127.0.0.1:39040/v1` |
| Model storage | external `vllm-hfcache` Docker volume, mounted read-only by the serve |

The multi-stage image copies the q36 executables and the required Ubuntu
`libgomp.so.1` into a digest-pinned CUDA runtime. The source archive checksum
fails closed if GitHub serves different bytes. `anvil-serving models pull`
downloads only the pinned GGUF into the managed data volume; no weight download
occurs during image build or container startup.

CPU parser/dequant validation, the Blackwell block-scaled MMA self-test, image
build, server startup, `/health`, and an OpenAI-compatible chat request all
passed. The service reported engine-ready in 7.9 seconds in its final restored
32K configuration.

## GPU isolation and neighboring workload

GPU 0 was an RTX 5090 with an existing workload using about 31.6 GiB. GPU 1 was
the RTX PRO 6000. Docker Desktop exposed both devices despite the Compose UUID
reservation, so the proven execution boundary was
`CUDA_DEVICE_ORDER=PCI_BUS_ID` with `CUDA_VISIBLE_DEVICES=1`; the Compose file
records both that mask and the PRO 6000 UUID.

The production Heavy container was kept down as requested. Health probes for
the existing 5090 endpoints on ports 30003, 30005, 30006, and 30007 returned
HTTP 200 after testing. The final q36 state was healthy on port 39040 with
32,768 context, FP16 KV, state caching off, and MTP off.

## Allocated-context matrix

Each row recreated the server and sent the same tiny greedy arithmetic request.
The primary evidence is startup/health and VRAM allocation; request throughput
from a seven-token completion is too noisy to compare.

| Allocated context | Engine ready | GPU memory | Health | Answer |
|---:|---:|---:|---|---|
| 8,192 | 13.0 s | 27,060 MiB | 200 | 391, stop |
| 32,768 | 9.0 s | 27,604 MiB | 200 | 391, stop |
| 90,112 | 9.9 s | 28,906 MiB | 200 | 391, stop |
| 262,144 | 10.0 s | 32,710 MiB | 200 | 391, stop |

All four allocations loaded on the PRO 6000. This proves allocation and a
short request, not semantic recall at each full depth. Raw values, including
managed wall time and request timings, are in
[context-matrix.json](2026-07-13-q36-pro6000-container-recipe-evidence/context-matrix.json).

## Native q36 benchmark

The engine's own `q36_bench` ran three repetitions with FP16 KV and no MTP:

| Test | Throughput |
|---|---:|
| Prefill 2,048 | 11,951.6 +/- 384.8 tok/s |
| Prefill 8,192 | 11,273.7 +/- 1.8 tok/s |
| Prefill 32,768 | 9,937.1 +/- 27.9 tok/s |
| Prefill 90,112 | 7,784.6 +/- 54.6 tok/s |
| Generate 128 at depth 0 | 252.7 +/- 0.4 tok/s |
| Generate 128 at depth 32,768 | 217.6 +/- 0.1 tok/s |
| Generate 128 at depth 90,112 | 171.6 +/- 0.2 tok/s |

This is a q36-only synthetic result on the 300 W PRO 6000, not a back-to-back
llama.cpp comparison and not directly comparable to the q36 README's 400 W RTX
5090 numbers. The command and structured output are in
[q36-native-benchmark.json](2026-07-13-q36-pro6000-container-recipe-evidence/q36-native-benchmark.json).

## MTP on/off

MTP used q36's own self-speculative head at draft depth 1 and greedy decoding.
A short arithmetic smoke returned byte-identical `<think>\n\n</think>\n\n391`
responses in both modes. Both finished with `stop`; the MTP log reported 75%
acceptance, but seven generated tokens are not a useful speed sample.

For a 1,024-token reasoning-heavy generation, three repetitions produced:

| Mode | Decode samples | Mean | Relative change |
|---|---|---:|---:|
| MTP off | 251.4, 250.7, 251.9 tok/s | 251.3 tok/s | baseline |
| MTP K=1 | 286.6, 290.2, 289.9 tok/s | 288.9 tok/s | +14.95% |

Single 2,048-token runs measured 249.9 versus 284.2 tok/s (+13.7%); MTP
accepted 93% of draft tokens at 1.93 tokens per verification. Single
4,096-token runs measured 247.9 versus 285.7 tok/s (+15.2%); MTP accepted 94%
at 1.94 tokens per verification.

The cross-mode 2,048- and 4,096-token outputs were not byte-identical even
though q36 documents lossless byte identity. Each mode required a fresh
container, so this experiment does not isolate MTP from cross-restart CUDA
nondeterminism. It is an author-facing follow-up, not proof that MTP alone
changed the token path.

## Reasoning-heavy quality check

The prompt asked for the number of shortest lattice paths from `(0,0)` to
`(10,10)` avoiding `(5,5)`, ending with `FINAL=<integer>`. An independent
combinatorial check gives `C(20,10) - C(10,5)^2 = 121252`; the model did not
grade itself.

Both MTP modes exhausted 1,024, 2,048, and 4,096 completion-token budgets
inside a verbose visible `<think>` trace without emitting `FINAL=`. Therefore
the reasoning gate failed under every tested budget. The responses contained
the correct intermediate values but never satisfied the requested final-answer
contract. Treat this as an output-control/verbosity failure, not a wrong final
integer and not a passed intelligence result.

## Limits and disposition

- One host and one physical PRO 6000 were tested.
- Only MTP depth 1 was characterized; depths 2 and 3 remain future axes.
- The HTTP server is sequential, so no concurrent-serving claim is made.
- State caching and KV quantization were intentionally disabled.
- The 262K row proves allocation plus a short request, not a full-depth needle.
- No independent llama.cpp run or perplexity comparison was performed.
- No routing profile, Heavy recipe, `docs/BENCHMARKS.md` recommendation, or
  production container changed.

The engine is operational and fast enough to justify deeper evaluation, but
the reasoning-budget failure and unresolved long-output byte-identity question
make promotion premature.
