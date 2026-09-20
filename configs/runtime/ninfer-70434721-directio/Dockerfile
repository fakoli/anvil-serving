FROM nvidia/cuda:13.1.2-devel-ubuntu24.04@sha256:b9f64abf7226fdb3463ca202bc99878ec847171e6c5f77bd34c8d1403fbf1eca

# Artifact-v2 compatible runtime. Later NInfer revisions require artifact v3.
# Disable mutable third-party repositories; CUDA is supplied by the pinned base.
RUN rm -f /etc/apt/sources.list.d/cuda*.list \
    && apt-get --snapshot 20260919T000000Z update \
    && DEBIAN_FRONTEND=noninteractive apt-get --snapshot 20260919T000000Z install -y --no-install-recommends \
       ca-certificates cmake git libavcodec-dev libavformat-dev libavutil-dev \
       libcurl4-openssl-dev libswscale-dev ninja-build pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN git init /opt/ninfer \
    && git -C /opt/ninfer remote add origin https://github.com/Neroued/ninfer.git \
    && git -C /opt/ninfer fetch --depth 1 origin 70434721b1ae29d0616f3de9b376c8a4d91590b5 \
    && git -C /opt/ninfer checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/ninfer rev-parse HEAD)" = 70434721b1ae29d0616f3de9b376c8a4d91590b5 \
    && cmake -S /opt/ninfer -B /opt/ninfer-build -G Ninja \
       -DCMAKE_BUILD_TYPE=Release -DNINFER_BUILD_APPS=ON \
       -DBUILD_TESTING=OFF -DNINFER_BUILD_BENCHMARKS=OFF \
    && cmake --build /opt/ninfer-build --parallel 4 --target ninfer ninfer-serve \
    && mkdir /opt/provenance \
    && sha256sum /opt/ninfer-build/apps/ninfer-serve > /opt/provenance/binary.sha256 \
    && dpkg-query -W > /opt/provenance/packages.tsv \
    && git -C /opt/ninfer rev-parse HEAD > /opt/provenance/source-revision
LABEL org.opencontainers.image.source="https://github.com/Neroued/ninfer" \
      org.opencontainers.image.revision="70434721b1ae29d0616f3de9b376c8a4d91590b5"
RUN printf '%s  %s\n' 82ec9634bf221bee534bcf26c940a7c63f29fff36cd858de9bc57e75c3e6b25f /opt/ninfer-build/apps/ninfer-serve | sha256sum --check
COPY verify-artifact.sh /opt/verify-artifact.sh
COPY entrypoint.sh /opt/entrypoint.sh
RUN chmod 0555 /opt/verify-artifact.sh /opt/entrypoint.sh
ENTRYPOINT ["/opt/entrypoint.sh"]
