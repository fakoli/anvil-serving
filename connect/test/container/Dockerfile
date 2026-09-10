# Preparation is an explicit networked build. Normal qualification is offline.
FROM mcr.microsoft.com/playwright@sha256:bc6ab0d6d44ff4826e4cb8c1e6d801e185bfc42bb0753f8e2a30efc70db054c7
USER root
RUN apt-get update && apt-get install --no-install-recommends -y libnss3-tools=2:3.98-1ubuntu0.2 && rm -rf /var/lib/apt/lists/*
COPY pins.json edge-tools.json transport.lock.json install_tools.py /build/
RUN python3 /build/install_tools.py
ENV PATH="/opt/go/bin:/opt/connect-tools:${PATH}"
ENV GOTOOLCHAIN=local GOMAXPROCS=2 GOFLAGS=-p=2
COPY go.mod go.sum /opt/connect-modules/
RUN cd /opt/connect-modules && GOMODCACHE=/opt/go-modules go mod download
COPY package.json package-lock.json /opt/connect-browser/
RUN cd /opt/connect-browser && npm ci --ignore-scripts --no-audit --no-fund
RUN chown -R 1000:1000 /opt/go-modules /opt/connect-browser && mkdir /work && chown 1000:1000 /work
ENV GOPROXY=off GOSUMDB=off GOMODCACHE=/opt/go-modules
USER 1000:1000
WORKDIR /work
CMD ["node", "--version"]
