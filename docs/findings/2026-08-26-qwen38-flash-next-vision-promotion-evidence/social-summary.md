# Shareable Qwen3.8 Flash Next result block

Local result on 2x RTX PRO 6000 Blackwell Max-Q, TP=2 over PCIe/WSL2:

- RadixArk Qwen3.8 Flash Next NVFP4, SGLang QSA-fast, MTP3, c1, 262K
- 4K: 155.9 decode tok/s, 25.6K effective prefill tok/s, 0.141 s TTFT
- 128K target / 125,447 actual: 114.7 decode tok/s, 18.1K effective prefill tok/s, 6.932 s TTFT
- 254K target / 245,000 actual: 112.9 decode tok/s, 8.94K effective prefill tok/s, 27.345 s TTFT
- Full reserve: 253,703 prompt + 8,192 output request, 102.0 decode tok/s
- Direct vision corpus: 30/30; live routed repeats: 57/60 strict
- The three live misses were correct observations that omitted one literal rubric word
- Image/video/mixed latency p50: 0.636 / 1.236 / 1.147 s
- Router edge suite: 8/8; four images or one video, fail closed
- KV: 6.275 GiB/rank, 12.55 GiB aggregate, 516,032 server tokens

Effective prefill includes scheduling and first-token work. All throughput
figures are local c1 measurements, not vendor claims. Full methodology and raw
artifacts: `docs/findings/2026-08-26-qwen38-flash-next-vision-promotion.md`.
