"""Rozpoznawanie marki i modelu (SPEC.md §6.2).

Heurystyka wspólna dla adapterów, więc i testy są wspólne — serwisy sklejają
markę z modelem tak samo bezradnie.
"""

from __future__ import annotations

import pytest

from app.infrastructure.sources import marki


@pytest.mark.parametrize(
    "wejscie,oczekiwane",
    [
        # Marka wychodzi juz w postaci kanonicznej: EFL pisze „Skoda",
        # poleasingowe „SKODA", a jedna i druga to Škoda.
        ("Skoda  Superb  Style DSG", ("Škoda", "Superb", "Style DSG")),
        (
            "Audi A3 30 TDI S tronic Hatchback",
            ("Audi", "A3", "30 TDI S tronic Hatchback"),
        ),
        ("Volkswagen Passat", ("Volkswagen", "Passat", None)),
        ("Opel", ("Opel", None, None)),
        ("", (None, None, None)),
        # Marka dwuwyrazowa rozpoznawana z listy — bez niej "Land" trafiloby
        # w marke, a "Rover" w model.
        ("Land Rover Discovery Sport", ("Land Rover", "Discovery", "Sport")),
    ],
)
def test_podzial_marki_i_modelu(
    wejscie: str, oczekiwane: tuple[str | None, str | None, str | None]
) -> None:
    assert marki.podziel_marke_model(wejscie) == oczekiwane


def test_nieznana_marka_dwuwyrazowa_to_znane_ograniczenie() -> None:
    """Udokumentowany sposób, w jaki heurystyka zawodzi.

    Serwis nie oddziela marki od modelu niczym, czemu można zaufać
    (podwójna spacja jest tylko w części wartości), więc marka spoza listy
    `MARKI_DWUWYRAZOWE` rozjedzie się na markę i model. Test pilnuje, żeby to
    zachowanie było świadome, a nie odkryte kiedyś na produkcji.
    """
    assert marki.podziel_marke_model("Great Wall Haval") == ("Great", "Wall", "Haval")
    assert "great wall" not in marki.MARKI_DWUWYRAZOWE


# --------------------------------------------------------------------------
# Ujednolicenie zapisu marki
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "warianty,kanoniczna",
    [
        (["TESLA", "tesla", "Tesla", " Tesla "], "Tesla"),
        (["AUDI", "Audi", "audi"], "Audi"),
        (["VOLKSWAGEN", "volkswagen"], "Volkswagen"),
        (["MERCEDES-BENZ", "mercedes-benz", "Mercedes-Benz"], "Mercedes-Benz"),
        (["ALFA ROMEO", "alfa romeo"], "Alfa Romeo"),
        (["PEUGEOT", "Peugeot"], "Peugeot"),
    ],
)
def test_ten_sam_producent_dostaje_jedna_postac(
    warianty: list[str], kanoniczna: str
) -> None:
    """To jest ten błąd, który widać wprost w interfejsie.

    poleasingowe i autoprzetarg piszą marki WERSALIKAMI, EFL zwykłym
    zapisem. Bez ujednolicenia lista rozwijana ma po dwa wpisy na markę,
    a filtr po jednym z nich gubi połowę wyników — porównanie jest ścisłe.
    """
    assert {marki.kanoniczna_marka(w) for w in warianty} == {kanoniczna}


@pytest.mark.parametrize("skrot", ["BMW", "MAN", "DAF", "SEAT"])
def test_skroty_zostaja_wersalikami(skrot: str) -> None:
    """`Bmw` i `Man` wyglądają na literówkę, a to są skróty, nie nazwy."""
    assert marki.kanoniczna_marka(skrot) == skrot
    assert marki.kanoniczna_marka(skrot.lower()) == skrot


@pytest.mark.parametrize(
    "wariant,kanoniczna",
    [
        ("VW", "Volkswagen"),
        ("MERCEDES", "Mercedes-Benz"),
        ("SKODA", "Škoda"),
        ("ŠKODA", "Škoda"),
        ("CITROEN", "Citroën"),
    ],
)
def test_aliasy_lacza_rozne_nazwy_tej_samej_marki(
    wariant: str, kanoniczna: str
) -> None:
    """Druga klasa duplikatów, obok wielkości liter.

    W danych z autoprzetarg stoją obok siebie `MERCEDES` i `MERCEDES-BENZ`.
    Tego nie da się wyliczyć z kształtu napisu — stąd **jawna lista**,
    a nie heurystyka.
    """
    assert marki.kanoniczna_marka(wariant) == kanoniczna


