"""Opcje add-onu (SPEC.md §7.1) i redakcja sekretów (§10.2)."""

from __future__ import annotations

import json
import logging
import pathlib

import pytest

from app.infrastructure.supervisor.options import (
    BladKonfiguracji,
    Opcje,
    Poswiadczenia,
    wczytaj_opcje,
)

MINIMALNE = {"db_password": "tajne-haslo"}


def zapisz(katalog: pathlib.Path, dane: object) -> pathlib.Path:
    sciezka = katalog / "options.json"
    sciezka.write_text(json.dumps(dane), encoding="utf-8")
    return sciezka


def test_domyslne_wartosci_zgodne_z_sekcja_0_spec(tmp_path: pathlib.Path) -> None:
    """SPEC.md §0 — hostname, port, baza i rola są ustalone na serwerze."""
    opcje = wczytaj_opcje(zapisz(tmp_path, MINIMALNE))
    assert opcje.db_host == "db21ed7f-postgres-latest"
    assert opcje.db_port == 5432
    assert opcje.db_name == "poleasingowe"
    assert opcje.db_user == "poleasingowe_app"
    assert opcje.log_level == "INFO"
    assert opcje.debug_dumps is False
    assert opcje.notify_on_critical is False


def test_brak_pliku_konczy_sie_czytelnym_komunikatem(tmp_path: pathlib.Path) -> None:
    with pytest.raises(BladKonfiguracji, match="Brak pliku opcji"):
        wczytaj_opcje(tmp_path / "nie-ma.json")


def test_zepsuty_json_konczy_sie_czytelnym_komunikatem(
    tmp_path: pathlib.Path,
) -> None:
    sciezka = tmp_path / "options.json"
    sciezka.write_text("{ to nie jest json", encoding="utf-8")
    with pytest.raises(BladKonfiguracji, match="nie jest poprawnym JSON"):
        wczytaj_opcje(sciezka)


def test_brak_hasla_zatrzymuje_addon(tmp_path: pathlib.Path) -> None:
    """SPEC.md §7.1 — błędna konfiguracja to zatrzymanie, nie domyślne wartości."""
    with pytest.raises(BladKonfiguracji) as blad:
        wczytaj_opcje(zapisz(tmp_path, {}))
    assert "db_password" in str(blad.value)
    assert "Popraw opcje" in str(blad.value)


def test_nieznana_opcja_jest_bledem_a_nie_ignorowana(
    tmp_path: pathlib.Path,
) -> None:
    """Literówka w nazwie opcji ma się ujawnić, a nie zostać po cichu pominięta."""
    with pytest.raises(BladKonfiguracji, match="db_hostt|Extra inputs"):
        wczytaj_opcje(zapisz(tmp_path, {**MINIMALNE, "db_hostt": "x"}))


@pytest.mark.parametrize(
    "floor,czy_ok",
    [(10, True), (60, True), (9, False), (0, False), (86_401, False)],
)
def test_floor_ma_twarda_dolna_granice(
    tmp_path: pathlib.Path, floor: int, czy_ok: bool
) -> None:
    """SPEC.md §11.2 — poniżej 10 s nie schodzimy nigdy."""
    dane = {**MINIMALNE, "sources": [{"key": "efl", "floor_seconds": floor}]}
    if czy_ok:
        assert wczytaj_opcje(zapisz(tmp_path, dane)).sources[0].floor_seconds == floor
    else:
        with pytest.raises(BladKonfiguracji):
            wczytaj_opcje(zapisz(tmp_path, dane))


def test_zduplikowane_zrodlo_jest_bledem(tmp_path: pathlib.Path) -> None:
    dane = {**MINIMALNE, "sources": [{"key": "efl"}, {"key": "efl"}]}
    with pytest.raises(BladKonfiguracji, match="Zduplikowane klucze"):
        wczytaj_opcje(zapisz(tmp_path, dane))


def test_haslo_nie_pojawia_sie_w_bezpiecznym_opisie(tmp_path: pathlib.Path) -> None:
    """SPEC.md §10.2 — opis połączenia trafia do logów, DSN nie."""
    opcje = wczytaj_opcje(zapisz(tmp_path, MINIMALNE))
    assert "tajne-haslo" not in opcje.bezpieczny_opis()
    assert "tajne-haslo" in opcje.dsn, "DSN musi mieć hasło — po to jest"


def test_poswiadczenia_nie_ujawniaja_hasla_w_repr() -> None:
    """SPEC.md §10.2 — nawet przypadkowy repr w logu nie ma prawa go pokazać."""
    p = Poswiadczenia(source="efl", username="jan", password="bardzo-tajne")
    assert "bardzo-tajne" not in repr(p)
    assert "bardzo-tajne" not in str(p)
    assert "bardzo-tajne" not in f"{p}"


