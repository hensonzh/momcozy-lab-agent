# Reproducible MinIO community build from the last reviewed upstream release.
# Public prebuilt minio/minio and minio/mc tags are no longer pullable.
FROM golang:1.24.8-alpine AS build
RUN apk add --no-cache ca-certificates curl git
ARG MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
ARG MC_RELEASE=RELEASE.2025-08-13T08-35-41Z
RUN git clone --depth 1 --branch RELEASE.2025-10-15T17-29-55Z https://github.com/minio/minio.git /source \
    && test "$(git -C /source rev-parse HEAD)" = "$MINIO_COMMIT"
WORKDIR /source
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -tags kqueue -trimpath -o /out/minio .
WORKDIR /out
RUN curl -fsSL --retry 3 \
      "https://github.com/minio/mc/releases/download/${MC_RELEASE}/mc.linux-amd64.${MC_RELEASE}" -o mc \
    && curl -fsSL --retry 3 \
      "https://github.com/minio/mc/releases/download/${MC_RELEASE}/mc.linux-amd64.${MC_RELEASE}.sha256sum" -o mc.sha256sum \
    && printf "%s  mc\n" "$(cut -d' ' -f1 mc.sha256sum)" | sha256sum -c - \
    && chmod 0755 minio mc

FROM alpine:3.21
RUN apk add --no-cache ca-certificates curl
COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /out/mc /usr/local/bin/mc
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a"
EXPOSE 9000
VOLUME ["/data"]
ENTRYPOINT ["/usr/local/bin/minio"]
