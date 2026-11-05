"""Konfiguracja z Diagnostyki: zapis, pierwszeństwo i ochrona klucza."""

from __future__ import annotations

import json
import pathlib
import stat
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.supervisor.options import (
    BladKonfiguracji,
    Opcje,
    OpcjeZrodla,
    Poswiadczenia,
    wczytaj_opcje,
    zapisz_nadpisania,
    zastosuj_zmiany,
)
from app.interfaces.app import utworz_aplikacje
from app.interfaces.web.formularze import zmiany_konfiguracji

OPCJE = Opcje(
    db_password="tajne-haslo-bazy",
    ai_provider="zgodny_z_openai",
    ai_api_key="klucz-stary",
    ai_base_url="https://api.deepseek.com/v1",
    ai_model="deepseek-chat",
    credentials=[Poswiadczenia(source="efl", username="ja", password="tajne-konto")],
    sources=[OpcjeZrodla(key="efl", rate_limit_per_minute=20, floor_seconds=90)],
)


def _formularz(**zmiany: str) -> dict[str, str]:
    return {
        "ai_provider": "zgodny_z_openai",
        "ai_base_url": "https://api.deepseek.com/v1",
        "ai_model": "deepseek-chat",
        "wycena_auto": "on",
        "wycena_auto_limit_dzienny": "50",
        "zrodlo_efl_wlaczone": "on",
        "zrodlo_efl_limit": "20",
        "zrodlo_efl_floor": "90",
        **zmiany,
    }


def test_puste_pole_klucza_zostawia_klucz() -> None:
    zmiany = zmiany_konfiguracji(_formularz(ai_api_key=""), OPCJE, ["efl"])
    assert "ai_api_key" not in zmiany


@pytest.mark.parametrize(
    "pole",
    [{"ai_base_url": "https://zly.example/v1"}, {"ai_provider": "openai"}],
)
def test_nowy_adres_lub_dostawca_wymaga_wpisania_klucza(pole: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="klucza API"):
        zmiany_konfiguracji(_formularz(**pole), OPCJE, ["efl"])


def test_nowy_adres_z_nowym_kluczem_przechodzi() -> None:
    zmiany = zmiany_konfiguracji(
        _formularz(ai_base_url="http://claudebox:3000/v1", ai_api_key=" nowy "),
        OPCJE,
        ["efl"],
    )
    assert zmiany["ai_api_key"] == "nowy"


def test_usuniecie_klucza_czysci_tez_stara_nazwe_opcji() -> None:
    zmiany = zmiany_konfiguracji(_formularz(ai_usun_klucz="on"), OPCJE, ["efl"])
    assert zmiany["ai_api_key"] == ""
    assert zmiany["openai_api_key"] == ""


def test_zrodla_z_formularza() -> None:
    formularz = _formularz(
        zrodlo_dawro_wlaczone="on", zrodlo_dawro_limit="10", zrodlo_dawro_floor="60"
    )
    del formularz["zrodlo_efl_wlaczone"]
    zrodla = zmiany_konfiguracji(formularz, OPCJE, ["efl", "dawro", "mleasing"])[
        "sources"
    ]
    assert zrodla == [
        {
            "key": "dawro",
            "enabled": True,
            "rate_limit_per_minute": 10,
            "floor_seconds": 60,
        },
        {
            "key": "efl",
            "enabled": False,
            "rate_limit_per_minute": 20,
            "floor_seconds": 90,
        },
    ]


def test_zla_liczba_daje_zdanie_dla_uzytkownika() -> None:
    with pytest.raises(ValueError, match="Dzienny limit"):
        zmiany_konfiguracji(
            _formularz(wycena_auto_limit_dzienny="dużo"), OPCJE, ["efl"]
        )


def test_zmiany_przechodza_walidacje_startowa() -> None:
    with pytest.raises(BladKonfiguracji, match="ai_base_url"):
        zastosuj_zmiany(OPCJE, {"ai_base_url": ""})
    with pytest.raises(BladKonfiguracji, match="wycena_auto_limit_dzienny"):
        zastosuj_zmiany(OPCJE, {"wycena_auto_limit_dzienny": 0})


def test_plik_ma_prawa_0600_i_tylko_dozwolone_pola(tmp_path: pathlib.Path) -> None:
    plik = tmp_path / "konfiguracja.json"
    zapisz_nadpisania(OPCJE, plik)
    assert stat.S_IMODE(plik.stat().st_mode) == 0o600
    tresc = plik.read_text(encoding="utf-8")
    assert "tajne" not in tresc
    assert json.loads(tresc)["ai_api_key"] == "klucz-stary"


