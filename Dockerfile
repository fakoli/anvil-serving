# anvil-serving router and remote controller images.
#
# stdlib-only runtime (CLAUDE.md gotcha #2 / pyproject `dependencies = []`); this image
# just gives the router a standard, supervised deployment target (Docker `restart:
# unless-stopped`) alongside the compose-defined serves (ADR-0002). Docker is an
# ADDITIONAL deployment option -- `pip install anvil-serving` still works unchanged.
#
# Build (from repo root): docker build -t anvil-serving:2.0.1 .
# Run:   docker run -p 127.0.0.1:8000:8000 \
#          -e ANVIL_ROUTER_TOKEN \
#          -v ./configs/example-docker.toml:/etc/anvil/config.toml:ro \
#          anvil-serving:2.0.1
ARG DOCKER_CLI_IMAGE=docker:29.6.2-cli@sha256:be132a9f282288de4afaf63379dff75711fda0147c6b72a9df44e51841402144
# Official Python 3.13.15 slim-bookworm source:
# https://github.com/docker-library/python/blob/688a0b86bb44289df16a363e9f41d90514c1a5f9/3.13/slim-bookworm/Dockerfile
ARG PYTHON_RUNTIME_IMAGE=python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26
ARG SQLITE_SOURCE_SHA3_256=b6ced585c750916b1052667239a12c25a7ff34e7f06e1e6e01ecb0427657d9c4

# Official SQLite source archive and published SHA3-256:
# https://www.sqlite.org/2026/sqlite-autoconf-3540100.tar.gz
# https://www.sqlite.org/download.html

FROM ${DOCKER_CLI_IMAGE} AS docker-cli

FROM ${PYTHON_RUNTIME_IMAGE} AS sqlite-builder

ARG SQLITE_SOURCE_SHA3_256

# Build the official SQLite amalgamation separately. The Python image's
# _sqlite3 module exposes the Python 3.12+ connection API, but its linked
# SQLite must also be new enough for the router's native WAL policy.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential ca-certificates curl; \
    rm -rf /var/lib/apt/lists/*; \
    curl --fail --location --silent --show-error \
      https://www.sqlite.org/2026/sqlite-autoconf-3540100.tar.gz \
      --output /tmp/sqlite-autoconf.tar.gz; \
    python -B -c "import hashlib, pathlib; archive=pathlib.Path('/tmp/sqlite-autoconf.tar.gz').read_bytes(); assert hashlib.sha3_256(archive).hexdigest() == '$SQLITE_SOURCE_SHA3_256'"; \
    tar -xzf /tmp/sqlite-autoconf.tar.gz -C /tmp; \
    cd /tmp/sqlite-autoconf-3540100; \
    ./configure --prefix=/opt/sqlite --disable-static --enable-shared; \
    make -j2; \
    make install; \
    rm -rf /tmp/sqlite-autoconf.tar.gz /tmp/sqlite-autoconf-3540100

FROM ${PYTHON_RUNTIME_IMAGE} AS runtime

COPY --from=sqlite-builder /opt/sqlite/lib/ /opt/sqlite/lib/
ENV LD_LIBRARY_PATH=/opt/sqlite/lib

RUN python -B -c "import sqlite3; assert sqlite3.sqlite_version == '3.54.1'; assert hasattr(sqlite3.Connection, 'setconfig') and hasattr(sqlite3.Connection, 'getconfig')"

ARG ANVIL_SERVING_VERSION=2.0.1
LABEL org.opencontainers.image.title="anvil-serving" \
      org.opencontainers.image.version="${ANVIL_SERVING_VERSION}"

# Only what `pip install .` (stdlib-only, no extras) needs to build/install the wheel;
# no compiler toolchain required since anvil-serving has zero compiled deps.
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY anvil_serving ./anvil_serving
RUN pip install --no-cache-dir --no-compile .
RUN python -B -c "from anvil_serving import __version__; assert __version__ == '${ANVIL_SERVING_VERSION}'"

# Default config path the entrypoint reads unless ANVIL_CONFIG overrides it; the
# actual file is normally bind-mounted or baked in by whoever builds/runs the image.
RUN mkdir -p /etc/anvil /var/lib/anvil-serving/router-keys \
    && chmod 700 /var/lib/anvil-serving/router-keys

# Non-root runtime user (defense-in-depth: a compromised front door process should
# not run as root inside the container).
RUN useradd --system --create-home --shell /usr/sbin/nologin anvil \
    && chown -R anvil:anvil /etc/anvil /var/lib/anvil-serving
USER anvil

WORKDIR /home/anvil

# Run the storage smoke as the same non-root user used by the service.
COPY --chown=anvil:anvil scripts/router_wal_image_smoke.py /tmp/router_wal_image_smoke.py
RUN --network=none python -B /tmp/router_wal_image_smoke.py && rm /tmp/router_wal_image_smoke.py

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

FROM runtime AS dashboard

# The Observatory composition uses the packaged dashboard command. It needs no
# Docker socket, GPU devices, model mounts, Node runtime or controller binaries.
EXPOSE 8768
ENTRYPOINT ["anvil-serving", "dashboard", "serve"]
CMD ["--host", "127.0.0.1", "--port", "8768"]

FROM runtime AS controller

# New controller volumes inherit this private snapshot directory on first use.
RUN mkdir -p /home/anvil/.local/state/anvil-serving/router-snapshots \
    && chmod 700 /home/anvil/.local/state/anvil-serving /home/anvil/.local/state/anvil-serving/router-snapshots

# The controller is a separate image target. It gets only the pinned Docker CLI
# and Compose plugin required by managed lifecycle verbs; router images do not.
COPY --from=docker-cli --chown=root:root /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli --chown=root:root \
    /usr/local/libexec/docker/cli-plugins/docker-buildx \
    /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --from=docker-cli --chown=root:root \
    /usr/local/libexec/docker/cli-plugins/docker-compose \
    /usr/local/libexec/docker/cli-plugins/docker-compose

ENV ANVIL_SERVING_HOME=/etc/anvil \
    DOCKER_CONFIG=/tmp/docker-config \
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
