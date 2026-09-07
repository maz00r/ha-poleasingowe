"""Rozpoznawanie marki i modelu ze sklejonej nazwy pojazdu.

Wspólne dla adapterów, bo to wiedza o **samochodach**, a nie o serwisie.
Dziwactwa poszczególnych serwisów zostają w ich własnych mapperach
(SPEC.md §6.2); tutaj jest tylko to, co dla wszystkich znaczy to samo.
"""

from __future__ import annotations

# Marki dwuwyrazowe. Pole "Marka, Model, Wersja wyposazenia" sklada je bez
# separatora, ktoremu mozna zaufac: w probce fixtures tylko CZESC wartosci ma
# podwojna spacje miedzy czlonami, reszta ma pojedyncza. Dlatego marke
# rozpoznajemy po pierwszym tokenie, a te kilka marek — po dwoch.
# Lista jest jawnie niepelna i ma rosnac, gdy pojawi sie kolejna.
MARKI_DWUWYRAZOWE = frozenset({"land rover", "alfa romeo", "aston martin"})


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
        marka = f"{tokeny[0]} {tokeny[1]}"
        reszta = tokeny[2:]
    else:
        marka = tokeny[0]
        reszta = tokeny[1:]
    model = reszta[0] if reszta else None
    wersja = " ".join(reszta[1:]) if len(reszta) > 1 else None
    return marka, model, wersja
