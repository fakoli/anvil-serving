"""Out-of-process STT/TTS serve lifecycle adapters (anvil tasks T006/T008).

These modules manage bring-up/tear-down of the STT and TTS serves that the
voice pipeline's :mod:`~anvil_serving.voice.stages.stt` /
:mod:`~anvil_serving.voice.stages.tts` stages call over HTTP. Per the house
rule, the actual engine binary/container is declared in a `serves.toml`
manifest (the SAME declarative manifest `anvil-serving serves` already reads
-- see :mod:`anvil_serving.serves`), never hardcoded here: this package only
adds an OpenAI-compatible readiness probe on top of that existing
docker-manifest lifecycle, so `anvil-serving voice up`/`down` can report
"up and healthy" for the audio serves without ever shelling out to `docker`
itself.
"""
