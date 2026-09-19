#!/bin/sh
set -eu

uzycie() {
    echo "Użycie: $0 [--replace] /ścieżka/do/pakietu.tar" >&2
    exit 2
}

replace=0
if [ "${1:-}" = "--replace" ]; then
    replace=1
    shift
fi
[ "$#" -eq 1 ] || uzycie

pakiet=$1
[ -f "$pakiet" ] || { echo "Brak pakietu: $pakiet" >&2; exit 1; }
if [ -f "$pakiet.sha256" ]; then
    oczekiwany=$(tr -d '\r\n' < "$pakiet.sha256")
    rzeczywisty=$(sha256sum "$pakiet" | awk '{print $1}')
    if [ "$oczekiwany" != "$rzeczywisty" ]; then
        echo "Suma SHA-256 całego pakietu jest niepoprawna" >&2
        exit 1
    fi
fi

cd "$(dirname "$0")/.."

if docker compose ps --services --status running app | grep -qx app; then
    echo "Najpierw zatrzymaj aplikację: docker compose stop app" >&2
    exit 1
fi
if ! docker compose ps --services --status running postgres | grep -qx postgres; then
    echo "PostgreSQL nie działa. Uruchom: docker compose up -d postgres" >&2
    exit 1
fi

roboczy=$(mktemp -d)
trap 'rm -rf "$roboczy"' EXIT INT TERM
python3 ./scripts/verify_package.py "$pakiet" "$roboczy"

tabele=$(docker compose exec -T postgres psql -U postgres -d poleasingowe -tAc \
    "SELECT count(*) FROM pg_tables WHERE schemaname IN ('app','reporting')")

if [ "$tabele" -gt 0 ] && [ "$replace" -ne 1 ]; then
    echo "Baza docelowa nie jest pusta. Powtórz z --replace, aby ją zastąpić." >&2
    exit 1
fi

if docker compose run --rm --no-deps --entrypoint sh app -c \
    'test -d /data/archiwum-zdjec && test -n "$(find /data/archiwum-zdjec -type f -print -quit)"'
then
    if [ "$replace" -ne 1 ]; then
        echo "Archiwum zdjęć nie jest puste. Powtórz z --replace." >&2
        exit 1
    fi
fi

docker compose cp "$roboczy/database.dump" postgres:/tmp/poleasingowe-import.dump
docker compose exec -T --user root postgres chown postgres:postgres \
    /tmp/poleasingowe-import.dump
docker compose exec -T postgres pg_restore \
    --username postgres \
    --dbname poleasingowe \
    --clean \
    --if-exists \
    --single-transaction \
    --no-owner \
    --no-privileges \
    --role poleasingowe_app \
    /tmp/poleasingowe-import.dump

docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
    --username postgres --dbname poleasingowe <<'SQL'
REVOKE ALL ON DATABASE poleasingowe FROM PUBLIC;
GRANT CONNECT ON DATABASE poleasingowe TO poleasingowe_app, grafana_ro;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA reporting TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE poleasingowe_app IN SCHEMA reporting
    GRANT SELECT ON TABLES TO grafana_ro;
SQL

docker compose exec -T --user root postgres rm -f /tmp/poleasingowe-import.dump

if [ "$replace" -eq 1 ]; then
    docker compose run --rm --no-deps --entrypoint sh app -c \
        'mkdir -p /data/archiwum-zdjec && find /data/archiwum-zdjec -mindepth 1 -delete'
fi
docker compose run --rm --no-deps --entrypoint sh \
    -v "$roboczy/archiwum-zdjec:/import:ro" app -c \
    'mkdir -p /data/archiwum-zdjec && cp -a /import/. /data/archiwum-zdjec/'

docker compose exec -T postgres psql -U postgres -d poleasingowe -c \
    "SELECT schemaname, count(*) AS tabele FROM pg_tables WHERE schemaname IN ('app','reporting') GROUP BY schemaname ORDER BY schemaname"

echo "Odtworzenie zakończone. Uruchom aplikację z sources: [] i zweryfikuj dane."
