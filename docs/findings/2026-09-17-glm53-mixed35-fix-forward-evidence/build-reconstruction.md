# Generic image reconstruction

The fixed image derives from the pinned parent runtime
`sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692`.
The generic [Dockerfile](Dockerfile) copies five files from the pinned upstream
checkout `8fc95f0da72072a49a697f2164410b851a4e7377` over that image. The
[loader-r7-stream.patch](loader-r7-stream.patch) applies to the upstream
`vllm-patches/exl3-mixed.py` file whose pre-patch SHA-256 is
`cfb98860254467153f5c7ae7df1beb818b97936cadb67aeaa606521db376e00d`; this
is a file hash, not a source revision. The build then runs
[test_stream_loader.py](test_stream_loader.py). The resulting candidate image identity was
`sha256:d1311adc96a37d235858aa397040ba5e550366961f1d4142f048e30476b93c47`.

The build used Linux amd64, two CPUs, 8,192 MiB build memory, and an 1,800-second
timeout. Do not use this reconstruction as an operator command or substitute
it for the managed recipe and human-gated lifecycle.
