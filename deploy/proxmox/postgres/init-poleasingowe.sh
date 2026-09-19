#!/bin/sh
set -eu

app_password=$(cat "$POLEASINGOWE_APP_PASSWORD_FILE")
grafana_password=$(cat "$GRAFANA_RO_PASSWORD_FILE")

if [ -z "$app_password" ] || [ -z "$grafana_password" ]; then
    echo "Hasła ról PostgreSQL nie mogą być puste" >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    --set=app_password="$app_password" \
    --set=grafana_password="$grafana_password" <<'SQL'
SELECT format(
    'CREATE ROLE poleasingowe_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'poleasingowe_app')
\gexec

SELECT format(
    'CREATE ROLE grafana_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
    :'grafana_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro')
\gexec

ALTER ROLE poleasingowe_app PASSWORD :'app_password';
ALTER ROLE poleasingowe_app NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE poleasingowe_app SET statement_timeout = '30s';
ALTER ROLE poleasingowe_app SET lock_timeout = '5s';
ALTER ROLE poleasingowe_app SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE poleasingowe_app CONNECTION LIMIT 5;

ALTER ROLE grafana_ro PASSWORD :'grafana_password';
ALTER ROLE grafana_ro NOSUPERUSER NOCREATEDB NOCREATEROLE;

SELECT 'CREATE DATABASE poleasingowe OWNER poleasingowe_app TEMPLATE template0'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'poleasingowe')
\gexec

REVOKE ALL ON DATABASE poleasingowe FROM PUBLIC;
GRANT CONNECT ON DATABASE poleasingowe TO poleasingowe_app, grafana_ro;

\connect poleasingowe

REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION poleasingowe_app;
CREATE SCHEMA IF NOT EXISTS reporting AUTHORIZATION poleasingowe_app;
GRANT USAGE ON SCHEMA reporting TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE poleasingowe_app IN SCHEMA reporting
    GRANT SELECT ON TABLES TO grafana_ro;
SQL
