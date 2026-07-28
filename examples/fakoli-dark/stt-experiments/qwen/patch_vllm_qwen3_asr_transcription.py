"""Apply Qwen's official ASR output parser to vLLM transcription responses.

The pinned official Qwen image includes ``qwen_asr.parse_asr_output`` but its
vLLM 0.14.0 transcription handler returns the model's internal
``language ...<asr_text>`` envelope. Fail if the pinned source changes instead
of silently applying a partial patch.
"""

from pathlib import Path

import vllm.entrypoints.openai.speech_to_text as speech_to_text


path = Path(speech_to_text.__file__)
source = path.read_text(encoding="utf-8")
old = (
    '            text = "".join(text_parts)\n'
    '            if self.task_type == "transcribe":\n'
)
new = (
    '            text = "".join(text_parts)\n'
    '            if getattr(self.model_config.hf_config, "model_type", None) == "qwen3_asr":\n'
    "                from qwen_asr import parse_asr_output\n"
    "\n"
    "                _, text = parse_asr_output(text, user_language=None)\n"
    '            if self.task_type == "transcribe":\n'
)
count = source.count(old)
if count != 1:
    raise RuntimeError(f"expected one vLLM 0.14.0 patch target, found {count}")
path.write_text(source.replace(old, new), encoding="utf-8")
