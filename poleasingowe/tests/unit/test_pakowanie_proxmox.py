"""Statyczne bramki opakowania standalone dla Proxmoxa."""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import yaml

from app.infrastructure.supervisor.options import Opcje
from app.wersja import WERSJA

KORZEN = pathlib.Path(__file__).resolve().parents[3]
DEPLOY = KORZEN / "deploy/proxmox"


def _compose() -> dict[str, Any]:
    wczytane: dict[str, Any] = yaml.safe_load(
        (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    )
    return wczytane


def test_standalone_uzywa_tego_samego_kodu_i_zaleznosci() -> None:
    dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY poleasingowe/requirements.txt" in dockerfile
    assert "COPY --chown=10001:10001 poleasingowe/app" in dockerfile
    assert "--only-binary=:all:" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'CMD ["python", "-m", "app.interfaces.main"]' in dockerfile
    assert "bashio" not in dockerfile
    assert "s6" not in dockerfile


def test_compose_nie_wystawia_postgresa_i_wiaze_port_vm() -> None:
    uslugi = _compose()["services"]
    assert "ports" not in uslugi["postgres"]
    assert uslugi["app"]["ports"] == ["${APP_BIND_IP:?Ustaw APP_BIND_IP}:8099:8099"]
    assert uslugi["app"]["read_only"] is True
    assert uslugi["app"]["cap_drop"] == ["ALL"]
    assert uslugi["app"]["depends_on"]["postgres"]["condition"] == ("service_healthy")


def test_przyklad_opcji_pokrywa_model_i_ma_pusta_liste_zrodel() -> None:
    dane = json.loads((DEPLOY / "options.example.json").read_text(encoding="utf-8"))
    assert set(dane) == set(Opcje.model_fields)
    assert dane["db_host"] == "postgres"
    assert dane["db_password"] == ""
    assert dane["sources"] == []


def test_przyklad_env_jest_przypiety_do_wersji_aplikacji() -> None:
    env = (DEPLOY / ".env.example").read_text(encoding="utf-8")
    dopasowanie = re.search(r"^POLEASINGOWE_VERSION=(.+)$", env, re.MULTILINE)
    assert dopasowanie is not None
    assert dopasowanie.group(1) == WERSJA


def test_inicjalizacja_bazy_utrzymuje_granice_uprawnien() -> None:
    skrypt = (DEPLOY / "postgres/init-poleasingowe.sh").read_text(encoding="utf-8")
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in skrypt
    assert "CONNECTION LIMIT 5" in skrypt
    assert "statement_timeout = '30s'" in skrypt
    assert "REVOKE ALL ON DATABASE poleasingowe FROM PUBLIC" in skrypt
    assert "CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION poleasingowe_app" in skrypt
    assert "GRANT USAGE ON SCHEMA reporting TO grafana_ro" in skrypt


def test_postgres_kopiuje_sekrety_do_prywatnych_plikow() -> None:
    compose = _compose()["services"]["postgres"]
    assert compose["entrypoint"] == ["/usr/local/bin/poleasingowe-entrypoint.sh"]

    skrypt_path = DEPLOY / "postgres/entrypoint.sh"
    skrypt = skrypt_path.read_text(encoding="utf-8")
    assert skrypt_path.stat().st_mode & 0o111
    assert "install -m 0400 -o postgres -g postgres" in skrypt
    assert "exec /usr/local/bin/docker-entrypoint.sh" in skrypt


def test_restore_wymaga_jawnego_replace_i_przywraca_granty() -> None:
    skrypt_path = DEPLOY / "scripts/restore-package.sh"
    skrypt = skrypt_path.read_text(encoding="utf-8")
    assert skrypt_path.stat().st_mode & 0o111
    assert "--replace" in skrypt
    assert "--single-transaction" in skrypt
    assert "--no-owner" in skrypt
    assert "--no-privileges" in skrypt
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO grafana_ro" in skrypt
