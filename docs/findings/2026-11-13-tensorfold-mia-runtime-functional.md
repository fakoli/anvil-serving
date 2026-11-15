# Mia TensorFold runtime: functional validation — 2026-11-13

The pinned Mia update fixes a reproduced truncated-tool failure and expands usable prompt scoring. It is accepted as a same-model runtime candidate after direct regression checks and a verified retained-runtime rollback rehearsal. Operational promotion and fleet closure are tracked separately; this finding establishes no speed improvement.

[Evidence index](2026-11-13-tensorfold-mia-runtime-functional-evidence/README.md) · [Decision summary](2026-11-13-tensorfold-mia-runtime-functional-evidence/summary.json) · [Hashed artifact manifest](2026-11-13-tensorfold-mia-runtime-functional-evidence/artifact-manifest.json).

## What changed

Mia recipe [`fc1ee3a`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-2x-RTX-PRO-6000-TensorFold/tree/fc1ee3a5381031e2357662b46c2fb44b20190429) applies 110 author patches to TensorFold `f350cd3e`. The six owned telemetry patches produce tree `109491764fc5f711705c148da278a708f6c1aeea`; the ordered portable catalog is in [PR 755](https://github.com/fakoli/anvil-serving/pull/755). Four owned patches are unchanged, and two were rebased around checkpoint identity fields. Authorship and notices remain intact.

The additions cover tool-envelope guards, truncated JSON tool calls and Messages conversion, additional EOS handling, verification windows up to32, actual-KV-pool reply caps, prompt scoring up to128 alternatives with requested token IDs, and build identity. Prefill returns to2048 rows. L2 prefetch and thinking-off splitting remain disabled.

## Matched functional observations

The same weights and DFlash2 draft ran on two RTX PRO 6000 Blackwell Max-Q96GiB cards at300W, with TP2, EXL3 4-bpw, FP8 sharded KV, configured327680 context,32768 output,C4 and image8. Container memory was bounded at48GiB with zero effective swap and a16GiB host reserve. Separate conversation-cache volumes prevented cross-build cache reuse.

| Request or gate | Previous runtime | Updated runtime | Comparison |
|---|---|---|---|
| Forced write_file call cut at64 output tokens, nonstreamed Messages API | HTTP500, unterminated argument string | HTTP200, parsed partial input and max_tokens stop reason | matched request; concrete reliability fix |
| Prompt logprobs128 | HTTP400, cap20 |128 accepted;129 refused | matched request; added capability |
| Requested IDs outside the top128 | unsupported in prior contract | both returned with distinct finite scores/ranks | partially matched; candidate-only scoring checks |
| Repeated and concurrent scoring | not collected | exact repeat; two scoring plus one plain request pass | candidate-only regression |
| OpenAI whole/stream and Messages stream truncated calls | valid cut-call response | valid cut-call response | matched structural assertions |
| Tools, tool results, streaming, JSON, image | retained prior qualification | native regression passes, including20 tool cases | regression, not a new broad quality score |

The synthetic tool was never executed. Tests verify transport and argument handling, not completion of a1000-number file. The requested-ID control catches reuse of the first requested ID's score. Numerical agreement with a full-precision reference was not measured.

## Context, concurrency and recovery

Four concurrent exact canaries passed. The telemetry window retained1164 coherent samples, observed four active streams and no cgroup memory events; minimum observed host-available memory was68.04GiB. This does not prove four simultaneous full327680-token windows.

Exact retrieval passed at19456 and99284 prompt tokens. The first99K request already reused18432 tokens, so it is not a fully cold measurement. Warm requests reused18432/98304 tokens; after managed recreation, the same-volume first retrievals reused those prefixes and returned correct values. A384-entry structured map also passed.

The previous container was restored with its exact image and identity, monitoring was rebound to its new start time, and authenticated routed RESTORE_OK passed. The later delta window reused the accepted candidate. [Restoration evidence](2026-11-13-tensorfold-mia-runtime-functional-evidence/restoration.json) distinguishes this rehearsal from the isolated candidate state recorded when the qualification packet was finalized.

## Freshness and boundaries

Mia main was rechecked on November13 and remained `fc1ee3a`. The open [video assembly PR](https://github.com/MiaAI-Lab/GLM-5.3-Flash-2x-RTX-PRO-6000-TensorFold/pull/3) reports results on a different Max-Q/CPU setup; it needs a separate WITH_VIDEO=1 image and remains outside this text/image route. Video is not advertised. External performance figures are priors, not local measurements.

Source validation passed1049 upstream host tests,60 telemetry tests and4548 product tests (116 skipped,20 subtests); all13 PR checks passed. The artifact set retains the baseline failures and exact request responses. Public copies replace private paths, topology and ephemeral identities with synthetic values.

Tool-guard history smoke does not guarantee rejection-path activation; host parser tests cover that path. No speed ranking, endurance, reboot, fresh-host rebuild, or universal quality claim follows. Full rollout closure remains `harness_convergence_pending` while the fleet's replacement workflow owner is unavailable; legacy writers stay retired.
