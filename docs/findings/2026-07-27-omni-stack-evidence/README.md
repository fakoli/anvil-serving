# Omni stack qualification evidence

Machine-readable evidence for the RTX 5090 Omni stack qualification.

- `gemma3n-access-diagnostic.json`: bounded authenticated checkpoint access
  probe and exact repository-approval denial.
- `gemma-access-verification.json`: post-acceptance config-only verification
  for all canonical Google Gemma repositories in the current recipe set.
- `omni-multimodal-preflight.json`: direct endpoint image and OCR gate.
- `omni-capacity.json`: direct endpoint bounded c2 capacity probe.
- `router-llm-voice.json`: routed auxiliary-text gate.
- `router-vision-general.json`: routed general-image gate.
- `router-vision-ocr.json`: routed OCR gate.
- `qwen25-omni-small-text-preflight.json`: small-Omni text/JSON/4K gate.
- `qwen25-omni-small-multimodal-preflight.json`: small-Omni image and OCR gate.
- `qwen25-omni-small-audio-input.json`: direct audio-input acceptance probe.
- `qwen25-omni-small-runtime-diagnostics.json`: bounded startup logs, package
  inventory, exact warnings, failure classification, and log-retention caveat.
- `qwen25-omni-small-capacity.json`: small-Omni bounded c2 capacity probe.
- `qwen25-omni-small-voice-audio.json`: co-resident Parakeet/Kokoro round trip.
