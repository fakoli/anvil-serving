FROM lmsysorg/sglang@sha256:b90c0d760a65bc4dbbe4520bea966c437cc40391dcb7cca2a74922985dc1abeb

COPY pinned-hf-quant-config.patch /tmp/pinned-hf-quant-config.patch

RUN python3 -m pip install --no-cache-dir "accelerate==1.14.0" \
    && python3 -c "import accelerate; assert accelerate.__version__ == '1.14.0'" \
    && patch -d /sgl-workspace/sglang -p1 < /tmp/pinned-hf-quant-config.patch \
    && rm /tmp/pinned-hf-quant-config.patch

LABEL org.opencontainers.image.title="SGLang Inkling ModelOpt TP2 qualification runtime" \
      org.opencontainers.image.description="Pinned SGLang image with Inkling SM120 and WSL2 TP2 compatibility fixes" \
      org.opencontainers.image.base.digest="sha256:b90c0d760a65bc4dbbe4520bea966c437cc40391dcb7cca2a74922985dc1abeb" \
      org.opencontainers.image.accelerate.version="1.14.0" \
      org.opencontainers.image.sglang.patch="pinned-hf-quant-config"
