"""Ujednolicenie nazw paliwa (SPEC.md §6.2).

Serwisy używają tu **różnych słów**, nie tylko różnej wielkości liter —
i to jest różnica wobec marek. Wartości w testach pochodzą z rzeczywistych
danych trzech źródeł.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.infrastructure.sources import paliwa


@pytest.mark.parametrize(
    "warianty,kanoniczne",
    [
        # Jedno paliwo pod trzema nazwami — poleasingowe pisze bez ogonka,
        # EFL z ogonkiem, autoprzetarg zupełnie inaczej.
        (["Olej napedowy", "Olej napędowy", "Diesel", "diesel", "ON"], "Diesel"),
        (["Benzyna", "benzyna", "Petrol"], "Benzyna"),
        # EFL pisze samo `Hybryda`, poleasingowe `Hybryda/benzyna`.
        (["Hybryda", "Hybryda/benzyna", "hybryda/olej napędowy"], "Hybryda"),
        (["Elektryczny", "elektryczne", "Electric"], "Elektryczny"),
        (["LPG", "lpg", "benzyna+lpg"], "LPG"),
    ],
)
def test_to_samo_paliwo_dostaje_jedna_nazwe(
    warianty: list[str], kanoniczne: str
) -> None:
    assert {paliwa.kanoniczne_paliwo(w) for w in warianty} == {kanoniczne}


def test_hybryda_z_gniazdka_zostaje_osobno() -> None:
    """Inaczej się jej używa i inaczej wycenia, więc nie zlewamy jej
    ze zwykłą hybrydą — scalanie ma mieć granicę."""
    assert paliwa.kanoniczne_paliwo("PHEV") == "Hybryda plug-in"
    assert paliwa.kanoniczne_paliwo("PHEV") != paliwa.kanoniczne_paliwo("Hybryda")


def test_ogonki_nie_rozdzielaja_paliwa() -> None:
    """`Olej napedowy` i `Olej napędowy` to jedno paliwo, a `lower()` ich
    nie zrówna — dlatego oba warianty są w słowniku."""
    assert paliwa.kanoniczne_paliwo("Olej napedowy") == paliwa.kanoniczne_paliwo(
        "Olej napędowy"
    )


def test_nieznane_paliwo_zostaje_widoczne() -> None:
    """Nieznana nazwa to nadal informacja — tylko nie umiemy jej scalić.

    Zwrócenie `None` skasowałoby dane; pokazanie osobno jest sygnałem, żeby
    dopisać ją do słownika.
    """
    assert paliwa.kanoniczne_paliwo("wodór") == "Wodór"
    assert paliwa.kanoniczne_paliwo("WODÓR") == "Wodór"


def test_pusta_wartosc_nie_wybucha() -> None:
    assert paliwa.kanoniczne_paliwo("") == ""
    assert paliwa.kanoniczne_paliwo("  ") == ""


def test_migracja_i_kod_maja_ten_sam_slownik() -> None:
    """Migracja `007_paliwa.sql` POWTARZA słownik za kodem Pythona.

    SQL nie zawoła funkcji z `paliwa.py`, więc powtórzenie jest konieczne —
    ale rozjazd byłby cichy, bo obie strony nadal by działały. Ten test jest
    jedynym miejscem, w którym boli od razu.
    """
    sql = (
        pathlib.Path(__file__).resolve().parents[2] / "app/migrations/007_paliwa.sql"
    ).read_text(encoding="utf-8")
    z_sql = dict(re.findall(r"\('([^']+)',\s*'([^']+)'\)", sql))
    assert z_sql == paliwa.SLOWNIK, "słownik paliw rozjechał się z kodem"
