"""Spójność opakowania add-onu (SPEC.md §7, §7.1).

Add-on ma trzy opisy tej samej rzeczy: `config.yaml` (widzi go Home Assistant),
model pydantic (widzi go kod) i `requirements.txt` (widzi go Docker). Każdy da
się zmienić osobno, więc każdy może się rozjechać po cichu. Te testy pilnują,
żeby się nie rozjechały.
"""

from __future__ import annotations

import pathlib
import re
import tomllib
from typing import Any

import pytest
import yaml

KORZEN = pathlib.Path(__file__).resolve().parents[3]
ADDON = KORZEN / "poleasingowe"
CONFIG = ADDON / "config.yaml"
REQUIREMENTS = ADDON / "requirements.txt"
PYPROJECT = KORZEN / "pyproject.toml"


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    wczytane: dict[str, Any] = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return wczytane


def test_ingress_wlaczony_i_nic_nie_publikujemy(config: dict[str, Any]) -> None:
    """SPEC.md §7.1 — Ingress uwierzytelnia, `ports` zostaje puste."""
    assert config["ingress"] is True
    assert config["ingress_port"] == 8099
    assert config["panel_icon"] == "mdi:car-search"
    assert (
        "ports" not in config
    ), "nic nie publikujemy na zewnątrz — obecność `ports` to błąd"


def test_bez_podniesionych_uprawnien(config: dict[str, Any]) -> None:
    """SPEC.md §7.1 — bez `privileged`, `host_network`, `full_access`."""
    for zakazane in ("privileged", "host_network", "full_access", "docker_api"):
        assert zakazane not in config, f"{zakazane} nie ma prawa się tu znaleźć"
    assert config["apparmor"] == "poleasingowe"
    assert config["init"] is False


def test_homeassistant_api_domyslnie_wylaczone(config: dict[str, Any]) -> None:
    """SPEC.md §7.1 — jedyne zastosowanie to alarm operacyjny, nie encje."""
    assert config["homeassistant_api"] is False


def test_port_ingress_zgadza_sie_z_kodem(config: dict[str, Any]) -> None:
    from app.interfaces.main import PORT_INGRESS

    assert config["ingress_port"] == PORT_INGRESS


def test_opcje_w_config_pokrywaja_sie_z_modelem(config: dict[str, Any]) -> None:
    """`config.yaml` i model pydantic opisują ten sam zestaw opcji."""
    from app.infrastructure.supervisor.options import Opcje

    z_configu = set(config["options"])
    z_modelu = set(Opcje.model_fields)
    assert z_configu == z_modelu, (
        f"tylko w config.yaml: {sorted(z_configu - z_modelu)}; "
        f"tylko w modelu: {sorted(z_modelu - z_configu)}"
    )


def test_schema_opisuje_wszystkie_opcje(config: dict[str, Any]) -> None:
    """Bez wpisu w `schema` Home Assistant nie pokaże pola w interfejsie."""
    assert set(config["schema"]) == set(config["options"])


def test_domyslne_z_configu_zgadzaja_sie_z_sekcja_0(config: dict[str, Any]) -> None:
    """SPEC.md §0 — te wartości są ustalone na serwerze, nie do zgadywania."""
    opcje = config["options"]
    assert opcje["db_host"] == "db21ed7f-postgres-latest"
    assert opcje["db_port"] == 5432
    assert opcje["db_name"] == "poleasingowe"
    assert opcje["db_user"] == "poleasingowe_app"


def test_haslo_ma_typ_password_w_schemacie(config: dict[str, Any]) -> None:
    """SPEC.md §10.2 — typ `password` sprawia, że HA nie pokazuje go jawnie."""
    assert config["schema"]["db_password"] == "password"
    assert config["schema"]["credentials"][0]["password"] == "password"


def _wersje(tekst: str) -> dict[str, str]:
    wynik: dict[str, str] = {}
    for linia in tekst.splitlines():
        linia = linia.strip().strip('",')
        if not linia or linia.startswith("#"):
            continue
        dopasowanie = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([0-9.]+)$", linia)
        if dopasowanie:
            wynik[dopasowanie.group(1).lower()] = dopasowanie.group(2)
    return wynik


def test_requirements_zgadza_sie_z_pyproject() -> None:
    """Docker instaluje z `requirements.txt`, testy chodzą na `pyproject`.

    Rozjazd między nimi znaczy, że obraz dostaje inne wersje niż te, na
    których cokolwiek sprawdziliśmy.
    """
    dane = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    z_pyproject = _wersje("\n".join(dane["project"]["dependencies"]))
    z_requirements = _wersje(REQUIREMENTS.read_text(encoding="utf-8"))

    wspolne = set(z_pyproject) & set(z_requirements)
    assert wspolne, "nie znalazłem wspólnych zależności — sprawdź parsowanie"
    rozjazdy = {
        n: (z_pyproject[n], z_requirements[n])
        for n in wspolne
        if z_pyproject[n] != z_requirements[n]
    }
    assert not rozjazdy, f"różne wersje w pyproject vs requirements: {rozjazdy}"

    brakujace = set(z_pyproject) - set(z_requirements)
    assert not brakujace, (
        f"zależności runtime nieobecne w requirements.txt: {sorted(brakujace)} "
        "— obraz ich nie zainstaluje"
    )


def test_dockerfile_wymusza_gotowe_kola() -> None:
    """SPEC.md §5 — brak koła ma wywalić budowanie, nie uruchomić kompilator.

    Kompilacja na Pentium Silver J5005 trwa nieakceptowalnie długo, więc
    `--only-binary=:all:` jest tu bramką, nie optymalizacją.
    """
    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    assert "--only-binary=:all:" in dockerfile
    assert dockerfile.count("FROM ") >= 2, "obraz ma być wieloetapowy (SPEC.md §7.1)"


def test_usluga_s6_jest_kompletna() -> None:
    """Bez wpisu w `user/contents.d` s6 nie uruchomi usługi w ogóle."""
    s6 = ADDON / "rootfs/etc/s6-overlay/s6-rc.d"
    assert (s6 / "poleasingowe/type").read_text(encoding="utf-8").strip() == "longrun"
    assert (s6 / "user/contents.d/poleasingowe").exists()
    uruchom = (s6 / "poleasingowe/run").read_text(encoding="utf-8")
    assert (
        "exec python3 -m app.interfaces.main" in uruchom
    ), "exec jest konieczny, żeby SIGTERM trafiał do Pythona, a nie do powłoki"


def test_wersje_addonu_sa_spojne(config: dict[str, Any]) -> None:
    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    assert f'io.hass.version="{config["version"]}"' in dockerfile


def test_repozytorium_wskazuje_ten_sam_adres(config: dict[str, Any]) -> None:
    repo: dict[str, Any] = yaml.safe_load(
        (KORZEN / "repository.yaml").read_text(encoding="utf-8")
    )
    assert repo["url"] == config["url"]
