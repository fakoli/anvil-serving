FROM anvil-strata:6f32ec0-sm120-m50-v1@sha256:d9c5ae90a028ba7ae5cee9c01ebddc1524da3b831be47de0daf7c6a3e211817a
COPY diagnostics.py /opt/strata/anvil/diagnostics.py
COPY diagnostics.py /opt/strata/anvil_diagnostics.py
COPY runtime_diagnostics.py /opt/strata/anvil/runtime_diagnostics.py
COPY patch_server.py /opt/strata/anvil/patch_server.py
RUN /opt/strata/.venv/bin/python /opt/strata/anvil/patch_server.py \
    && /opt/strata/.venv/bin/python -m py_compile /opt/strata/serve/server.py /opt/strata/anvil/diagnostics.py /opt/strata/anvil/runtime_diagnostics.py
ENTRYPOINT ["/opt/strata/.venv/bin/python", "/opt/strata/anvil/runtime_diagnostics.py"]
