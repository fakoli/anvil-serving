"""Out-of-process STT/TTS serve lifecycle adapters (anvil tasks T006/T008).

These modules manage bring-up/tear-down of the STT and TTS serves that the
voice pipeline's :mod:`~anvil_serving.voice.stages.stt` /
:mod:`~anvil_serving.voice.stages.tts` stages call over HTTP. Docker-backed
serves delegate to the same declarative `serves.toml` lifecycle used by
:mod:`anvil_serving.serves`. Same-host native serves on an audio-owning host
use trusted commands declared in the voice manifest with PID/log files. Both
paths keep engine details out of the pipeline stages and add an
OpenAI-compatible readiness probe for `anvil-serving voice up/down`.
"""
