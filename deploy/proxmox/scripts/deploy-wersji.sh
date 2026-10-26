#!/bin/sh
# Deploy konkretnej wersji obrazu — wywoływany przez GitHub Actions po SSH.
#
# Klucz CI w `authorized_keys` ma wymuszoną komendę (README §6), więc jedyne,
# co CI może zrobić, to uruchomić ten skrypt. Wersję przekazuje w poleceniu
# SSH (`ssh host 0.32.6`) — trafia tu jako $SSH_ORIGINAL_COMMAND i jest
# sprawdzana wzorcem, zanim dotknie `.env` albo Dockera.
#
# Po starcie czeka na zdrową aplikację (/zdrowie, baza dostępna). Gdy się nie
# doczeka, wraca do poprzedniej wersji i kończy się błędem — CI to pokaże.
set -eu

KATALOG=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WERSJA=${SSH_ORIGINAL_COMMAND:-${1:-}}

if ! printf '%s\n' "$WERSJA" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "Odrzucono: oczekiwano wersji X.Y.Z, dostano '${WERSJA}'" >&2
  exit 2
fi

cd "$KATALOG"
# Jeden deploy naraz — dwa tagi wypchnięte jeden po drugim nie mogą się
# przeplatać na `.env`.
exec 9>"$KATALOG/.deploy.lock"
flock -w 600 9

POPRZEDNIA=$(sed -n 's/^POLEASINGOWE_VERSION=//p' .env)
ADRES=$(sed -n 's/^APP_BIND_IP=//p' .env)

ustaw() {
  sed -i "s/^POLEASINGOWE_VERSION=.*/POLEASINGOWE_VERSION=$1/" .env
  docker compose pull app
  docker compose up -d --no-deps app
}

zdrowa() {
  for _ in $(seq 1 60); do
    if curl --fail --silent --max-time 5 "http://${ADRES}:8099/zdrowie" \
      | grep -q '"dostepna":true'; then
      return 0
    fi
    sleep 3
  done
  return 1
}

echo "Deploy ${POPRZEDNIA} -> ${WERSJA}"
ustaw "$WERSJA"
if zdrowa; then
  docker compose ps app
  echo "OK: działa ${WERSJA}"
  exit 0
fi

echo "BŁĄD: ${WERSJA} nie wstała zdrowa — wracam do ${POPRZEDNIA}" >&2
docker compose logs --tail 80 app >&2 || true
if [ -n "$POPRZEDNIA" ] && [ "$POPRZEDNIA" != "$WERSJA" ]; then
  ustaw "$POPRZEDNIA"
  zdrowa && echo "Przywrócono ${POPRZEDNIA}" >&2
fi
exit 1
