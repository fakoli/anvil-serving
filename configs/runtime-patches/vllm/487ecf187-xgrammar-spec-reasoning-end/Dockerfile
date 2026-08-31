FROM ghcr.io/tpurtell/glm-5.3-flash-exl3-4bpw-2x-rtx@sha256:001a45bd71bcf908a8c07459570bdb8c5e0a205d085f29ac7f3201529fa3eb75

COPY xgrammar-spec-reasoning-end.patch /tmp/xgrammar-spec-reasoning-end.patch
RUN printf '%s  %s\n%s  %s\n' \
      '355f6f1193c15d5d6901a0f567e2e16005e3681f04f70079c6ba11e020b4d33a' \
      '/usr/local/lib/python3.12/dist-packages/vllm/v1/structured_output/__init__.py' \
      '3fd606dc2b8e950fe9b49f28cf1c030be78beaaf7c78b457b5942a0909d3457f' \
      '/usr/local/lib/python3.12/dist-packages/vllm/v1/structured_output/backend_xgrammar.py' \
      | sha256sum -c - \
    && patch -p1 -d /usr/local/lib/python3.12/dist-packages \
      < /tmp/xgrammar-spec-reasoning-end.patch \
    && printf '%s  %s\n%s  %s\n' \
      '003b090b3182e377dff48561050ba86b6f671e0e5c60cc35984e96df8699c386' \
      '/usr/local/lib/python3.12/dist-packages/vllm/v1/structured_output/__init__.py' \
      '906b24eae8ca3cdd9425a87f9e2dfae9ef9840cfd2f3be647d7b1f4ba72cbab4' \
      '/usr/local/lib/python3.12/dist-packages/vllm/v1/structured_output/backend_xgrammar.py' \
      | sha256sum -c - \
    && rm /tmp/xgrammar-spec-reasoning-end.patch

LABEL org.opencontainers.image.title="Anvil GLM-5.3-Flash structured-output runtime" \
      org.opencontainers.image.description="Pinned Purtell GLM runtime with vLLM 12f64b39 and c6e19b3 structured-output backports" \
      org.opencontainers.image.base.digest="sha256:001a45bd71bcf908a8c07459570bdb8c5e0a205d085f29ac7f3201529fa3eb75" \
      org.opencontainers.image.vllm.commit="487ecf187" \
      org.opencontainers.image.vllm.patches="12f64b39d29282437e35be9aa5db432fb2a1a6e6,c6e19b3be24338759a443e03c8325d76da9ee202"
