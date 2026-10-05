FROM anvil-strata:6f32ec0-sm120-trace-m50-v1@sha256:ae0433aac9045a739de69ae9852a0611dc86a7e4d9a167bc4f5da1f5e92175ee
COPY runtime-configs/ /opt/strata/anvil/runtime-configs-prefill4096/
COPY runtime_prefill4096.py /opt/strata/anvil/runtime_prefill4096.py
RUN /opt/strata/.venv/bin/python -m py_compile /opt/strata/anvil/runtime_prefill4096.py
ENTRYPOINT ["/opt/strata/.venv/bin/python", "/opt/strata/anvil/runtime_prefill4096.py"]
