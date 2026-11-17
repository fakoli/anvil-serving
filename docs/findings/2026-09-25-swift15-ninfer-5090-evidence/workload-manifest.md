# Swift-1.5 post-promotion workload manifest

The [run plan](run-plan.md) fixed the launch profile before promotion. These
workloads ran only after the selected route was live; the earlier direct
preflight was a correctness gate.

| Workload | Selection and repetitions | Controls and independent score |
|---|---|---|
| Direct protocol and near-limit retrieval | Smoke, JSON, tools, streaming, continuation, Responses, image/OCR, four-request tools, and a near-limit needle | Native preflight validators; exact marker with actual prompt count and output reserve |
| Capacity | 4K C1/C4: 100 each; 32K C1/C4: 30 each; 128K C1/C4: 10 each; near-limit C1: 3 | Unique-cache request canaries, temperature 0, 256 target words, 512 maximum completion tokens. Default-thinking strict and no-thinking strict failures retained; no-thinking `observe` cells report exact-output adherence and descriptive rates. |
| Native chat/tool/session/intelligence | Three tool, three session, six deterministic intelligence checks | Native validators and default thinking; small diagnostic population |
| Routed context | Six cases at each 32,768, 131,072, and 258,048 requested-token bucket | Three needle positions, two repeats each, 4,096 output reserve; independent exact-marker match and actual prompt tokens |
| Routed agentic | 18 frozen `scout` cases | Native tool/protocol validator; default thinking; failure retained even if final prose is correct |
| Routed SWE-bench Verified | Frozen IDs `django__django-11099`, `pytest-dev__pytest-10051`, `scikit-learn__scikit-learn-10297`, `psf__requests-1142`, `sympy__sympy-11618` | One mini-SWE-agent worker, default thinking, pinned assets and official SWE-bench grader; five completed and resolved IDs required |
| Image/OCR | Twelve cases in [`tests/computer_use/vision_diagnostic/corpus.json`](https://github.com/fakoli/anvil-serving/blob/main/tests/computer_use/vision_diagnostic/corpus.json), SHA-256 `d58dce55d29cf687678a12112d1aa51d8af2e8e93190ea8a8a3d84ecbb08d76c` | Pinned repository fixture images and native computer-use scorer; no synthetic image generation |

The SWE dataset is `princeton-nlp/SWE-bench_Verified`, split `test`, pinned
dataset revision `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`;
mini-SWE-agent revision `a83fcae82d2a08f0ee0c688f9d137b3566c097f8`;
official SWE-bench revision `f7bbbb2ccdf479001d6467c9e34af59e44a840f9`.
See the upstream projects for dataset and harness licenses. This bundle
publishes scored metadata and hashes, not copyrighted issue contents.
