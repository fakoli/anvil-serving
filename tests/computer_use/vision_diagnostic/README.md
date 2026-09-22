# Computer-use vision diagnostic corpus

`corpus.json` is a frozen `multimodal-corpus/v1` workload of twelve synthetic,
single-image cases. Each asset is a 640 by 360 RGB PNG drawn locally from
Pillow primitives and DejaVu Sans text, contains no downloaded or user data,
and is released under CC0-1.0. Its SHA-256 is pinned in the manifest.

The cases are independently labelled in the manifest prompts and expected
checks: dense small text/numbers, near-duplicate toolbar icons, disabled or
error appearance, charts and color meaning, missing or partly occluded widgets,
and untrusted image instructions. Each category has exactly two cases. The
adversarial images explicitly demand the incorrect sentinels `BANANA` or
`ORANGE`; prompts instead request bounded transcription of the depicted facts.

The native schema has no field for category labels, negative visual assertions,
or an assertion that image instructions were ignored. Stable case IDs encode
the category; the existing `contains_casefold` and `ordered_casefold` checks
are used for the labelable outputs. Consequently the adversarial checks are
provisional: the native evaluator accepts contradictory or extra output, so a
stronger evaluator is required before any live robustness qualification. This
fixture does not claim a measured result.

Run the offline validation with:

```sh
python3 scripts/run_tests.py tests/computer_use/vision_diagnostic/test_dry_run.py tests/test_multimodal_benchmark.py -q
```
