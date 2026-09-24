# Mixed long-context and vision gate is unavailable

**Date:** 2026-09-24

**Status:** open product gap

**Scope:** bounded qualification tooling

The managed evaluation surface has no request type that combines a calibrated long text context with one or two fixed hashed images and evaluates both the text needle and image/OCR result independently. Text context and small-image evidence must remain separate gates.

Evidence from the current CLI implementation:

- `eval preflight --checks image,ocr` accepts `--image-path` and expectations, but `preflight.t_multimodal` constructs fixed image/OCR instructions; it has no caller-provided long text or needle contract.
- `eval benchmark multimodal` accepts a hashed `multimodal-corpus/v1`, but each case prompt is limited to 8,192 characters in `benchmarking/multimodal.py`. It records endpoint usage only after a request, so it cannot establish a planned approximately-120K combined input.
- `eval benchmark context` creates and scores calibrated native text cases, then sends a user message whose content is text only in `benchmarking/suite_runner.py`.
- `eval benchmark quality --suite-file` accepts only string message content in `benchmarking/specs.py`; it has no image content-part schema.
- The existing fixed public images used for image/OCR qualification are 640 by 360 pixels. Their small-image results do not prove the combined near-limit envelope.

No raw model-calling script or grading override was used. The blocked validation is a tooling limitation, not a model pass or failure.

## Acceptance criteria

Provide a managed, bounded composite context-and-vision suite that:

1. builds a calibrated text context to a declared token target and leaves explicit output headroom;
2. attaches one or two manifest-hashed image inputs to the same OpenAI-compatible request;
3. evaluates an independent exact text-needle assertion and image/OCR assertions;
4. records actual endpoint usage tokens, including the service's combined-input accounting, finish reason, reasoning evidence, request controls, and per-attempt failure classification;
5. bounds concurrency, images per request, output tokens, and request timeout; and
6. emits a durable artifact usable by the benchmark evidence workflow.

Until that exists, report long-context and vision gates separately and do not claim a combined near-limit text-and-image qualification.
