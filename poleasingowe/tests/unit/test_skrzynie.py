"""Ujednolicenie skrzyni biegów — dwie wartości (SPEC.md §6.2).

Na HA filtr miał obok siebie `Automatyczna` i `Automat` (2026-09-14).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.infrastructure.sources import skrzynie

MIGRACJA = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app/migrations/023_paliwa_i_skrzynie.sql"
)


@pytest.mark.parametrize(
    "warianty,kanoniczne",
    [
        (
            [
                "Automatyczna",
                "Automat",
                "automatyczna",
                "Automatic",
                "DSG",
                "CVT",
                "A/T",
                "AT",
                "Tiptronic",
                "S tronic",
                "bezstopniowa",
                # AMT: dla kierowcy automat — bez pedału sprzęgła.
                "zautomatyzowana manualna",
            ],
            "Automatyczna",
        ),
        (
            ["Manualna", "manual", "Ręczna", "reczna", "M/T", "MT", "mechaniczna"],
            "Manualna",
        ),
    ],
)
def test_ta_sama_skrzynia_dostaje_jedna_nazwe(
    warianty: list[str], kanoniczne: str
) -> None:
    assert {skrzynie.kanoniczna_skrzynia(w) for w in warianty} == {kanoniczne}


def test_nieznana_wartosc_daje_none() -> None:
    assert skrzynie.kanoniczna_skrzynia("brak danych") is None
    assert skrzynie.kanoniczna_skrzynia("") is None
    assert skrzynie.kanoniczna_skrzynia("Automatyczna") in skrzynie.KANONICZNE


def test_migracja_i_kod_maja_te_same_wzorce() -> None:
    sql = MIGRACJA.read_text(encoding="utf-8")
    blok = sql.split("SET gearbox = CASE", 1)[1].split("END", 1)[0]
    z_sql = [
        (wzorzec.replace("\\y", "\\b"), skrzynia)
        for wzorzec, skrzynia in re.findall(
            r"WHEN k ~\* '([^']+)' THEN '([^']+)'", blok
        )
    ]
    assert z_sql == list(skrzynie.WZORCE), "wzorce skrzyń rozjechały się z kodem"
