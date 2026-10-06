# Draft: requires root/Astra review before any managed build or load.
FROM anvil-strata:6f32ec0-sm120@sha256:5f944f89efe58a127f934703449f6871583ffcda62d1fae4e4d58556fb5f3cea
COPY runtime.py /opt/strata/anvil/runtime.py
COPY runtime-configs /opt/strata/anvil/runtime-configs
RUN /opt/strata/.venv/bin/python -m py_compile /opt/strata/anvil/runtime.py
LABEL org.opencontainers.image.description="Unqualified immutable Strata context/concurrency experiments"
ENTRYPOINT ["/opt/strata/.venv/bin/python", "/opt/strata/anvil/runtime.py"]
