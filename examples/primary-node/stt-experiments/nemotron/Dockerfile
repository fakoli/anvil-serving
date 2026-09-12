ARG BASE_IMAGE=pytorch/pytorch@sha256:60f22fb80755fd0b470fb47928dbd55816aa9f847edd95cf43c93253507a9ddf
FROM ${BASE_IMAGE}

ARG TRANSFORMERS_VERSION=5.13.0

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir \
        "transformers[serving]==${TRANSFORMERS_VERSION}" \
        "librosa==0.11.0" \
        "python-multipart==0.0.32"

COPY patch_transformers_transcription.py /tmp/patch_transformers_transcription.py
RUN python /tmp/patch_transformers_transcription.py \
    && rm /tmp/patch_transformers_transcription.py

EXPOSE 8000
