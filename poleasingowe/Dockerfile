# Wieloetapowy, bez kompilatorow w warstwie runtime (SPEC.md §7.1).
#
# Zostajemy na Alpine: sprawdzone, ze KAZDA zaleznosc binarna ma kolo
# musllinux x86_64 dla cp312 — psycopg-binary, pydantic-core, selectolax,
# uvloop, watchfiles, httptools. Gdyby ktorakolwiek go nie miala, SPEC.md §5
# kaze przejsc na wariant Debian, a nie kompilowac w obrazie: kompilacja
# na Pentium Silver J5005 trwa nieakceptowalnie dlugo.
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.22

# --- etap budowania -------------------------------------------------------
FROM ${BUILD_FROM} AS budowanie

# Instalujemy do osobnego prefiksu, zeby przeniesc do runtime same biblioteki,
# bez cache pip-a i bez narzedzi budowania.
ENV PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt /tmp/requirements.txt

# --only-binary=:all: to bramka, nie optymalizacja: jesli ktorakolwiek
# zaleznosc nie ma kola dla musllinux, budowanie ma sie WYWALIC, zamiast po
# cichu zaczac kompilowac (SPEC.md §5).
RUN pip install --prefix=/instalacja --only-binary=:all: -r /tmp/requirements.txt

# --- etap runtime ---------------------------------------------------------
FROM ${BUILD_FROM}

COPY --from=budowanie /instalacja /usr/local

# Kod aplikacji. Katalog nadrzedny jest korzeniem pakietu `app`, dokladnie
# tak jak lokalnie (pythonpath = poleasingowe/).
COPY app /opt/poleasingowe/app
COPY rootfs /

ENV PYTHONPATH=/opt/poleasingowe \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/poleasingowe

LABEL \
  io.hass.name="Aukcje poleasingowe" \
  io.hass.description="Zbieranie ofert z serwisow aukcji poleasingowych" \
  io.hass.type="addon" \
  io.hass.version="0.3.0" \
  io.hass.arch="amd64"
