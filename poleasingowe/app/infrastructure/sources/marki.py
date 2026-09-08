"""Rozpoznawanie marki i modelu ze sklejonej nazwy pojazdu.

Wspólne dla adapterów, bo to wiedza o **samochodach**, a nie o serwisie.
Dziwactwa poszczególnych serwisów zostają w ich własnych mapperach
(SPEC.md §6.2); tutaj jest tylko to, co dla wszystkich znaczy to samo.
"""

from __future__ import annotations

import re

# Marki dwuwyrazowe. Pole "Marka, Model, Wersja wyposazenia" sklada je bez
# separatora, ktoremu mozna zaufac: w probce fixtures tylko CZESC wartosci ma
# podwojna spacje miedzy czlonami, reszta ma pojedyncza. Dlatego marke
# rozpoznajemy po pierwszym tokenie, a te kilka marek — po dwoch.
# Lista jest jawnie niepelna i ma rosnac, gdy pojawi sie kolejna.
MARKI_DWUWYRAZOWE = frozenset({"land rover", "alfa romeo", "aston martin"})


# Marki pisane WERSALIKAMI z zasady — to skróty, nie nazwy własne.
# `initcap` zrobiłby z nich „Bmw" i „Man", co wygląda na literówkę.
AKRONIMY = frozenset(
    {"BMW", "MAN", "DAF", "MG", "DS", "SEAT", "KTM", "JCB", "BYD", "FAW", "GMC", "RAM"}
)

# Aliasy: **jawna lista**, nie heurystyka. Każdy wpis to decyzja, że dwie
# nazwy oznaczają tę samą markę — a tego nie da się wyliczyć z kształtu
# napisu. Klucze podajemy w wariancie bez znaków diakrytycznych i z nimi,
# bo serwisy piszą raz tak, raz tak, a rozszerzenia `unaccent` nie mamy
# (SPEC.md §8.3 zabrania rozszerzeń).
#
# Lista ma rosnąć, gdy w danych pojawi się kolejna para. To jest jej
# normalny tryb życia, a nie oznaka niedoróbki.
ALIASY = {
    "vw": "Volkswagen",
    "mercedes": "Mercedes-Benz",
    "skoda": "Škoda",
    "škoda": "Škoda",
    "citroen": "Citroën",
    "citroën": "Citroën",
}


def _tytulem(wartosc: str) -> str:
    """Title case, ale z zachowaniem myślników: `MERCEDES-BENZ` → `Mercedes-Benz`."""
    return re.sub(
        r"[^\W\d_]+",
        lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(),
        wartosc,
    )


def kanoniczna_marka(wartosc: str) -> str:
    """Jedna postać marki niezależnie od tego, jak zapisał ją serwis.

    Problem jest realny i widać go w danych: poleasingowe i autoprzetarg
    piszą `TESLA` i `VOLKSWAGEN`, EFL — `Audi`. Bez ujednolicenia lista
    rozwijana marek ma po dwa wpisy na markę, filtr po jednej z nich gubi
    połowę wyników, a `v_market_stats` liczy dwie osobne mediany dla tego
    samego modelu.

    Ujednolicamy **przy zapisie**, w warstwie antykorupcyjnej: sposób
    zapisu jest dziwactwem serwisu i nie ma prawa wyciekać dalej
    (SPEC.md §6.2).
    """
    oczyszczona = re.sub(r"\s+", " ", wartosc).strip()
    if not oczyszczona:
        return oczyszczona
    if (alias := ALIASY.get(oczyszczona.lower())) is not None:
        return alias
    if oczyszczona.upper() in AKRONIMY:
        return oczyszczona.upper()
    return _tytulem(oczyszczona)


def podziel_marke_model(wartosc: str) -> tuple[str | None, str | None, str | None]:
    """Rozdziela „Marka Model Wersja" na trzy części.

    **To jest heurystyka, nie parsowanie.** Serwis nie oddziela tych pól
    niczym, czemu można zaufać, więc bierzemy pierwszy token jako markę
    (albo dwa, gdy pasuje do `MARKI_DWUWYRAZOWE`), drugi jako model, resztę
    jako wersję. Znany sposób, w jaki to zawiedzie: nieznana marka
    dwuwyrazowa trafi w markę i model rozłącznie.
    """
    tokeny = wartosc.split()
    if not tokeny:
        return None, None, None
    if len(tokeny) >= 2 and f"{tokeny[0]} {tokeny[1]}".lower() in MARKI_DWUWYRAZOWE:
        marka = kanoniczna_marka(f"{tokeny[0]} {tokeny[1]}")
        reszta = tokeny[2:]
    else:
        marka = kanoniczna_marka(tokeny[0])
        reszta = tokeny[1:]
    model = reszta[0] if reszta else None
    wersja = " ".join(reszta[1:]) if len(reszta) > 1 else None
    return marka, model, wersja
