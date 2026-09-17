FROM verdictai/glm53-flash-exl3-k4:r19-sm120-tp2-ep2-dcp2-v84-language-only@sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692
COPY b12x-master/w4a16/host.py /opt/infernal-invocation/b12x/b12x/moe/_shared/kernels/w4a16/host.py
COPY b12x-master/w4a16/kernel.py /opt/infernal-invocation/b12x/b12x/moe/_shared/kernels/w4a16/kernel.py
COPY b12x-master/w4a16/mixed_trellis.py /opt/infernal-invocation/b12x/b12x/moe/_shared/kernels/w4a16/mixed_trellis.py
COPY b12x-master/fused_moe/_impl.py /opt/infernal-invocation/b12x/b12x/moe/fused_moe/_impl.py
COPY vllm-patches/exl3-mixed.py /opt/infernal-invocation/vllm/vllm/model_executor/layers/quantization/exl3.py
RUN python3 -m compileall -q /opt/infernal-invocation/b12x/b12x/moe/_shared/kernels/w4a16/host.py /opt/infernal-invocation/b12x/b12x/moe/_shared/kernels/w4a16/kernel.py /opt/infernal-invocation/b12x/b12x/moe/_shared/kernels/w4a16/mixed_trellis.py /opt/infernal-invocation/b12x/b12x/moe/fused_moe/_impl.py /opt/infernal-invocation/vllm/vllm/model_executor/layers/quantization/exl3.py
RUN python3 -c "from b12x.moe._shared.kernels.w4a16 import mixed_trellis; import vllm.model_executor.layers.quantization.exl3"
COPY test_stream_loader.py /opt/anvil-test-stream-loader.py
RUN python3 /opt/anvil-test-stream-loader.py
