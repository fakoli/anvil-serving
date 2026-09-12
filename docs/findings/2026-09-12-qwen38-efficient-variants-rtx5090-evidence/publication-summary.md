# Publication summary: four efficient Qwen3.8 variants on RTX 5090

<!-- benchmark-publication-summary/v1 -->

Derivative publishing copy only; nothing has been posted externally. The
[dated finding](../2026-09-12-qwen38-efficient-variants-rtx5090.md) and native
artifacts remain authoritative.

## Canonical facts

- **Model identity:** Signal27B, Swift27B, Qwopus Flash27B and Minitron20B,
  exact repositories/revisions/files in [recipes](recipes.toml); compare the
  retained Unsloth Qwen3.8-27B GGUF revision `4ca720788d1e01f1bff70c033e0d0028fd02e502`.
- **Runtime identity:** llama.cpp `a298422da78eb75e440a7de0ca408af64d323d93`,
  image digest `sha256:cf2e30bc855cf58cdbdc65d05b5b5e02afa95fb788343a5334d704367ac5c9ac`.
- **Local setup:** one RTX5090,32607MiB, sm120, driver616.64, Windows/Docker/WSL2;
  challenger Q6_K/Q4_0 KV,64K/C1; incumbent UD-Q4_K_XL/MTP3,262K/C1.
- **Recipe:** [original four](recipes.toml), [no-spec controls](nospec-controls.toml).
- **Measurement path:** direct online loopback. Cache state differs by
  diagnostic; no matched finalist performance ranking is claimed.
- **Headline result:** all four original candidates passed six functional
  checks, tools20/20 each, and47349 actual prompt-token retrieval.
- **Capability result:** ten-question strict thinking-on screen: Signal9/10,
  Swift9/10, Qwopus1/10, Minitron3/10, incumbent8/10. N1 per question;
  5120 combined completion-token cap. This includes format and budget failures.
- **Important caveat:** failed128-word output and missing full-context/broad
  quality/client qualification prevent replacement. Small scores are not broad accuracy.
- **Decision:** retain incumbent; no challenger promotion; exact restoration
  passed. User authority was present, but evidence gates were not cleared.
- **Canonical evidence:** [finding](https://fakoli.github.io/anvil-serving/findings/2026-09-12-qwen38-efficient-variants-rtx5090/).
- **Artifact set:** [manifest](artifact-manifest.json), [index](README.md),
  [summary](summary.json), [restoration](restoration.json).

## X / short post

```text
RTX 5090: four Qwen3.8 variants passed tools but none cleared replacement gates. Signal/Swift scored 9/10 on a tiny strict reasoning screen. No promotion. https://fakoli.github.io/anvil-serving/findings/2026-09-12-qwen38-efficient-variants-rtx5090/
```

## Reddit

```text
Four Qwen3.8 variants on one RTX 5090: promising shorter reasoning, but no qualified replacement
```

```markdown
I tested Signal, Swift, Qwopus Flash and Minitron on one RTX 5090 using pinned
llama.cpp Q6_K recipes at 64K/C1, including no-spec controls for Signal/Swift.
All four passed the six-check functional gate and tools20/20 each.

On a ten-question strict thinking-enabled sanity fixture, Signal/Swift passed
9/10, versus8/10 for the retained Unsloth UD-Q4_K_XL/MTP3 incumbent. They used
about16% fewer completion tokens. Qwopus and Minitron scored1/10 and3/10;
format and budget failures count, so these are not broad knowledge scores.

No tested profile cleared replacement gates. Exact128-word output failed for
Signal/Swift with and without speculation; revised shorter checks do not erase
that result. The incumbent also failed the original128-word check. The
challengers did not qualify its262K deployment or broader client/quality gates.
I restored the exact incumbent and retained failed attempts. This is a local
diagnostic result, not a universal ranking.

Method, exact recipes and raw evidence:
https://fakoli.github.io/anvil-serving/findings/2026-09-12-qwen38-efficient-variants-rtx5090/
```

## Screenshot alt text

Table of four Qwen3.8 variants tested on a single RTX 5090 with pinned64K/C1
Q6_K recipes. All pass functional tools. Tiny strict thinking-on scores are
9/10 for Signal and Swift,1/10 for Qwopus and3/10 for Minitron, compared with
8/10 for the262K incumbent. Output failures and missing full-contract gates
prevent promotion; the incumbent was restored.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Four functional passes | Six checks and tools20/20 each;47349 actual input tokens | [Signal](signal-preflight.json), [Swift](swift-preflight.json), [Qwopus](qwopus-preflight.json), [Minitron](minitron-preflight.json) |
| Strict thinking-on scores | Ten questions,N1,5120 combined cap; exact syntax and budget failures included | [Signal](signal-mmlu-thinking.json), [Swift](swift-mmlu-thinking.json), [Qwopus](qwopus-mmlu-thinking.json), [Minitron](minitron-mmlu-thinking.json), [incumbent](incumbent-mmlu-thinking.json) |
| About16% fewer completion tokens | Total across fixed ten attempts, not isolated reasoning tokens | Same five native quality artifacts; [finding results](../2026-09-12-qwen38-efficient-variants-rtx5090.md#results) |
| No qualified replacement | Original strict failures, tiny diagnostic population and missing full deployment gates | [Signal failure](signal-capacity-strict.json), [Swift failure](swift-capacity-strict.json), [summary](summary.json) |
| Exact incumbent restored | Healthy identity, direct functional check, ownership, protected process and shared-memory checks | [Restoration](restoration.json), [preflight](incumbent-restored-preflight.json), [state](restored-state.json) |
