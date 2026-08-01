FROM lmsysorg/sglang@sha256:984699c298a95b73c469b2191403ddc85fd780506e13c39c4afff3845e27bc6c

COPY disable-wsl2-multimem-logits.patch /tmp/disable-wsl2-multimem-logits.patch
RUN patch -p1 -d /sgl-workspace/sglang < /tmp/disable-wsl2-multimem-logits.patch \
    && rm /tmp/disable-wsl2-multimem-logits.patch

LABEL org.opencontainers.image.title="SGLang DeepSeek V4 WSL2 TP2 qualification runtime" \
      org.opencontainers.image.description="Pinned SGLang image with symmetric-memory logits gather disabled in favor of NCCL under WSL2" \
      org.opencontainers.image.base.digest="sha256:984699c298a95b73c469b2191403ddc85fd780506e13c39c4afff3845e27bc6c"
