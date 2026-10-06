FROM anvil-strata:6f32ec0-sm120-variants-v1@sha256:d8a0d5b90db4e5a3b8770a60298c97cf73bfaaa72c5d5bcb4e1f920a4153fbc2
COPY runtime_m50.py /opt/strata/anvil/runtime_m50.py
COPY runtime-configs /opt/strata/anvil/runtime-configs-m50
RUN /opt/strata/.venv/bin/python -m py_compile /opt/strata/anvil/runtime_m50.py
ENTRYPOINT ["/opt/strata/.venv/bin/python", "/opt/strata/anvil/runtime_m50.py"]
