# anvil-serving router — container image (ADR-0004: router as a service).
#
# stdlib-only runtime (CLAUDE.md gotcha #2 / pyproject `dependencies = []`); this image
# just gives the router a standard, supervised deployment target (Docker `restart:
# unless-stopped`) alongside the compose-defined serves (ADR-0002). Docker is an
# ADDITIONAL deployment option -- `pip install anvil-serving` still works unchanged.
#
# Build (from repo root): docker build -t anvil-serving:0.13.1 .
# Run:   docker run -p 127.0.0.1:8000:8000 \
#          -e ANVIL_ROUTER_TOKEN \
#          -v ./configs/example-docker.toml:/etc/anvil/config.toml:ro \
#          anvil-serving:0.13.1
FROM python:3.11-slim

ARG ANVIL_SERVING_VERSION=0.13.1
LABEL org.opencontainers.image.title="anvil-serving" \
      org.opencontainers.image.version="${ANVIL_SERVING_VERSION}"

# Only what `pip install .` (stdlib-only, no extras) needs to build/install the wheel;
# no compiler toolchain required since anvil-serving has zero compiled deps.
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY anvil_serving ./anvil_serving
RUN pip install --no-cache-dir --no-compile .

# Default config path the entrypoint reads unless ANVIL_CONFIG overrides it; the
# actual file is normally bind-mounted or baked in by whoever builds/runs the image.
RUN mkdir -p /etc/anvil

# Non-root runtime user (defense-in-depth: a compromised front door process should
# not run as root inside the container).
RUN useradd --system --create-home --shell /usr/sbin/nologin anvil \
    && chown -R anvil:anvil /etc/anvil
USER anvil

WORKDIR /home/anvil
EXPOSE 8000

ENV ANVIL_CONFIG=/etc/anvil/config.toml

# Liveness: GET /healthz is always unauthenticated (ADR-0004) so this needs no token.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()" || exit 1

# 0.0.0.0 is correct HERE (inside the container): host-side exposure is controlled by
# the published port (compose: only ${ROUTER_PUBLISH:-127.0.0.1}:8000:8000), not by the
# in-container bind address (CLAUDE.md gotcha #1 is about the HOST side).
ENTRYPOINT ["sh", "-c", "exec anvil-serving router run --config \"${ANVIL_CONFIG:-/etc/anvil/config.toml}\" --host 0.0.0.0 --port 8000"]
