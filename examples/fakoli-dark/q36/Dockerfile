# syntax=docker/dockerfile:1.7

# q36 documents CUDA 13.1 and an sm_120a build. Keep both stages digest-pinned:
# the devel stage supplies nvcc 13.1.115, while the runtime stage stays smaller.
FROM nvidia/cuda@sha256:b9f64abf7226fdb3463ca202bc99878ec847171e6c5f77bd34c8d1403fbf1eca AS build

ARG Q36_REVISION=458eb018997565445f0ce0a4887ed7cdfeab756b

WORKDIR /src

# GitHub archive for Q36_REVISION. The checksum makes a changed archive fail closed.
ADD --checksum=sha256:875f52c5ac52680f212e1fa4b9a232cf722f6eea1d1bdf8af15fedf07785e7dd \
    https://github.com/ambud/q36/archive/458eb018997565445f0ce0a4887ed7cdfeab756b.tar.gz \
    /tmp/q36.tar.gz

RUN test "${Q36_REVISION}" = "458eb018997565445f0ce0a4887ed7cdfeab756b" \
    && tar -xzf /tmp/q36.tar.gz --strip-components=1 -C /src \
    && make all

FROM nvidia/cuda@sha256:bff001d3257971cc4752e15ac2d354befa70995ded8e141741ade50569fc192e

ARG Q36_REVISION=458eb018997565445f0ce0a4887ed7cdfeab756b

LABEL org.opencontainers.image.source="https://github.com/ambud/q36" \
      org.opencontainers.image.revision="${Q36_REVISION}" \
      org.opencontainers.image.description="q36 CUDA 13.1 sm_120a experiment image"

COPY --from=build \
    /src/q36 \
    /src/q36_server \
    /src/q36_bench \
    /src/q36_ppl \
    /src/q36_info \
    /src/test_dequant \
    /src/test_mma_bs \
    /opt/q36/

# nvcc links the engine with OpenMP. The CUDA runtime image omits libgomp, so
# carry the matching Ubuntu 24.04 library from the pinned devel stage.
COPY --from=build /usr/lib/x86_64-linux-gnu/libgomp.so.1* /usr/lib/x86_64-linux-gnu/

COPY entrypoint.sh /opt/q36/entrypoint.sh
RUN chmod 0555 /opt/q36/entrypoint.sh

# Preserve NVIDIA's runtime initialization, then exec the q36 engine directly.
ENTRYPOINT ["/opt/nvidia/nvidia_entrypoint.sh", "/opt/q36/entrypoint.sh"]
