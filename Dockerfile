# anvil-serving router and remote controller images.
#
# stdlib-only runtime (CLAUDE.md gotcha #2 / pyproject `dependencies = []`); this image
# just gives the router a standard, supervised deployment target (Docker `restart:
# unless-stopped`) alongside the compose-defined serves (ADR-0002). Docker is an
# ADDITIONAL deployment option -- `pip install anvil-serving` still works unchanged.
#
# Build (from repo root): docker build -t anvil-serving:0.21.1 .
# Run:   docker run -p 127.0.0.1:8000:8000 \
#          -e ANVIL_ROUTER_TOKEN \
#          -v ./configs/example-docker.toml:/etc/anvil/config.toml:ro \
#          anvil-serving:0.21.1
ARG DOCKER_CLI_IMAGE=docker:29.6.2-cli@sha256:be132a9f282288de4afaf63379dff75711fda0147c6b72a9df44e51841402144

FROM ${DOCKER_CLI_IMAGE} AS docker-cli

FROM python:3.11-slim AS runtime

ARG ANVIL_SERVING_VERSION=0.21.1
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
RUN mkdir -p /etc/anvil /var/lib/anvil-serving

# Non-root runtime user (defense-in-depth: a compromised front door process should
# not run as root inside the container).
RUN useradd --system --create-home --shell /usr/sbin/nologin anvil \
    && chown -R anvil:anvil /etc/anvil /var/lib/anvil-serving
USER anvil

WORKDIR /home/anvil

ENV ANVIL_CONFIG=/etc/anvil/config.toml

FROM runtime AS router

EXPOSE 8000

# Liveness: GET /healthz is always unauthenticated (ADR-0004) so this needs no token.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()" || exit 1

# 0.0.0.0 is correct HERE (inside the container): host-side exposure is controlled by
# the published port (compose: only ${ROUTER_PUBLISH:-127.0.0.1}:8000:8000), not by the
# in-container bind address (CLAUDE.md gotcha #1 is about the HOST side).
ENTRYPOINT ["sh", "-c", "exec anvil-serving router run --config \"${ANVIL_CONFIG:-/etc/anvil/config.toml}\" --host 0.0.0.0 --port 8000"]

FROM runtime AS controller

# The controller is a separate image target. It gets only the pinned Docker CLI
# and Compose plugin required by managed lifecycle verbs; router images do not.
COPY --from=docker-cli --chown=root:root /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli --chown=root:root \
    /usr/local/libexec/docker/cli-plugins/docker-compose \
    /usr/local/libexec/docker/cli-plugins/docker-compose

ENV ANVIL_SERVING_HOME=/etc/anvil \
    ANVIL_SERVING_LOOPBACK_ALIAS=host.docker.internal

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD anvil-serving controller status --url http://127.0.0.1:8765 || exit 1

# The wildcard bind exists only inside the container. The reference Compose
# deployment publishes it on host loopback and Tailscale Serve owns remote reachability.
ENTRYPOINT ["anvil-serving", "controller", "serve"]
CMD ["--host", "0.0.0.0", "--port", "8765", "--allow-public-bind", "--state-db", "/var/lib/anvil-serving/controller-operations.sqlite3"]

# Preserve the historical `docker build .` behavior: the default final image is
# still the router. Build the controller explicitly with `--target controller`.
FROM router AS default
