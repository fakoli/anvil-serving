# Two independent WER implementations can silently diverge between voice benchmarks

**Status:** Open

## Problem

The repository carries two unrelated Word-Error-Rate implementations:

- `anvil_serving/voice/stt_benchmark.py` — `error_counts()` /
  `normalized_words()` pair local to that file.
- `anvil_serving/voice/benchmark.py:58-83` — its own regex tokenizer
  (`\w+(?:['’]\w+)*`, casefold) and its own Levenshtein DP, sharing no code
  with the first.

Two WER definitions can silently disagree on edge cases (punctuation,
apostrophes, empty-string handling), so the STT-stage benchmark and the
voice-pipeline benchmark may publish non-comparable "WER" numbers for the
same audio.

## Resolution options

Either consolidate into one shared helper (e.g. `voice/_wer.py`) or, at
minimum, add a regression test asserting both implementations return the same
counts on a fixed corpus that covers punctuation, apostrophes, casing, and
empty strings. Not merged during the 2026-08-07 production cleanup because
the algorithms are non-trivial and the safe merge needs its own A/B of
published numbers.
