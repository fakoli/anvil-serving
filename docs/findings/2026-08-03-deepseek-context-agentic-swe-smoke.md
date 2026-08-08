# DeepSeek 0731 context, agentic-recovery, and SWE-bench smoke

**Date:** 2026-08-03

**Decision:** benchmark substrate qualified for larger campaigns; no model or
route promotion change

**Evidence classes:** `functional`, bounded `quality`, benchmark-infrastructure
qualification

## Outcome

The new Anvil Serving benchmark job surface successfully ran three remote jobs
from AI-MBP25 against the existing DeepSeek 0731 Primary on Fakoli Dark:

| Lane | Bounded result | Interpretation |
|---|---|---|
| Native context retrieval | 1/1 at the 8,192-token smoke bucket; 6,102 observed prompt tokens; 1.177 s latency; 37.393 completion tok/s | The 8K smoke passed. It does not locate a degradation point or validate the 650K ceiling. |
| Agentic tool-error recovery | Protocol, result incorporation, retry recovery, and history passed; reasoning and final-answer checks failed; score 0/1 | The model made both required tool attempts after the injected error, then failed to complete the answer. This is a retained model-behavior failure. |
| SWE-bench Verified | `django__django-11099` resolved 1/1 under the official grader; agent and grader exited 0 | The end-to-end agent/container/grader path works. One issue is not a representative SWE-bench score. |

The result supports starting a controlled scout campaign. It does not support a
new intelligence ranking, a long-context quality claim above 8K, or a routing
change.

## Exact subject and topology

- Checkpoint: `deepseek-ai/DeepSeek-V4-Flash-0731` at
  `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`
- Served name:
  `deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-tp2-650k`
- Runtime image digest:
  `sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f`
- Serve host: Fakoli Dark, two equivalent RTX PRO 6000 Blackwell Max-Q cards,
  exclusive TP=2 over PCIe without NVLink
- Worker: AI-MBP25, macOS arm64, Docker Desktop
- Published endpoint: `http://100.64.0.10:8000/v1` (generic tailnet placeholder)

The worker did not host the model. It owned harness checkout, Docker grading,
job state, bounded logs, and content-addressed artifacts while the model stayed
on Fakoli Dark.

## Harnesses and integrity

All three jobs used immutable submitted specs and returned digest-bound
artifacts. The public JSON files retain the identifiers and hashes needed to
match this publication to the private worker evidence without publishing
prompts, reasoning traces, credentials, private addresses, or machine-local
paths.

The SWE lane pinned:

- mini-SWE-agent revision
  `a83fcae82d2a08f0ee0c688f9d137b3566c097f8`
- SWE-bench revision
  `f7bbbbfefbb0b9efdcddc5eb568a29d9cace17c9`
- SWE-bench Verified dataset revision
  `c104a568cd32d76b9a41390466a2c865d98f5d6a`

Docker reused a previously available x86 grading image through Docker
Desktop's amd64 emulation. That is acceptable for the one-case infrastructure
smoke, but a larger campaign should record cold-image preparation time and
separate emulation overhead from model latency.

## Operational findings fixed during the smoke

Two CRLF-sensitive dotenv boundaries initially made valid authentication look
incorrect. The controller and benchmark endpoint loaders now normalize dotenv
values before comparing or sending bearer tokens. Regression tests cover both
boundaries.

The managed router reported healthy but was published only on
`127.0.0.1:8000`, so the remote worker could not reach it. The router alone was
recreated through the managed Anvil Serving command with a tailnet-specific
publish address; the model container, route identity, exclusive owner, and
650K recipe were not restarted or changed. The missing published-bind detail
in managed router status is recorded as a product ticket because diagnosing it
required a bounded read-only Docker inspection.

The worker controller also gained safe stage-artifact retrieval by digest and
relative path. That allowed the failure class and grader result to be examined
through Anvil Serving instead of SSH or direct filesystem access.

## Next campaign

Run the scout profile before a deep campaign:

1. Expand native context retrieval across logarithmic buckets, including
   repeated near-limit positions, and plot success, latency, throughput, and
   answer quality by actual token count.
2. Repeat the failing tool-error recovery scenario and add structured-output,
   multi-tool, tool-result-conflict, and long-history recovery cases.
3. Run a fixed, predeclared SWE-bench Verified sample large enough to report a
   confidence interval; retain every attempted instance and official grader
   result.
4. Start the deep profile only if the scout's correctness and infrastructure
   gates pass. Do not promote or change the router from benchmark evidence
   without a separate human decision.

## Public evidence

- [Context smoke](2026-08-03-deepseek-context-agentic-swe-evidence/context-smoke.json)
- [Agentic recovery smoke](2026-08-03-deepseek-context-agentic-swe-evidence/agentic-recovery-smoke.json)
- [SWE-bench Verified smoke](2026-08-03-deepseek-context-agentic-swe-evidence/swe-verified-smoke.json)
