# thermiq-bridge
#
#   docker build -t thermiq-bridge standalone/
#
# The build context is standalone/ only. Generated sources are checked in, so
# the image needs a C compiler and nothing else - no Python, no package
# manager, no network at build time beyond the base image itself.
#
# The result is a scratch image containing one static binary and nothing else:
# no shell, no libc, no package database, no CVE surface that is not this
# program's own code.

FROM alpine:3.22 AS build

RUN apk add --no-cache build-base

WORKDIR /src
COPY Makefile ./
COPY src ./src

# -static so the binary carries no loader or libc, which is what lets the
# runtime stage be scratch. --gc-sections drops everything unreferenced.
RUN make LDFLAGS="-static -Wl,--gc-sections" \
    && strip build/thermiq-bridge \
    && ls -l build/thermiq-bridge


FROM scratch

LABEL org.opencontainers.image.title="thermiq-bridge" \
      org.opencontainers.image.description="Thermia/Danfoss heat pump over MQTT: web UI, JSON API and Prometheus metrics, without Home Assistant" \
      org.opencontainers.image.source="https://github.com/larsablixth/thermiq_mqtt-ha" \
      org.opencontainers.image.licenses="MIT"

COPY --from=build /src/build/thermiq-bridge /thermiq-bridge

# Nobody. There is no passwd file in a scratch image, so this is numeric; the
# process needs no filesystem, no user and no privileges of any kind.
USER 65534:65534

ENV THERMIQ_HTTP_PORT=8080
EXPOSE 8080

# A scratch image has no curl to probe with, so the binary probes itself.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["/thermiq-bridge", "--healthcheck"]

ENTRYPOINT ["/thermiq-bridge"]
