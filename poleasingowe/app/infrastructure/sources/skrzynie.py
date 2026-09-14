"""Ujednolicenie skrzyni biegów (SPEC.md §6.2) — **zamknięta lista**.

Do 0.29.13 `gearbox` szedł do bazy tak, jak podał go serwis, więc filtr
miał obok siebie `Automatyczna` i `Automat` (a potencjalnie `automatic`,
`DSG`, `A/T`). To ten sam problem, który `paliwa.py` rozwiązuje dla paliwa,
z tą samą odpowiedzią: dwie wartości i słowa kluczowe, nie słownik napisów.

    Automatyczna · Manualna

Nieznana wartość daje `None` — patrz uzasadnienie w `paliwa.py`.
Ta sama lista stoi w `023_paliwa_i_skrzynie.sql`; pilnuje tego
`test_skrzynie.py`.
"""

from __future__ import annotations

import re

AUTOMATYCZNA = "Automatyczna"
MANUALNA = "Manualna"

KANONICZNE: tuple[str, ...] = (AUTOMATYCZNA, MANUALNA)

# Automat przed manualem: „zautomatyzowana manualna" (AMT) jest dla
# kierowcy automatem — nie ma pedału sprzęgła.
WZORCE: tuple[tuple[str, str], ...] = (
    (
        r"autom|\bdsg\b|\bcvt\b|\bamt\b|\bat\b|\ba/t\b|tiptronic|multitronic|"
        r"steptronic|s[- ]?tronic|powershift|\bedc\b|\bdct\b|bezstopniow",
        AUTOMATYCZNA,
    ),
    (r"manual|reczn|\bmt\b|\bm/t\b|mechaniczn", MANUALNA),
)

_BEZ_OGONKOW = str.maketrans("ąćęłńóśżź", "acelnoszz")
_SKOMPILOWANE = tuple((re.compile(w), skrzynia) for w, skrzynia in WZORCE)


def kanoniczna_skrzynia(wartosc: str) -> str | None:
    """`Automatyczna`, `Manualna` albo `None`, gdy napis nie opisuje skrzyni."""
    klucz = " ".join(wartosc.split()).lower().translate(_BEZ_OGONKOW)
    if not klucz:
        return None
    for wzorzec, skrzynia in _SKOMPILOWANE:
        if wzorzec.search(klucz):
            return skrzynia
    return None
