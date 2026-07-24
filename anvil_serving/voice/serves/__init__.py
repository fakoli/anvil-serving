"""Out-of-process STT/TTS serve lifecycle adapters (anvil tasks T006/T008).

These modules manage bring-up/tear-down of the STT and TTS serves that the
voice pipeline's :mod:`~anvil_serving.voice.stages.stt` /
:mod:`~anvil_serving.voice.stages.tts` stages call over HTTP. Docker-backed
serves delegate to the same declarative `serves.toml` lifecycle used by
:mod:`anvil_serving.serves`. Same-host native serves on an audio-owning host
use trusted commands declared in the voice manifest with PID/log files. Both
paths keep engine details out of the pipeline stages and add an
OpenAI-compatible readiness probe for `anvil-serving voice audio up/down`.

The Realtime voice proxy can likewise be run as a Docker-managed container via
:mod:`anvil_serving.voice.serves.proxy` (mirroring the STT/TTS managed-serve
pattern), brought up by `anvil-serving voice proxy up` with a managed
``[voice.proxy]`` lifecycle.
"""

from .proxy import ProxyServe, ProxyServeConfig
from .stt import STTServe, STTServeConfig
from .tts import TTSServe, TTSServeConfig

__all__ = [
    "ProxyServe",
    "ProxyServeConfig",
    "STTServe",
    "STTServeConfig",
    "TTSServe",
    "TTSServeConfig",
]
