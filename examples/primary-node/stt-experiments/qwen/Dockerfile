ARG BASE_IMAGE=qwenllm/qwen3-asr@sha256:fb75b775f089e06e5a1aaebffd421e37505cc630d50c86d889d95ffa45a7e16a
FROM ${BASE_IMAGE}

COPY patch_vllm_qwen3_asr_transcription.py /tmp/patch_vllm_qwen3_asr_transcription.py
RUN python /tmp/patch_vllm_qwen3_asr_transcription.py \
    && rm /tmp/patch_vllm_qwen3_asr_transcription.py
