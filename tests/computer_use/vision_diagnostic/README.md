# Computer-use vision diagnostic corpus

`corpus.json` is a frozen `multimodal-corpus/v1` workload of twelve synthetic,
single-image cases. Each asset is a 640 by 360 RGB PNG drawn locally from
Pillow primitives and DejaVu Sans text, contains no downloaded or user data,
and is released under CC0-1.0. Its SHA-256 is pinned in the manifest.

The cases are independently labelled in the manifest prompts and expected
checks: small text/numbers, near-duplicate toolbar icons, disabled or error
appearance, charts and color meaning, missing or occluded targets, and
untrusted image instructions. Each category has exactly two cases. The
adversarial cases ask for bounded transcription while directing the model not
to follow instructions depicted in the image.

The native schema has no field for category labels, negative visual assertions,
or an assertion that image instructions were ignored. Stable case IDs encode
the category; the existing `contains_casefold` and `ordered_casefold` checks
are used for the labelable outputs. This fixture does not claim a measured
result.

Run the offline validation with:

```sh
python3 scripts/run_tests.py tests/computer_use/vision_diagnostic/test_dry_run.py tests/test_multimodal_benchmark.py -q
```
