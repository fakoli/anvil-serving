# Anvil Voice

Anvil Voice runs speech-to-text, text-to-speech, and realtime voice as one
operated domain. A voice profile declares which host owns each audio model and
which host runs the realtime proxy, so a split-host setup stays explicit
instead of hidden behind fallback.

<!-- product-journey:anvil-voice -->

## When to use it

- You want local STT and TTS behind the same gateway as your language models.
- Your realtime proxy and your audio models live on different hosts.
- You need latency and quality evidence before changing a voice model.

## Go deeper

- [Voice pipeline](../VOICE.md) — profiles, routes, and the realtime path
- [Managed native MLX Audio runtime](../MLX-AUDIO-RUNTIME.md)
- [Voice model results](../benchmarks/voice-models.md)
- Command reference: [voice](../cli/voice.md)
