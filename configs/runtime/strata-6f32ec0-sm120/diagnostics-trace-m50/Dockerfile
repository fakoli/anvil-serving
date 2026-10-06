FROM anvil-strata:6f32ec0-sm120-diagnostics-m50-v1@sha256:2451bf190e65d96337dbf15683c58465f08e2a8fe49c9a591c741f3c94c3d3b3
COPY trace_diagnostics.py install_trace.py /opt/strata/anvil/
RUN /opt/strata/.venv/bin/python /opt/strata/anvil/install_trace.py \
    && /opt/strata/.venv/bin/python -m py_compile /opt/strata/anvil_diagnostics.py /opt/strata/anvil_diagnostics_base.py
# Inherit the exact verified preparation wrapper, runtime profile and entrypoint.
# Timing, trace and fused controls remain opt-in recipe environment variables.
