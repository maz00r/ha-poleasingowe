#!/usr/bin/env bash
# Przygotowuje LOKALNY PostgreSQL 17 do testow repozytoriow (SPEC.md §13).
#
# Po co to w ogole istnieje: testy NIGDY nie moga dotknac instancji
# db21ed7f-postgres-latest w Home Assistant, bo tam mieszka TeslaMate,
# ktorego danych nie da sie odtworzyc z zadnego innego zrodla (SPEC.md §2).
#
# Skrypt odwzorowuje produkcje na tyle, zeby test wylapal problem, ktory
# inaczej wyszedlby dopiero na wspoldzielonym serwerze:
#   - rola aplikacji NIE jest superuzytkownikiem, wiec migracja wymagajaca
#     CREATE EXTENSION albo ALTER SYSTEM wywali sie tutaj (SPEC.md §0);
#   - te same limity na roli co na produkcji (SPEC.md §2 pkt 5), wiec
#     zapytanie nieszczescace sie w 30 s wywali sie tutaj;
#   - ten sam locale i encoding co na serwerze docelowym.
#
# Skrypt jest idempotentny. Nie tworzy bazy `poleasingowe` — testy tworza
# i kasuja wlasne bazy tymczasowe.
set -euo pipefail

PG_BIN="/opt/homebrew/opt/postgresql@17/bin"
export PATH="$PG_BIN:$PATH"

HASLO="${POLEASINGOWE_TEST_PASSWORD:-poleasingowe_test}"

if ! pg_isready -q; then
    echo "PostgreSQL nie odpowiada. Uruchom: brew services start postgresql@17" >&2
    exit 1
fi

wersja=$(psql -d postgres -tAc "SHOW server_version_num")
if [ "$wersja" -lt 170000 ]; then
    echo "STOP: SPEC.md wymaga PostgreSQL 17, a lokalnie jest $(psql -d postgres -tAc 'SHOW server_version')" >&2
    exit 1
fi

psql -d postgres -v ON_ERROR_STOP=1 -v haslo="$HASLO" <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'poleasingowe_app') THEN
        CREATE ROLE poleasingowe_app LOGIN NOSUPERUSER NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
        CREATE ROLE grafana_ro LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB;
    END IF;
END
$$;
SQL

psql -d postgres -v ON_ERROR_STOP=1 <<SQL
ALTER ROLE poleasingowe_app WITH PASSWORD '$HASLO';
ALTER ROLE grafana_ro       WITH PASSWORD '$HASLO';

-- Limity z SPEC.md §2 pkt 5, zeby lokalny test mial te same ograniczenia
-- co produkcja. Zapytanie, ktore nie miesci sie w 30 s, ma sie wywalic tutaj.
ALTER ROLE poleasingowe_app SET statement_timeout = '30s';
ALTER ROLE poleasingowe_app SET lock_timeout = '5s';
ALTER ROLE poleasingowe_app SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE poleasingowe_app CONNECTION LIMIT 5;

-- Testy tworza i kasuja wlasne bazy tymczasowe, wiec rola ich potrzebuje.
-- Na produkcji tego uprawnienia NIE MA — tam baza jest tworzona recznie
-- i add-on nigdy nie tworzy ani nie usuwa baz (SPEC.md §2 pkt 1).
ALTER ROLE poleasingowe_app CREATEDB;
SQL

echo "Gotowe. Role:"
psql -d postgres -c "\du poleasingowe_app"
psql -d postgres -c "\du grafana_ro"
echo
echo "Polaczenie testowe:"
echo "  postgresql://poleasingowe_app:\${POLEASINGOWE_TEST_PASSWORD:-poleasingowe_test}@localhost:5432/<baza>"
