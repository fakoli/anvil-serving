FROM node:22.19.0-bookworm-slim

ARG PYTEST_VERSION=9.1.1

WORKDIR /opt/pi-runner
COPY package.json package-lock.json ./
COPY proxy.cjs ./
RUN npm ci --omit=dev --ignore-scripts \
    && apt-get update \
    && apt-get install --no-install-recommends -y git bash python3 python3-venv \
    && python3 -m venv /opt/pi-runner/venv \
    && /opt/pi-runner/venv/bin/python -m pip install --no-cache-dir "pytest==${PYTEST_VERSION}" \
    && /opt/pi-runner/venv/bin/python -m pytest --version \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin pi

ENV PATH="/opt/pi-runner/venv/bin:${PATH}"

USER pi
ENTRYPOINT ["/opt/pi-runner/node_modules/.bin/pi"]
