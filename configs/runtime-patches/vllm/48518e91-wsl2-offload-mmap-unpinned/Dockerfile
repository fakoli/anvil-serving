FROM voipmonitor/vllm@sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f

COPY wsl2-offload-mmap-pin-control.patch /tmp/wsl2-offload-mmap-pin-control.patch
RUN patch -p1 -d /opt/venv/lib/python3.12/site-packages \
      < /tmp/wsl2-offload-mmap-pin-control.patch \
    && rm /tmp/wsl2-offload-mmap-pin-control.patch

LABEL org.opencontainers.image.title="vLLM r16 DeepSeek native KV offload WSL2 translation" \
      org.opencontainers.image.description="Pinned r16 image with shared KV-offload mmap registration independently controllable while V2 UVA remains enabled" \
      org.opencontainers.image.base.digest="sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f" \
      org.opencontainers.image.vllm.patch="wsl2-offload-mmap-pin-control"
