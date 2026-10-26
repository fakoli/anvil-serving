# Publication summary: Qwen3.8 Flash Next three-recipe scout

<!-- benchmark-publication-summary/v1 -->

This is final derivative public copy. The [dated finding](../2026-10-26-flash-next-engine-qualification.md), [evidence index](README.md), and linked native artifacts are authoritative. The campaign closed with no promotion after exact local route/cap restoration and GLM incumbent readmission.

## Canonical facts

- **Model identity:** Qwen3.8 Flash Next across three pinned repositories/revisions: Ignis Trellis `gpillon/Qwen3.8-Flash-Next-trellis-a25-ignis@8d94db83cd8e58d6be31e796723e5e35b960638c`; vLLM QAD `local-inference-lab/Qwen3.8-Flash-Next-NVFP4-MXFP8-CSF-QAD@dc5b8ab2fa14bc4fecd6886273b7ebc6095662e2`; Flare `local-inference-lab/Qwen3.8-Flash-Next-FLARE@431b302155d53e296aa51633a997b4b5fd92e751`.
- **Runtime identity:** Ignis 0.8.0; vLLM observed build `v0.1.dev21836+g6d66eed35` with full source revision unknown; Flare source `2193455651794642debdaad0dda91e77ab675031`.
- **Local setup:** one RTX PRO 6000 Blackwell Max-Q per serialized run; each quality/context request used C1. Recipe-configured concurrency was Ignis C8, vLLM C16, Flare C16. MTP was 3 tokens for Ignis/vLLM and 5 for Flare. Quantization and KV details are in the [finding configuration table](../2026-10-26-flash-next-engine-qualification.md#exact-configuration).
- **Recipe:** distinct managed Ignis, vLLM and Flare recipes; these are not engine-only variants.
- **Measurement path:** managed local endpoints; one-request agentic, Aider, SWE, context and bounded serving scouts. No controlled speed population.
- **Headline result:** each recipe passed 6/6 agentic cells and 9/9 context samples (maximum actual prompt 114,588 tokens). Aider/SWE outcomes were mixed. vLLM and Flare C1 each had 0/8 performance-eligible requests.
- **Important caveat:** shared-router whitespace handling may affect historical Aider outputs; original HTTP bodies are unavailable, so historical causality is unknown. Flare C1 strict output failed on all eight requests. vLLM C1 also failed all eight requests. Exact prior route/output-cap configuration and GLM incumbent readmission passed local checks; the router uses a reviewed PR #704 overlay rather than the original image.
- **Decision:** `no-promotion`; no primary promotion. This supports bounded recipe outcomes, not engine causality, a speed ranking, full-window capacity, or promotion.
- **Canonical evidence:** [dated finding](https://fakoli.github.io/anvil-serving/findings/2026-10-26-flash-next-engine-qualification/)
- **Artifact set:** [final manifest](artifact-manifest.json) and [evidence index](README.md); workload-manifest and frozen run-plan roles are missing; restoration evidence is retained.

## X / short post

```text
Local Qwen3.8 Flash Next: 3 recipes, one RTX PRO 6000. Agentic/context: 6/6 and 9/9 each; max prompt 114,588. Mixed coding; vLLM/Flare C1 0/8 eligible. No promotion. https://fakoli.github.io/anvil-serving/findings/2026-10-26-flash-next-engine-qualification/
```

## Reddit

```text
Qwen3.8 Flash Next local scout: three recipe variants, bounded results
```

```markdown
I ran bounded local functional and context checks for three Qwen3.8 Flash Next recipe variants on one RTX PRO 6000 Blackwell Max-Q at a time. The restored router uses a reviewed PR #704 overlay, not the original image.

- Each recipe passed 6/6 agentic cells and 9/9 context samples.
- The largest observed prompt was 114,588 tokens; advertised context was not tested at its full limit.
- Coding outcomes were mixed. vLLM and Flare C1 each had 0/8 performance-eligible requests.

These are bounded recipe outcomes, not an engine-only comparison or speed ranking. Historical Aider attribution remains uncertain because original HTTP bodies were not retained and a shared-router whitespace issue was confirmed. No model was promoted; local route/output-cap restoration and GLM readmission passed. This does not establish whole-fleet, reboot, or fresh-host acceptance.

Full methodology, failures and evidence: https://fakoli.github.io/anvil-serving/findings/2026-10-26-flash-next-engine-qualification/
```

## Screenshot alt text

Final result card for three Qwen3.8 Flash Next recipe variants tested serially on one RTX PRO 6000 Blackwell Max-Q. Each passed six agentic cells and nine bounded context samples, with maximum actual prompt 114,588 tokens. Coding results were mixed, and vLLM and Flare C1 had no performance-eligible requests. The card states that engine ranking, speed, promotion and whole-fleet, reboot or fresh-host acceptance are not established; local direct/routed restoration checks passed.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Agentic 6/6 and context 9/9 for each recipe | Three distinct recipes; context maximum actual prompt 114,588 | [finding](../2026-10-26-flash-next-engine-qualification.md#results) · native agentic/context results linked from [evidence index](README.md) |
| vLLM and Flare C1 each had 0/8 performance-eligible requests | Eight responses per native C1 artifact; no timing population | [Flare C1 native result](flare-serving-c1-native-capacity-result.json) · [vLLM C1 native result](vllm-serving-c1-native-capacity-result.json) |
| No engine causality, speed ranking or promotion | Distinct recipe configurations; local restoration passed with a router runtime image difference | [finding](../2026-10-26-flash-next-engine-qualification.md#evidence-boundary) · [final restoration record](restoration.json) |
