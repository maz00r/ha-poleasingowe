#!/bin/sh
set -eu

# Docker Compose realizuje lokalne sekrety jako bind mounty i zachowuje
# uprawnienia plików hosta. Kopia należąca do postgres pozwala utrzymać źródła
# w trybie 0600, również gdy sekret aplikacji należy do UID 10001.
secrets_dir=/run/poleasingowe-postgres-secrets
install -d -m 0700 -o postgres -g postgres "$secrets_dir"

stage_secret() {
    source_path=$1
    target_name=$2
    target_path="$secrets_dir/$target_name"

    if [ ! -r "$source_path" ]; then
        echo "Nie można odczytać sekretu PostgreSQL: $source_path" >&2
        exit 1
    fi
    if [ ! -s "$source_path" ]; then
        echo "Sekret PostgreSQL jest pusty: $source_path" >&2
        exit 1
    fi

    install -m 0400 -o postgres -g postgres "$source_path" "$target_path"
}

stage_secret "$POSTGRES_PASSWORD_FILE" postgres_admin_password
stage_secret "$POLEASINGOWE_APP_PASSWORD_FILE" poleasingowe_app_password
stage_secret "$GRAFANA_RO_PASSWORD_FILE" grafana_ro_password

export POSTGRES_PASSWORD_FILE="$secrets_dir/postgres_admin_password"
export POLEASINGOWE_APP_PASSWORD_FILE="$secrets_dir/poleasingowe_app_password"
export GRAFANA_RO_PASSWORD_FILE="$secrets_dir/grafana_ro_password"

exec /usr/local/bin/docker-entrypoint.sh "$@"