def test_znaki_diakrytyczne_nie_rozdzielaja_marki() -> None:
    """`Skoda` i `Škoda` to jedna marka, a `lower()` ich nie zrówna."""
    assert marki.kanoniczna_marka("Skoda") == marki.kanoniczna_marka("Škoda")


def test_pusta_wartosc_daje_none() -> None:
    assert marki.kanoniczna_marka("") is None
    assert marki.kanoniczna_marka("   ") is None


@pytest.mark.parametrize(
    "wariant",
    ["Mercedes-Benz", "Mercedes- Benz", "MERCEDES -BENZ", "MERCEDES  BENZ", "mercedes"],
)
def test_lacznik_ze_spacja_nie_rozdziela_marki(wariant: str) -> None:
    """Leasygroup pisze `Mercedes- Benz` — w filtrze na HA stały obok siebie."""
    assert marki.kanoniczna_marka(wariant) == "Mercedes-Benz"


def test_nawias_w_nazwie_nie_laduje_w_modelu() -> None:
    """dawro: „MINI [BMW] Countryman Cooper S ALL4" dawało `Mini` / `[BMW]`."""
    assert marki.podziel_marke_model("MINI [BMW] Countryman Cooper S ALL4") == (
        "MINI",
        "Countryman",
        "Cooper S ALL4",
    )
    assert marki.kanoniczna_marka("Mini") == "MINI"


def test_tytul_aukcji_nie_jest_marka() -> None:
    """poleasingowe.pl po zakończeniu: „Aukcja nr 1384/STR/AU/2026 zakończyła
    się …" — z tego szła do bazy marka `Aukcja`."""
    tytul = "Aukcja nr 1384/STR/AU/2026 zakończyła się 2026-09-07 12:00:00"
    assert marki.podziel_marke_model(tytul) == (None, None, None)
    assert marki.kanoniczna_marka("Aukcja") is None


def test_podzial_marki_zwraca_juz_postac_kanoniczna() -> None:
    """Ujednolicenie idzie przez warstwę antykorupcyjną, więc obejmuje
    wszystkie adaptery naraz — żaden nie musi o nim pamiętać (§6.2)."""
    assert marki.podziel_marke_model("TESLA MODEL Y")[0] == "Tesla"
    assert marki.podziel_marke_model("ALFA ROMEO TONALE")[0] == "Alfa Romeo"


def test_migracja_i_kod_maja_te_same_listy() -> None:
    """Migracja `024_marki_porzadek.sql` POWTARZA skróty i aliasy za kodem Pythona.

    Powtórzenie jest konieczne — SQL nie zawoła funkcji z `marki.py` — ale
    dwie listy rozjeżdżają się przy pierwszej dopisanej marce i nikt tego
    nie zauważy, bo obie strony nadal działają. Ten test jest jedynym
    miejscem, w którym rozjazd boli od razu.
    """
    import pathlib
    import re

    sql = (
        pathlib.Path(__file__).resolve().parents[2]
        / "app/migrations/024_marki_porzadek.sql"
    ).read_text(encoding="utf-8")

    # Pierwsza tablica to słowa-nie-marki, druga — skróty.
    tablice = re.findall(r"ARRAY\[([^\]]+)\]", sql)
    assert len(tablice) == 2
    z_sql = {s.strip().strip("'") for s in tablice[1].split(",")}
    assert z_sql == set(marki.AKRONIMY), "lista skrótów rozjechała się z kodem"

    aliasy_sql = dict(re.findall(r"\('([^']+)',\s*'([^']+)'\)", sql))
    assert aliasy_sql == marki.ALIASY, "lista aliasów rozjechała się z kodem"

    nie_marki = {s.strip().strip("'") for s in tablice[0].split(",")}
    assert nie_marki == set(marki.NIE_MARKA), "lista słów-nie-marek rozjechała się"
