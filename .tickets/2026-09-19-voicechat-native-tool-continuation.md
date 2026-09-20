# Native VoiceChat requires a duplex and tool-result contract

Status: open; source-screened, no candidate loaded or qualified

The MLX VoiceChat session in `mlx-audio` revision
`77a6cfcaba9fcb246c9302f7196c05147501bd62` emits `function_delta` events but
exposes no complete call identity and no tool-result ingress/continuation API.
This blocks a whole tool-capable voice replacement through that exact runtime;
it is not a model-quality rejection or a finding about the upstream CUDA path.
Anvil's existing Realtime service is a serialized STT/LLM/TTS cascade and cannot
be treated as an integrated duplex adapter without implementation and tests.

Before a managed native trial:

- Close the immutable artifact and offline dependency set, including the
  conversion's source revision and secondary tokenizer. Use the
  [native acquisition and containment contract](2026-09-19-apple-native-artifact-and-memory-containment.md).
- Add an explicit native engine/recipe and bounded duplex adapter with C1,
  declared sample rates, session duration, cancellation, request isolation and
  bounded logs. An initial audio-only experiment must declare `tool_support=false`
  and reject tool-bearing input. Fake-runtime tests precede any weight load.
- Prove cancellation and resource accounting in the actual model process. A
  standalone memory-limit canary does not qualify a model launcher.
- Require complete tool-call identities, argument validation, result ingress
  and resumed response before claiming a tool-capable replacement. Do not infer
  support from a function fragment.
- Retain selected and rollback voice manifests, lifecycle, router identity and
  supported client configuration in the deployment owner's durable capture.
  Text-model catalog reconciliation alone does not converge voice sessions.

Pinned source: [MLX duplex implementation](https://github.com/Blaizzy/mlx-audio/blob/77a6cfcaba9fcb246c9302f7196c05147501bd62/mlx_audio/sts/models/nemotron_voicechat/streaming.py).
The existing cascade stays selected until independent quality, resource,
protocol and actual-client evidence supports a concrete promotion.