def test_calosc_opcji_nie_wypluwa_hasla_przy_formatowaniu(
    tmp_path: pathlib.Path,
) -> None:
    dane = {
        **MINIMALNE,
        "credentials": [
            {"source": "efl", "username": "jan", "password": "haslo-do-efl"}
        ],
    }
    opcje = wczytaj_opcje(zapisz(tmp_path, dane))
    assert "haslo-do-efl" not in repr(opcje.credentials)


def test_filtr_logow_zasłania_sekrety(caplog: pytest.LogCaptureFixture) -> None:
    """SPEC.md §10.2 — filtr na loggerze, nie dyscyplina autora."""
    from app.interfaces.main import _FiltrRedakcji

    log = logging.getLogger("test-redakcji")
    log.addFilter(_FiltrRedakcji(["tajne-haslo", "haslo-do-efl"]))
    with caplog.at_level(logging.INFO, logger="test-redakcji"):
        log.info("łączę się jako user z hasłem tajne-haslo do bazy")
        log.info("Set-Cookie: sessionid=abc123; HttpOnly")

    tresc = "\n".join(r.getMessage() for r in caplog.records)
    assert "tajne-haslo" not in tresc
    assert "***" in tresc
    assert "abc123" not in tresc, "ciasteczko sesji nie ma prawa trafić do logu"


def test_dsn_sklada_sie_z_opcji(tmp_path: pathlib.Path) -> None:
    dane = {
        **MINIMALNE,
        "db_host": "localhost",
        "db_port": 5433,
        "db_name": "testowa",
        "db_user": "rola",
    }
    opcje = wczytaj_opcje(zapisz(tmp_path, dane))
    assert opcje.dsn == "postgresql://rola:tajne-haslo@localhost:5433/testowa"
    assert opcje.bezpieczny_opis() == "rola@localhost:5433/testowa"


def test_model_odrzuca_nieznany_poziom_logow(tmp_path: pathlib.Path) -> None:
    with pytest.raises(BladKonfiguracji):
        wczytaj_opcje(zapisz(tmp_path, {**MINIMALNE, "log_level": "GADATLIWY"}))


def test_opcje_da_sie_zbudowac_wprost_w_testach() -> None:
    """Sanity check — model musi dać się utworzyć bez pliku."""
    opcje = Opcje(db_password="x")
    assert opcje.sources == []
    assert opcje.credentials == []


# --------------------------------------------------------------------------
# Wybór dostawcy wyceny AI
# --------------------------------------------------------------------------


def test_stara_nazwa_klucza_nadal_wlacza_wycene(tmp_path: pathlib.Path) -> None:
    """Aktualizacja dodatku nie ma prawa wyłączyć działającej wyceny.

    Supervisor nie przepisuje opcji sam, a `extra="forbid"` odrzuciłby pole,
    którego model nie zna — czyli usunięcie `openai_api_key` zatrzymałoby
    add-on osobom, które go mają.
    """
    opcje = wczytaj_opcje(
        zapisz(tmp_path, {"db_password": "x", "openai_api_key": "sk-stare"})
    )
    assert opcje.klucz_ai == "sk-stare"
    assert opcje.ai_provider == "openai"


def test_nowa_nazwa_klucza_ma_pierwszenstwo(tmp_path: pathlib.Path) -> None:
    opcje = wczytaj_opcje(
        zapisz(
            tmp_path,
            {
                "db_password": "x",
                "ai_api_key": "sk-nowe",
                "openai_api_key": "sk-stare",
            },
        )
    )
    assert opcje.klucz_ai == "sk-nowe"


def test_wlasny_dostawca_bez_adresu_zatrzymuje_dodatek(
    tmp_path: pathlib.Path,
) -> None:
    """Cisze wolimy tu od domysłu: bez adresu dane poleciałyby do OpenAI,
    czyli nie tam, gdzie użytkownik chciał (SPEC.md §7.1)."""
    with pytest.raises(BladKonfiguracji, match="ai_base_url"):
        wczytaj_opcje(
            zapisz(
                tmp_path,
                {
                    "db_password": "x",
                    "ai_api_key": "k",
                    "ai_provider": "zgodny_z_openai",
                    "ai_model": "qwen3:14b",
                },
            )
        )


def test_bez_klucza_konfiguracja_ai_nie_jest_sprawdzana(
    tmp_path: pathlib.Path,
) -> None:
    """Wycena wyłączona to normalny stan, nie błąd konfiguracji."""
    opcje = wczytaj_opcje(
        zapisz(tmp_path, {"db_password": "x", "ai_provider": "zgodny_z_openai"})
    )
    assert opcje.klucz_ai == ""


def test_nieznany_dostawca_jest_odrzucany(tmp_path: pathlib.Path) -> None:
    with pytest.raises(BladKonfiguracji):
        wczytaj_opcje(
            zapisz(tmp_path, {"db_password": "x", "ai_provider": "moj-wlasny-model"})
        )