def test_nadpisania_maja_pierwszenstwo_przed_opcjami(tmp_path: pathlib.Path) -> None:
    plik_opcji = tmp_path / "options.json"
    plik_opcji.write_text(
        json.dumps({"db_password": "h", "ai_model": "stary"}), encoding="utf-8"
    )
    nadpisania = tmp_path / "konfiguracja.json"
    nadpisania.write_text(json.dumps({"ai_model": "nowy"}), encoding="utf-8")
    assert wczytaj_opcje(plik_opcji, nadpisania).ai_model == "nowy"
    nadpisania.unlink()
    assert wczytaj_opcje(plik_opcji, nadpisania).ai_model == "stary"


@pytest.mark.parametrize("tresc", ["{zepsute", "[]", '{"db_password": "x"}'])
def test_zly_plik_nadpisan_mowi_co_zrobic(tmp_path: pathlib.Path, tresc: str) -> None:
    plik_opcji = tmp_path / "options.json"
    plik_opcji.write_text(json.dumps({"db_password": "h"}), encoding="utf-8")
    nadpisania = tmp_path / "konfiguracja.json"
    nadpisania.write_text(tresc, encoding="utf-8")
    with pytest.raises(BladKonfiguracji, match="Usuń ten plik"):
        wczytaj_opcje(plik_opcji, nadpisania)


def _aplikacja(plik: pathlib.Path) -> tuple[TestClient, list[int]]:
    app = utworz_aplikacje(OPCJE)
    restarty: list[int] = []
    app.state.sciezka_nadpisan = plik
    app.state.restart = lambda: restarty.append(1)
    return TestClient(app), restarty


def test_klucz_nie_wraca_na_ekran(tmp_path: pathlib.Path) -> None:
    klient, _ = _aplikacja(tmp_path / "k.json")
    strona = klient.get("/diagnostyka")
    assert strona.status_code == 200
    assert "Zapisz i uruchom ponownie" in strona.text
    assert "klucz-stary" not in strona.text


def test_zapis_tworzy_plik_i_restartuje(tmp_path: pathlib.Path) -> None:
    plik = tmp_path / "k.json"
    klient, restarty = _aplikacja(plik)
    odpowiedz = klient.post("/diagnostyka/konfiguracja", data=_formularz())
    assert odpowiedz.status_code == 200
    assert "Konfiguracja zapisana" in odpowiedz.text
    assert restarty == [1]
    assert (
        json.loads(plik.read_text(encoding="utf-8"))["wycena_auto_limit_dzienny"] == 50
    )


def test_blad_nie_zapisuje_i_nie_restartuje(tmp_path: pathlib.Path) -> None:
    plik = tmp_path / "k.json"
    klient, restarty = _aplikacja(plik)
    odpowiedz = klient.post(
        "/diagnostyka/konfiguracja",
        data=_formularz(ai_base_url="https://zly.example/v1"),
        follow_redirects=False,
    )
    assert odpowiedz.status_code == 303
    assert not plik.exists()
    assert restarty == []
    strona = klient.get(odpowiedz.headers["location"].replace("..", ""))
    assert "wymaga ponownego wpisania klucza" in strona.text


def test_przywrocenie_usuwa_plik_i_restartuje(tmp_path: pathlib.Path) -> None:
    plik = tmp_path / "k.json"
    plik.write_text("{}", encoding="utf-8")
    klient, restarty = _aplikacja(plik)
    assert "Wróć do options.json" in klient.get("/diagnostyka").text
    assert klient.post("/diagnostyka/konfiguracja/przywroc").status_code == 200
    assert not plik.exists()
    assert restarty == [1]


def test_diagnostyka_dziala_bez_bazy(tmp_path: pathlib.Path) -> None:
    """Bez bazy konfiguracja i powód awarii mają być nadal widoczne."""
    fabryka = MagicMock()
    fabryka.stan_puli.return_value = {}  # tak odpowiada zamknięta pula
    app = utworz_aplikacje(OPCJE, fabryka=fabryka)
    app.state.sciezka_nadpisan = tmp_path / "k.json"
    strona = TestClient(app).get("/diagnostyka")
    assert strona.status_code == 200
    assert "Zapisz i uruchom ponownie" in strona.text
    fabryka.assert_not_called()
