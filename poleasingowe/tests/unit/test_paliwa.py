"""Ujednolicenie nazw paliwa — zamknięta lista sześciu wartości (SPEC.md §6.2).

Serwisy używają tu **różnych słów**, nie tylko różnej wielkości liter.
Wartości w testach pochodzą z rzeczywistych danych źródeł i z filtra na HA
(2026-09-14: kilka rodzajów hybryd, wartości niebędące paliwem).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.infrastructure.sources import paliwa

MIGRACJA = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app/migrations/023_paliwa_i_skrzynie.sql"
)


@pytest.mark.parametrize(
    "warianty,kanoniczne",
    [
        (
            ["Olej napedowy", "Olej napędowy", "Diesel", "diesel", "ON", "2.0 TDI"],
            "Diesel",
        ),
        (["Benzyna", "benzyna", "Petrol", "PB", "Gasoline"], "Benzyna"),
        # WSZYSTKIE hybrydy to jedna pozycja — decyzja właściciela repo.
        (
            [
                "Hybryda",
                "Hybryda/benzyna",
                "hybryda/olej napędowy",
                "Hybryda plug-in",
                "PHEV",
                "MHEV",
                "Hybrid (HEV)",
                "Mild Hybrid",
            ],
            "Hybryda",
        ),
        (["Elektryczny", "elektryczne", "Electric", "EV", "BEV"], "Elektryczny"),
        (["Wodór", "wodor", "Hydrogen", "FCEV"], "Wodór"),
        # Gaz obejmuje LPG i CNG, także jako dodatek do benzyny.
        (
            [
                "LPG",
                "lpg",
                "Gaz",
                "CNG",
                "Benzyna+LPG",
                "Benzyna + LPG",
                "Benzyna / LPG",
                "Benzyna + gaz",
                "benzyna i gaz",
                "Benzyna z instalacją gazową",
            ],
            "Gaz",
        ),
    ],
)
def test_to_samo_paliwo_dostaje_jedna_nazwe(
    warianty: list[str], kanoniczne: str
) -> None:
    assert {paliwa.kanoniczne_paliwo(w) for w in warianty} == {kanoniczne}


def test_wynik_zawsze_z_zamknietej_listy_albo_none() -> None:
    probki = ["Diesel", "PHEV", "LPG", "Kategoria 1", "1", "brak danych", "", "  "]
    for p in probki:
        wynik = paliwa.kanoniczne_paliwo(p)
        assert wynik is None or wynik in paliwa.KANONICZNE, p


def test_nieznana_wartosc_daje_none_a_nie_nowa_pozycje_filtra() -> None:
    """Filtr z zamkniętą listą nie rośnie od danych; „Kategoria 1" to nie paliwo."""
    assert paliwa.kanoniczne_paliwo("Kategoria 1") is None
    assert paliwa.kanoniczne_paliwo("nie dotyczy") is None
    assert paliwa.kanoniczne_paliwo("") is None
    assert paliwa.kanoniczne_paliwo("  ") is None


def test_granice_slow_nie_lapia_podciagow() -> None:
    """`ev` nie ma łapać `diesel`, `gas` — `gasoline`, `on` — `benzyna+on`?"""
    assert paliwa.kanoniczne_paliwo("Diesel") == "Diesel"
    assert paliwa.kanoniczne_paliwo("Gasoline") == "Benzyna"
    assert paliwa.kanoniczne_paliwo("Steven") is None


def test_migracja_i_kod_maja_te_same_wzorce() -> None:
    """Migracja `023` POWTARZA wzorce za kodem Pythona (SQL nie zawoła
    `paliwa.py`). Rozjazd byłby cichy, więc pilnuje go ten test.

    POSIX pisze granicę słowa `\\y`, Python `\\b` — porównujemy po
    sprowadzeniu do jednej postaci, w tej samej kolejności.
    """
    sql = MIGRACJA.read_text(encoding="utf-8")
    blok = sql.split("SET fuel = CASE", 1)[1].split("END", 1)[0]
    z_sql = [
        (wzorzec.replace("\\y", "\\b"), paliwo)
        for wzorzec, paliwo in re.findall(r"WHEN k ~\* '([^']+)' THEN '([^']+)'", blok)
    ]
    assert z_sql == list(paliwa.WZORCE), "wzorce paliw rozjechały się z kodem"
    assert {paliwo for _, paliwo in z_sql} == set(paliwa.KANONICZNE)
