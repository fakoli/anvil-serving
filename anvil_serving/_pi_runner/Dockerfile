FROM node:22.19.0-bookworm-slim

WORKDIR /opt/pi-runner
COPY package.json package-lock.json ./
COPY proxy.cjs ./
RUN npm ci --omit=dev --ignore-scripts \
    && apt-get update \
    && apt-get install --no-install-recommends -y git bash \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin pi

USER pi
ENTRYPOINT ["/opt/pi-runner/node_modules/.bin/pi"]
