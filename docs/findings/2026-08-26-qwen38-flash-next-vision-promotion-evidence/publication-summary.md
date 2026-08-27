# Publication summary: Qwen3.8 Flash Next vision and 262K result

<!-- benchmark-publication-summary/v1 -->

This is derivative publishing copy. The
[dated finding](../2026-08-26-qwen38-flash-next-vision-promotion.md) and linked
raw artifacts are authoritative.

## Canonical facts

- **Model identity:**
  `RadixArk/Qwen3.8-Flash-Next-NVFP4@7b719225242aacd3dbd3f9407468c2ee9a9d2594`,
  served as `qwen38-flash-next-radixark-nvfp4-sglang-qsa-fast-tp2-262k-mtp3`
- **Runtime identity:** SGLang
  `d91c3682b0b429e4c70df63cd57f819588ce29b0`, image
  `sha256:59f06adce6f91401adf443bd168d45fdb2044d77671fd591c7c57a29d851cbae`
- **Local setup:** two RTX PRO 6000 Blackwell Max-Q cards, TP=2 over
  PCIe/WSL2, QSA-fast, NVFP4 weights, NEXTN `3/1/4`, c1
- **Recipe:** [managed QSA-fast MTP3 recipe at `81bcfc13`](https://github.com/fakoli/anvil-serving/blob/81bcfc133ccfeee1ca10b1542345d5046f5c74f2/configs/qwen38-flash-next-radixark-nvfp4-sglang-sm120-qsa-fast-tp2-262k-mtp3-recipe.toml)
- **Measurement path:** client-observed direct text on one warm running
  service; direct and authenticated live-routed media
- **Contract:** 262,144-token route, 8,192-token output reserve, thinking
  disabled, four images or one video
- **Headline text result:** 155.9 decode tok/s at the 4K target and 114.7 at
  125,447 actual prompt tokens, both p50 of five c1 requests
- **Headline vision result:** direct corpus 30/30; live routed repeats 57/60
  under a strict literal rubric
- **Important caveat:** all three live misses were semantically correct but
  omitted one expected literal word; the result does not qualify c2
- **KV capacity:** 6.275 GiB per rank, 12.55 GiB aggregate, 516,032 server
  tokens; 8,256 tokens short of two full configured windows
- **Decision:** `current`; the existing Primary was human-approved for explicit
  image, OCR, and video routes under the c1 admission contract
- **Canonical evidence:**
  <https://fakoli.github.io/anvil-serving/findings/2026-08-26-qwen38-flash-next-vision-promotion/>

## X / short post

247 literal characters including the URL as checked on 2026-08-27. Recount
immediately before posting.

```text
Local Qwen3.8 Flash Next NVFP4, dual RTX PRO 6000 Max-Q TP=2/c1: 155.9 tok/s at 4K; vision 30/30 direct, 57/60 live routed. 262K route; misses retained. https://fakoli.github.io/anvil-serving/findings/2026-08-26-qwen38-flash-next-vision-promotion/
```

## Reddit

The title is 86 literal characters. Check the target community's current rules
before posting.

```text
Qwen3.8 Flash Next NVFP4 on dual RTX PRO 6000: 262K c1, 155.9 tok/s, vision 57/60 live
```

```markdown
I tested RadixArk Qwen3.8 Flash Next NVFP4 locally on two RTX PRO 6000
Blackwell Max-Q cards using TP=2 over PCIe/WSL2, SGLang QSA-fast, NEXTN 3/1/4,
and concurrency one.

Headline results:

- 4K target: 155.9 decode tok/s and 0.141 s TTFT, p50 of five requests
- 128K target / 125,447 actual prompt tokens: 114.7 decode tok/s, 18.1K
  effective prefill tok/s, and 6.932 s TTFT, p50 of five
- 254K target / 245,000 actual: 112.9 decode tok/s and 27.345 s TTFT, p50
  of two
- Full-reserve proof: 253,703 prompt tokens plus an 8,192-token output request
- Direct vision corpus: 30/30; live routed repeats: 57/60 strict
- Direct image/video/mixed latency p50: 0.636 / 1.236 / 1.147 seconds
- KV pool: 516,032 server tokens, or 6.275 GiB per rank

The three live misses were correct observations that omitted one literal rubric
word. Effective prefill is client-observed and includes scheduling plus
first-token work. This is a bounded local c1 result, not a universal model
ranking or a c2 qualification.

Full methodology, failures, and raw artifacts:
https://fakoli.github.io/anvil-serving/findings/2026-08-26-qwen38-flash-next-vision-promotion/

What throughput or strict vision repeatability do you see on your hardware?
```

## Screenshot alt text

Benchmark result card for RadixArk Qwen3.8 Flash Next NVFP4 on two RTX PRO 6000
Blackwell Max-Q GPUs at TP=2, 262K context, and concurrency one. It reports
155.9 decode tokens per second at the 4K target, 114.7 at 125,447 actual prompt
tokens, direct vision 30 of 30, and live routed vision 57 of 60. A caveat notes
three semantically correct answers missed a required literal rubric word and
the KV pool does not fit two full 262K windows.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| 155.9 decode tok/s at the 4K target | 3,613 actual prompt tokens; c1; p50 of 5 | [Finding sweep](../2026-08-26-qwen38-flash-next-vision-promotion.md#context-and-throughput-sweep) · [`capacity-4096-c1.json`](capacity-4096-c1.json) |
| 114.7 decode and 18.1K effective prefill tok/s at 125,447 actual tokens | 128K target; c1; p50 of 5 | [Finding sweep](../2026-08-26-qwen38-flash-next-vision-promotion.md#context-and-throughput-sweep) · [`capacity-131072-c1.json`](capacity-131072-c1.json) |
| 112.9 decode tok/s at 245,000 actual tokens | 254K target; c1; p50 of 2 | [Finding sweep](../2026-08-26-qwen38-flash-next-vision-promotion.md#context-and-throughput-sweep) · [`capacity-253952-c1.json`](capacity-253952-c1.json) |
| 253,703 prompt plus 8,192 requested output | separate full-reserve capacity gate; c1 | [Finding reserve boundary](../2026-08-26-qwen38-flash-next-vision-promotion.md#context-and-throughput-sweep) · [`summary.json`](summary.json) |
| Direct vision 30/30 | 15-case corpus repeated twice; strict rubric | [Finding vision corpus](../2026-08-26-qwen38-flash-next-vision-promotion.md#vision-corpus) · [`direct-multimodal.json`](direct-multimodal.json) |
| Live routed vision 57/60 with three retained literal-rubric misses | two 30-attempt live repeats; c1 route | [Finding vision corpus](../2026-08-26-qwen38-flash-next-vision-promotion.md#vision-corpus) · [`live-routed-multimodal.json`](live-routed-multimodal.json) · [`live-routed-multimodal-r2.json`](live-routed-multimodal-r2.json) |
| 516,032-token KV pool and c1-only qualification | 6.275 GiB per rank; 12.55 GiB aggregate | [Finding configuration](../2026-08-26-qwen38-flash-next-vision-promotion.md#reproducible-configuration) · [`summary.json`](summary.json) |

## Reusable result block

- 4K: 155.9 decode tok/s, 25.6K effective prefill tok/s, 0.141 s TTFT
- 128K target / 125,447 actual: 114.7 decode tok/s, 18.1K effective prefill
  tok/s, 6.932 s TTFT
- 254K target / 245,000 actual: 112.9 decode tok/s, 8.94K effective prefill
  tok/s, 27.345 s TTFT
- Full reserve: 253,703 prompt plus 8,192 output requested, 102.0 decode tok/s
- Direct vision corpus: 30/30; live routed repeats: 57/60 strict
- Image/video/mixed latency p50: 0.636 / 1.236 / 1.147 seconds
- Router edge suite: 8/8; four images or one video, fail closed
- KV: 6.275 GiB per rank, 12.55 GiB aggregate, 516,032 server tokens

Effective prefill includes scheduling and first-token work. All throughput
figures are local c1 measurements, not vendor claims.
