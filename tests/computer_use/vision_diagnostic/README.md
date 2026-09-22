# Computer-use vision diagnostic corpus

`corpus.json` is a frozen `multimodal-corpus/v1` workload of twelve synthetic,
single-image cases. Each asset is a 640 by 360 RGB PNG drawn locally from
Pillow primitives and DejaVu Sans text, contains no downloaded or user data,
and is released under CC0-1.0. Its SHA-256 is pinned in the manifest.

The cases are independently labelled in the manifest prompts and frozen
whole-answer checks: dense small text/numbers, near-duplicate toolbar icons,
disabled or error appearance, charts and color meaning, missing or partly
occluded widgets, and untrusted image instructions. Each category has exactly
two cases. Prompts request a compact field order but never include the expected
literal answer. The adversarial images explicitly demand the incorrect
sentinels `BANANA` or `ORANGE`; prompts instead request bounded transcription
of the depicted facts.

Stable case IDs encode the category. Every case uses the native
`equals_normalized_casefold` assertion: it casefolds and collapses whitespace
across the complete answer, while punctuation and extra prose remain
significant. The offline proof checks that appended contradiction text and the
two image-requested sentinels fail. This is deterministic diagnostic-format
compliance, not proof of visual perception, grounding, or prompt-injection
robustness. Retained responses need human review, and the separate forty
human-authored qualification checks remain unmet. This fixture does not claim a
measured result.

Run the offline validation with:

```sh
python3 scripts/run_tests.py tests/computer_use/vision_diagnostic/test_dry_run.py tests/test_multimodal_benchmark.py -q
```
