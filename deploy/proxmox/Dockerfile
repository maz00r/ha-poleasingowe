# Samodzielny obraz OCI. Add-on HA nadal używa poleasingowe/Dockerfile.
FROM python:3.12-alpine3.22 AS budowanie

ENV PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY poleasingowe/requirements.txt /tmp/requirements.txt
RUN pip install --prefix=/instalacja --only-binary=:all: -r /tmp/requirements.txt

FROM python:3.12-alpine3.22

ARG APP_VERSION=dev
ARG VCS_REF=unknown

RUN apk add --no-cache ca-certificates postgresql17-client \
    && addgroup -g 10001 poleasingowe \
    && adduser -D -H -u 10001 -G poleasingowe poleasingowe \
    && mkdir -p /opt/poleasingowe /data /share /config \
    && chown -R 10001:10001 /opt/poleasingowe /data /share

COPY poleasingowe/certyfikaty/certum-dv-tls-g2-r39-ca.crt \
    /usr/local/share/ca-certificates/
RUN update-ca-certificates

COPY --from=budowanie /instalacja /usr/local
COPY --chown=10001:10001 poleasingowe/app /opt/poleasingowe/app

ENV PYTHONPATH=/opt/poleasingowe \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POLEASINGOWE_OPTIONS=/config/options.json

WORKDIR /opt/poleasingowe
USER 10001:10001
EXPOSE 8099

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/zdrowie', timeout=3)"]

LABEL org.opencontainers.image.title="Aukcje poleasingowe" \
      org.opencontainers.image.description="Zbieranie ofert z aukcji poleasingowych" \
      org.opencontainers.image.source="https://github.com/maz00r/ha-poleasingowe" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

CMD ["python", "-m", "app.interfaces.main"]
