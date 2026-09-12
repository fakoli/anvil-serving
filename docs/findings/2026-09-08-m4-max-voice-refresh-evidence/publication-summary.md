<!-- benchmark-publication-summary/v1 -->
# Publication summary: M4 Max voice refresh

## Canonical facts

- Local Apple M4 Max 48 GB evidence only; it does not describe a live route.
- Qwen3.5-9B passed six functional preflight groups, but strict spoken quality
  was 33/36, patch-format diagnostic quality was 0/3, and strict controlled
  output was 0/10. It has no performance-eligible headline.
- The baseline synthesized Parakeet/Kokoro round trip measured WER 0.0 and
  1896.72 ms. The isolated Kokoro 0.8.2 candidate measured WER 0.0 and
  2139.31 ms; each is one sample, not corpus or subjective-quality evidence.
- Qwen3.8 preliminary preflight passed 5/6 groups; shared-prefix tools passed
  1/3 and the candidate was too slow for the voice goal. No capacity run advanced.
- No LLM candidate was promoted; the Qwen3 4B LLM configuration remains
  unchanged. Kokoro FastAPI 0.8.2 was separately deployed after bounded
  endpoint and Realtime acceptance checks.

## Copy-ready posts

**X / short post (232 characters):** Local M4 Max: Qwen3.5-9B passed preflight but failed strict spoken, format, and output gates. No LLM promotion. Kokoro 0.8.2 passed bounded TTS smokes. https://fakoli.github.io/anvil-serving/findings/2026-09-08-m4-max-voice-refresh/

**Reddit title:** Local M4 Max voice refresh: 9B functional pass, strict output gate failure, no promotion

**Reddit body:** A local Apple M4 Max 48 GB review found Qwen3.5-9B passed six
functional preflight groups but missed the strict spoken suite (33/36),
patch-format diagnostic quality (0/3), and strict controlled output (0/10).
The controlled-output failures make performance claims ineligible. One
synthesized Parakeet/Kokoro round trip had WER 0.0 in 1896.72 ms, which is not
a corpus result. Kokoro 0.8.2 was separately deployed as a local TTS runtime
update; no LLM route changed. Full evidence is linked from the dated finding.

## Screenshot alt text

Result card for a local Apple M4 Max voice-lane review. Qwen3.5-9B passes six
functional preflight groups but fails strict spoken quality at 33 of 36,
patch-format at 0 of 3, and strict controlled output at 0 of 10. One
synthesized STT/TTS round trip has WER 0.0 in 1896.72 milliseconds. No LLM
promotion; Kokoro 0.8.2 was separately deployed after bounded acceptance.

## Claim ledger

| Claim | Evidence |
| --- | --- |
| 9B functional preflight 6/6 | [finding: Functional gates](../2026-09-08-m4-max-voice-refresh.md#functional-gates) · [preflight summary](sanitized-preflight.json) |
| 33/36 spoken, patchformat 0/3, capacity 0/10 | [finding: Negative results](../2026-09-08-m4-max-voice-refresh.md#negative-results-retained) · [quality/capacity summary](sanitized-quality-and-capacity.json) |
| One-turn WER and latency | [finding: Voice path](../2026-09-08-m4-max-voice-refresh.md#voice-path-and-realtime-protocol) · [voice summary](sanitized-voice-summary.json) |
| No promotion | [finding: Decision](../2026-09-08-m4-max-voice-refresh.md#decision-and-promotion-boundary) · [summary](summary.json) |
