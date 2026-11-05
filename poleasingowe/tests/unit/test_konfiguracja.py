"""Konfiguracja z Diagnostyki: zapis, pierwszeństwo i ochrona klucza."""

from __future__ import annotations

import json
import pathlib
import stat
from unittest.mock import MagicMock
from urllib.parse import urljoin, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.supervisor.options import (
    BladKonfiguracji,
    Opcje,
    OpcjeZrodla,
    Poswiadczenia,
    ProfilAI,
    wczytaj_opcje,
    wczytaj_profile,
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
        "wycena_pauza_minuty": "7",
        "wycena_pauza_otomoto_minuty": "15",
        "wycena_auto_przerwa_s": "90",
        "wycena_rodzaj_OSOBOWY": "on",
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
    app.state.sciezka_profili = plik.with_name("profile.json")
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
    cel = urljoin(str(odpowiedz.url), odpowiedz.headers["location"])
    assert urlsplit(cel).path == "/diagnostyka"
    strona = klient.get(cel)
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


def test_ekran_restartu_wraca_wzgledem_base(tmp_path: pathlib.Path) -> None:
    klient, _ = _aplikacja(tmp_path / "k.json")
    odpowiedz = klient.post("/diagnostyka/konfiguracja/przywroc")
    assert 'content="8;url=diagnostyka"' in odpowiedz.text
    assert "../diagnostyka" not in odpowiedz.text


def test_profil_zapamietuje_klucz_i_przelacza_bez_wpisywania(
    tmp_path: pathlib.Path,
) -> None:
    plik = tmp_path / "k.json"
    profile = tmp_path / "profile.json"
    klient, restarty = _aplikacja(plik)
    zapis = klient.post(
        "/diagnostyka/konfiguracja/profil",
        data={"nazwa": "deepseek"},
        follow_redirects=False,
    )
    assert zapis.status_code == 303
    cel = urljoin(str(zapis.url), zapis.headers["location"])
    assert urlsplit(cel).path == "/diagnostyka"
    assert restarty == []
    assert stat.S_IMODE(profile.stat().st_mode) == 0o600
    assert wczytaj_profile(profile)[0].ai_api_key == "klucz-stary"

    strona = klient.get("/diagnostyka")
    assert "aktywny" in strona.text
    assert "klucz-stary" not in strona.text

    uzycie = klient.post(
        "/diagnostyka/konfiguracja/profil/uzyj", data={"nazwa": "deepseek"}
    )
    assert uzycie.status_code == 200
    assert restarty == [1]
    assert json.loads(plik.read_text(encoding="utf-8"))["ai_api_key"] == "klucz-stary"


def test_uzycie_profilu_ustawia_jego_adres_i_klucz(tmp_path: pathlib.Path) -> None:
    plik = tmp_path / "k.json"
    klient, _ = _aplikacja(plik)
    (tmp_path / "profile.json").write_text(
        json.dumps(
            [
                ProfilAI(
                    nazwa="claudebox",
                    ai_provider="zgodny_z_openai",
                    ai_api_key="klucz-boxa",
                    ai_base_url="http://claudebox:3000/v1",
                    ai_model="sonnet",
                ).model_dump()
            ]
        ),
        encoding="utf-8",
    )
    klient.post("/diagnostyka/konfiguracja/profil/uzyj", data={"nazwa": "claudebox"})
    zapis = json.loads(plik.read_text(encoding="utf-8"))
    assert zapis["ai_base_url"] == "http://claudebox:3000/v1"
    assert zapis["ai_api_key"] == "klucz-boxa"
    assert zapis["sources"][0]["key"] == "efl"


def test_nieznany_profil_nie_restartuje(tmp_path: pathlib.Path) -> None:
    plik = tmp_path / "k.json"
    klient, restarty = _aplikacja(plik)
    odpowiedz = klient.post(
        "/diagnostyka/konfiguracja/profil/uzyj",
        data={"nazwa": "brak"},
        follow_redirects=False,
    )
    assert odpowiedz.status_code == 303
    assert restarty == []
    assert not plik.exists()


def test_usuniecie_profilu(tmp_path: pathlib.Path) -> None:
    klient, restarty = _aplikacja(tmp_path / "k.json")
    klient.post("/diagnostyka/konfiguracja/profil", data={"nazwa": "a"})
    klient.post("/diagnostyka/konfiguracja/profil/usun", data={"nazwa": "a"})
    assert wczytaj_profile(tmp_path / "profile.json") == []
    assert restarty == []


def test_zepsuty_plik_profili_nie_wywraca_diagnostyki(tmp_path: pathlib.Path) -> None:
    klient, _ = _aplikacja(tmp_path / "k.json")
    (tmp_path / "profile.json").write_text("{zepsute", encoding="utf-8")
    strona = klient.get("/diagnostyka")
    assert strona.status_code == 200
    assert "Usuń ten plik" in strona.text


def test_repr_profilu_ukrywa_klucz() -> None:
    profil = ProfilAI(nazwa="x", ai_provider="openai", ai_api_key="tajny")
    assert "tajny" not in repr(profil)


def test_pauza_jest_w_formularzu_i_ma_granice() -> None:
    zmiany = zmiany_konfiguracji(_formularz(), OPCJE, ["efl"])
    assert zastosuj_zmiany(OPCJE, zmiany).wycena_pauza_minuty == 7
    with pytest.raises(BladKonfiguracji, match="wycena_pauza_minuty"):
        zastosuj_zmiany(OPCJE, {"wycena_pauza_minuty": 0})


@pytest.mark.parametrize(
    ("pole", "wartosc", "zla_dol", "zla_gora"),
    [
        ("wycena_pauza_otomoto_minuty", 15, 4, 1441),
        ("wycena_auto_przerwa_s", 90, 9, 3601),
    ],
)
def test_tempo_automatu_jest_w_formularzu_i_ma_granice(
    pole: str, wartosc: int, zla_dol: int, zla_gora: int
) -> None:
    zmiany = zmiany_konfiguracji(_formularz(), OPCJE, ["efl"])
    assert getattr(zastosuj_zmiany(OPCJE, zmiany), pole) == wartosc
    for zla in (zla_dol, zla_gora):
        with pytest.raises(BladKonfiguracji, match=pole):
            zastosuj_zmiany(OPCJE, {pole: zla})


def test_typy_pojazdow_z_formularza() -> None:
    zmiany = zmiany_konfiguracji(_formularz(wycena_rodzaj_DOSTAWCZY="on"), OPCJE, [])
    assert zmiany["wycena_auto_rodzaje"] == ["OSOBOWY", "DOSTAWCZY"]
    formularz = _formularz()
    del formularz["wycena_rodzaj_OSOBOWY"]
    with pytest.raises(ValueError, match="co najmniej jeden typ"):
        zmiany_konfiguracji(formularz, OPCJE, ["efl"])


def test_blad_zapisu_wraca_do_sekcji_konfiguracji(tmp_path: pathlib.Path) -> None:
    """Konfiguracja stoi nisko, więc bez kotwicy komunikat byłby poza ekranem."""
    klient, restarty = _aplikacja(tmp_path / "k.json")
    odpowiedz = klient.post(
        "/diagnostyka/konfiguracja/profil",
        data={"nazwa": "x" * 41},
        follow_redirects=False,
    )
    assert odpowiedz.status_code == 303
    assert odpowiedz.headers["location"].endswith("#konfiguracja")
    assert restarty == []
