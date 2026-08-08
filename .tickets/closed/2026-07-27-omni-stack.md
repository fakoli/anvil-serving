# Omni stack consolidation

Status: completed locally on 2026-07-27.

## Completed

- Replaced the separate auxiliary LLM, OCR, and vision serves with one
  production-named `omni` serve and one `omni-local` router tier.
- Split `omni-stack` from `auxiliary-stack`; `llm-stack` now means primary plus
  Omni and no longer over-reserves the RTX 5090.
- Made Omni evictable so the existing ComfyUI guarded drain/eviction flow
  remains valid.
- Added independent, bounded `image` and `ocr` preflight checks to both the CLI
  implementation and controller schema, including input hash/type/size evidence.
- Pinned the measured image digest, checkpoint revision, context, reservation,
  and vLLM flags; published text, multimodal, OCR, lifecycle, and capacity
  evidence.
- Removed the temporary candidate container and brought the production `omni`
  container up through the managed serve verb.
- Added and live-tested `router install-config` for safe topology migrations
  where tier IDs change: validate, quiesce, drain, atomic install, restart
  retry, and exact ready-tier-set verification.

## Tooling gaps observed

- The running controller did not expose the newly added multimodal preflight
  arguments until its package is upgraded/restarted, so the first live
  image/OCR artifact used the product CLI.
- The controller benchmark artifact call lacked its configured artifact root;
  the product CLI wrote the bounded artifact instead.
- The controller exposes serve lifecycle but not the new topology-migration
  verb or the existing guarded `serves promote` transaction. The local product
  verb required obtaining the router token into the invoking process with
  `router token --reveal --confirm`.

## Follow-up completed: small Omni plus voice

- Added `omni-small-stack` and `omni-voice-stack`. The latter resolves to
  Qwen2.5-Omni-3B, Parakeet STT, and Kokoro TTS; reranking and embeddings are
  not implicit members.
- Retained Gemma 3n E2B as an unverified pinned recipe after the managed pull
  returned Hugging Face `403 GatedRepoError` because the account had not been
  authorized for the Google license. No Gemma load or memory result is claimed.
- Repeated Gemma access with the product model-pull verb, the user-local
  credential file, the pinned revision, and only `config.json`. Credential
  forwarding and the space gate succeeded; the download container returned
  exactly `Access denied. This repository requires approval.` The published
  diagnostic therefore classifies this as authorization/license approval, not
  missing authentication.
- After browser acceptance, repeated config-only pulls passed for the pinned
  Gemma 3n checkpoint and all three canonical Google Gemma 4 repositories in
  the current recipe registry. The access blocker is resolved without a full
  checkpoint download.
- Built the Qwen service locally from the pinned vLLM digest. The first audio
  request exposed a missing `vllm[audio]` runtime extra; a small pinned
  derivative image fixed that durable packaging gap.
- Passed small-Omni text, JSON, 4K retrieval, image, OCR, audio-input, and c2
  capacity probes. Passed the co-resident Parakeet/Kokoro audio round trip with
  0.0 WER.
- Measured 28,743 MiB total 5090 use with small Omni and voice resident. The
  manifest now reserves 24,576 MiB for Qwen, 2,048 MiB for each voice service,
  and 3,072 MiB for Windows/display, leaving 863 MiB of admission headroom.
- Fixed the lifecycle admission boundary: serving, voice, controller, and
  ComfyUI paths now load the complete colocated manifest set. Previously, a
  command entered through `serves.voice.toml` or `serves.toml` could omit
  reservations owned by the other file.

### Remaining human gate

- `omni-small` is not mapped into the router. Promoting it requires an explicit
  human decision after model-quality review; its direct audio response was
  semantically correct but too noisy to replace dedicated STT.

### Additional tooling gap

- The already-running controller process had the pre-change schema and needed
  a restart before it could expose the newly added image/OCR arguments. There
  is still no first-class audio-input preflight check, so that probe used a
  bounded direct OpenAI-compatible request.
- The managed Qwen logs identified an absent external `flash_attn` package and
  Transformers token-ID validation warnings. Functional gates pass, but these
  are now recorded as compatibility caveats rather than being hidden behind
  the HTTP result.
- `serves logs tts` initially crashed on Windows because Unicode progress-bar
  glyphs could not be written through the active CP1252 console. The managed
  log writer now escapes unsupported characters, retains stderr, and completes
  successfully; a regression test covers the narrow-console path.
