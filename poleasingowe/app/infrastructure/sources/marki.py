"""Rozpoznawanie marki i modelu ze sklejonej nazwy pojazdu.

Wspólne dla adapterów, bo to wiedza o **samochodach**, a nie o serwisie.
Dziwactwa poszczególnych serwisów zostają w ich własnych mapperach
(SPEC.md §6.2); tutaj jest tylko to, co dla wszystkich znaczy to samo.

Co widać w danych (fixtures i filtr na HA, 2026-09-14) i co ten moduł
sprowadza do jednej postaci:

- ta sama marka różną wielkością liter: `TESLA` / `Tesla`;
- ta sama marka z różnym łącznikiem: `Mercedes-Benz` / `Mercedes- Benz` /
  `MERCEDES  BENZ` — Leasygroup wstawia spację po myślniku;
- dopisek w nawiasie, który nie jest marką: `MINI [BMW] Countryman` —
  bez wycięcia `[BMW]` lądował jako **model**;
- tytuł, który nie jest nazwą pojazdu: poleasingowe.pl po zakończeniu
  zmienia tytuł na `Aukcja nr 1384/STR/AU/2026 zakończyła się …`, z czego
  pierwsze słowo szło do bazy jako marka `Aukcja`.
"""

from __future__ import annotations

import re

# Marki dwuwyrazowe. Pole "Marka, Model, Wersja wyposazenia" sklada je bez
# separatora, ktoremu mozna zaufac: w probce fixtures tylko CZESC wartosci ma
# podwojna spacje miedzy czlonami, reszta ma pojedyncza. Dlatego marke
# rozpoznajemy po pierwszym tokenie, a te kilka marek — po dwoch.
# Lista jest jawnie niepelna i ma rosnac, gdy pojawi sie kolejna.
MARKI_DWUWYRAZOWE = frozenset(
    {
        "land rover",
        "alfa romeo",
        "aston martin",
        "mercedes benz",
        "mercedes-benz",
        "ds automobiles",
    }
)


# Marki pisane WERSALIKAMI z zasady — to skróty albo znaki, nie nazwy własne.
# `initcap` zrobiłby z nich „Bmw", „Man", „Mini", co wygląda na literówkę.
AKRONIMY = frozenset(
    {
        "BMW",
        "MAN",
        "DAF",
        "MG",
        "DS",
        "SEAT",
        "KTM",
        "JCB",
        "BYD",
        "FAW",
        "GMC",
        "RAM",
        "MINI",
    }
)

# Aliasy: **jawna lista**, nie heurystyka. Każdy wpis to decyzja, że dwie
# nazwy oznaczają tę samą markę — a tego nie da się wyliczyć z kształtu
# napisu. Klucz jest w postaci po `_klucz`: małe litery, myślnik bez spacji
# wokół, bez nawiasów. Warianty z ogonkami i bez, bo serwisy piszą raz tak,
# raz tak, a rozszerzenia `unaccent` nie mamy (SPEC.md §8.3 zabrania
# rozszerzeń).
#
# Lista ma rosnąć, gdy w danych pojawi się kolejna para. To jest jej
# normalny tryb życia, a nie oznaka niedoróbki.
ALIASY = {
    "vw": "Volkswagen",
    "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz",
    "skoda": "Škoda",
    "škoda": "Škoda",
    "citroen": "Citroën",
    "citroën": "Citroën",
    "landrover": "Land Rover",
    "land-rover": "Land Rover",
    "alfa": "Alfa Romeo",
    "alfa-romeo": "Alfa Romeo",
    "ds automobiles": "DS",
    "mini cooper": "MINI",
}

# Pierwsze słowo tytułu, które NIE jest marką — tytuł opisuje aukcję, nie
# pojazd. Taki napis nie daje marki, modelu ani wersji.
NIE_MARKA = frozenset({"aukcja", "licytacja", "oferta", "sprzedaz", "sprzedaż"})

_NAWIAS = re.compile(r"\s*[\[(][^\])]*[\])]\s*")
_LACZNIK = re.compile(r"\s*-\s*")


def _klucz(wartosc: str) -> str:
    """Postać do porównań: bez nawiasów, myślnik bez spacji, jedna spacja."""
    bez_nawiasow = _NAWIAS.sub(" ", wartosc)
    return " ".join(_LACZNIK.sub("-", bez_nawiasow).split()).lower()


def _tytulem(wartosc: str) -> str:
    """Title case, ale z zachowaniem myślników: `MERCEDES-BENZ` → `Mercedes-Benz`."""
    return re.sub(
        r"[^\W\d_]+",
        lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(),
        wartosc,
    )


def kanoniczna_marka(wartosc: str) -> str | None:
    """Jedna postać marki niezależnie od tego, jak zapisał ją serwis.

    Problem jest realny i widać go w danych: poleasingowe i autoprzetarg
    piszą `TESLA` i `VOLKSWAGEN`, EFL — `Audi`, Leasygroup `Mercedes- Benz`.
    Bez ujednolicenia lista rozwijana marek ma po dwa wpisy na markę, filtr
    po jednej z nich gubi połowę wyników, a `v_market_stats` liczy dwie
    osobne mediany dla tego samego modelu.

    Ujednolicamy **przy zapisie**, w warstwie antykorupcyjnej: sposób
    zapisu jest dziwactwem serwisu i nie ma prawa wyciekać dalej
    (SPEC.md §6.2). `None` — gdy napis nie jest marką (pusty albo z
    `NIE_MARKA`).
    """
    klucz = _klucz(wartosc)
    if not klucz or klucz.split()[0] in NIE_MARKA:
        return None
    if (alias := ALIASY.get(klucz)) is not None:
        return alias
    if klucz.upper() in AKRONIMY:
        return klucz.upper()
    return _tytulem(klucz)


def podziel_marke_model(wartosc: str) -> tuple[str | None, str | None, str | None]:
    """Rozdziela „Marka Model Wersja" na trzy części.

    **To jest heurystyka, nie parsowanie.** Serwis nie oddziela tych pól
    niczym, czemu można zaufać, więc bierzemy pierwszy token jako markę
    (albo dwa, gdy pasuje do `MARKI_DWUWYRAZOWE`), drugi jako model, resztę
    jako wersję. Znany sposób, w jaki to zawiedzie: nieznana marka
    dwuwyrazowa trafi w markę i model rozłącznie.

    Nawiasy wycinamy z całości: `MINI [BMW] Countryman Cooper S` ma dać
    `MINI` / `Countryman` / `Cooper S`, nie `Mini` / `[BMW]` / `Countryman…`.
    Tytuł niebędący nazwą pojazdu (`Aukcja nr …`) daje same `None`.
    """
    tokeny = _NAWIAS.sub(" ", wartosc).split()
    if not tokeny or tokeny[0].lower() in NIE_MARKA:
        return None, None, None
    if len(tokeny) >= 2 and _klucz(f"{tokeny[0]} {tokeny[1]}") in MARKI_DWUWYRAZOWE:
        marka = kanoniczna_marka(f"{tokeny[0]} {tokeny[1]}")
        reszta = tokeny[2:]
    else:
        marka = kanoniczna_marka(tokeny[0])
        reszta = tokeny[1:]
    model = reszta[0] if reszta else None
    wersja = " ".join(reszta[1:]) if len(reszta) > 1 else None
    return marka, model, wersja
