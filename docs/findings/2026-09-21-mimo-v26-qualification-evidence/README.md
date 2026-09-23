# MiMo v2.6 Flash qualification evidence

Final evidence for the 2026-09-21 GLM baseline and MiMo v2.6 Flash candidate campaign.

**Campaign state:** completed, candidate unqualified. **Promotion:** not authorized or performed. MiMo V3 passed weight loading and transport health, but failed two correctness probes. GLM is restored.

## Retained baseline evidence

- [GLM preflight](glm-preflight.json): baseline thinking-disabled diagnostic; retained failure.
- [GLM reasoning-enabled preflight](glm-preflight-enabled.json): six checks passed.
- [GLM quality diagnostic](glm-quality.json): repeated diagnostic passes; requested reasoning control is declared `requested_unverified`, not independently bound control evidence.
- [128-word C1 scout](glm-capacity-c1-scout.json): all eight requests reached the 10,240-token limit, repeated `code`, and had truncated validation capture. The strict controlled-output/canary gate failed; timing is performance-ineligible.
- [16-word C1 probe](glm-capacity-16word-probe.json): 1/1 passed.
- [16-word C4 scout](glm-capacity-c4-16word-scout.json): 8/8 passed.
- [16-word C4 final](glm-capacity-c4-16word-final.json): 100/100 passed and performance-eligible. Its [derived metric summary](glm-capacity-c4-16word-final-summary.json) reads exact values directly from the native artifact. The 100-sample p99 values remain a single observed order statistic, not a stable service-level tail claim. Prompts are unique within the final cell, but its first eight requests may have reused the prior scout's cache state; engine cache-hit counters were not returned. A candidate comparison must reproduce the scout/final sequence.

## Candidate inputs, not results

- [Pinned upstream configuration](mimo-config.json) and [generation configuration](mimo-generation_config.json).
- [External-prior source registry](source-registry.json) and [research synthesis](research-synthesis.md).
- [Public recipe reconstruction](mimo-v26-sglang-tp2-c1-327k.public.toml), [pinned image identity](image-identity.json), and [launcher identity](launcher.json).

The original recipe's operator cache location, listener details, and containment path are intentionally absent. The reconstruction preserves the model revision, image digest, engine revision, TP, context, concurrency, memory limits, parser choices, and `--trust-remote-code` requirement, but is not an executable operator recipe.

## Retained MiMo startup failures

- [First launch log](mimo-c1-logs.log) and [status](mimo-c1-status.log): SGLang rejected the legacy ambiguous `--cuda-graph-max-bs` option. The container exited with status 2 and no host-cgroup OOM kill.
- [Corrected-v2 log](mimo-v2-logs.log) and [status](mimo-v2-status.json): containment passed; the corrected attempt used only `--cuda-graph-max-bs-decode 1`.
- [Recovered tool transcript](mimo-v2-tool-transcript.txt) and [capture provenance](mimo-v2-capture-provenance.json): the engine reached mixed-MXFP4 detection, then failed in `AudioProjection` at `mimo_audio.py:947` with `torch.OutOfMemoryError` before weight load or KV allocation. The traceback retains the 128.00 MiB allocation request and reported 93.84 GiB PyTorch allocation. It is a recovered stderr excerpt from the campaign tool transcript, not a complete container log.

Neither V1/V2 failure is a model quality, capacity, or performance result. V3 addressed the load-pressure hypothesis but failed correctness.

## Retained MiMo V3 correctness failure

V3 with `flashinfer_mxfp4` completed weight loading at 81.41 GiB per rank, but default SWA ratio 0.8 left only 91,342 full-KV tokens, below the requested 327,680 context. The matched temperature-zero smoke and official temperature 1/top-p 0.95 sampling diagnostic both exhausted 9,216 tokens as reasoning with zero visible content. The second retained incoherent reasoning output; no capacity benchmark or MiMo throughput claim is valid. See [startup](mimo-v3-startup.log), [models](mimo-v3-models.json), [partial preflight](mimo-v3-preflight.log), [sampling diagnostic](mimo-v3-sampling-smoke.json), and [combined capture](mimo-v3-final-logs.log).

## SWE smoke

The official native summary is 0/1 resolved. This is not attributed solely to GLM: the new encoded-method test passed, while two redirect/netrc tests returned HTTP 502 and a legacy `pytest.raises(string)` path raised TypeError. See the [grade summary](glm-swe-grade-summary.json), [native result](glm-swe-native.json), [official report](glm-swe-official-report.json), and [bounded test excerpt](glm-swe-test-output.txt).

## Publication boundary

The [final artifact manifest](artifact-manifest.json) hashes the retained evidence. Its source records `gpus-final.json` as a historical plaintext command capture with a `.json` suffix and gives its compatibility reason explicitly. Final [GLM restoration](restoration.json) after V3 is verified: exact image/checkpoint/model and three configuration hashes match, all six direct checks passed, and an authenticated `llm.primary` smoke passed. The initial wrong-alias 404 remains historical failed-probe evidence. No MiMo capacity or speed comparison is supported. The SWE official 0/1 result remains recorded with its infrastructure confounds.

See [redaction provenance](redaction-provenance.md) for the deterministic public transformation.
