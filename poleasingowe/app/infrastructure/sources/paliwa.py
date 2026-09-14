"""Ujednolicenie rodzaju paliwa (SPEC.md §6.2) — **zamknięta lista**.

Serwisy używają tu **różnych słów**, nie tylko różnej wielkości liter:
`Olej napędowy`, `Olej napedowy`, `Diesel`, `ON`; `Hybryda`, `Hybryda/benzyna`,
`Hybryda plug-in`, `PHEV`, `MHEV`; `LPG`, `Benzyna+LPG`, `CNG`, `Gaz`.
Słownik dokładnych dopasowań (wersje do 0.29.13) przepuszczał każdy nowy
wariant jako osobną pozycję filtra — w danych na HA skończyło się kilkoma
rodzajami hybryd i wartościami, które paliwem nie są wcale.

Od 0.29.14 filtr paliwa to **zamknięta lista sześciu wartości**
(decyzja właściciela repo, 2026-09-14):

    Benzyna · Diesel · Hybryda · Elektryczny · Wodór · Gaz

Rozpoznanie idzie po słowach kluczowych, nie po pełnym napisie — więc
`Hybryda plug-in (PHEV)`, `Benzyna + LPG`, `Olej napędowy (Diesel)` trafiają
tam, gdzie trzeba, bez dopisywania każdego wariantu z osobna. Kolejność
wzorców ma znaczenie: „hybryda/benzyna" ma być hybrydą, „benzyna+LPG" —
gazem, więc te dwa sprawdzamy PRZED benzyną.

**Nieznana wartość daje `None`, nie napis.** To zmiana wobec poprzedniej
polityki „nieznane zostaje widoczne": filtr z zamkniętą listą nie może
rosnąć od danych, a wartość, której nie umiemy przypisać do żadnej
z sześciu, to z definicji nie paliwo (albo śmieć w źródle). Sygnałem, że
trzeba dopisać wzorzec, jest test, nie pozycja w filtrze.

Migracja `023` powtarza te wzorce w SQL dla wierszy już zebranych — także
zakończonych, których przemiat już nie dotknie, a to one niosą ceny końcowe.
Rozjazdu między SQL a Pythonem pilnuje `test_paliwa.py`.
"""

from __future__ import annotations

import re

BENZYNA = "Benzyna"
DIESEL = "Diesel"
HYBRYDA = "Hybryda"
ELEKTRYCZNY = "Elektryczny"
WODOR = "Wodór"
GAZ = "Gaz"

KANONICZNE: tuple[str, ...] = (BENZYNA, DIESEL, HYBRYDA, ELEKTRYCZNY, WODOR, GAZ)

# (wzorzec, paliwo) — pierwszy pasujący wygrywa. Wzorce są bez ogonków
# i bez wielkości liter: wejście jest wcześniej sprowadzane do tej postaci.
# `\b` to granica słowa — `ev` nie ma łapać `diesel` ani `benzyna`.
# Ta sama lista stoi w `023_paliwa_i_skrzynie.sql` (POSIX: `\y` zamiast `\b`).
WZORCE: tuple[tuple[str, str], ...] = (
    # Wodór przed hybrydą: ogniwo paliwowe (FCEV) to nie hybryda.
    (r"wodor|hydrogen|fcev", WODOR),
    # Hybryda przed benzyną i dieslem: „hybryda/benzyna" to hybryda.
    # Wszystkie odmiany (HEV, MHEV, PHEV, plug-in) to jedna pozycja filtra.
    (r"hybr|\bphev\b|\bmhev\b|\bhev\b|plug-?in", HYBRYDA),
    # Gaz przed benzyną: instalacja jest dodatkiem do benzyny, ale auto
    # z LPG ma stać w „Gaz", nie w „Benzyna". `\bgas\b` nie łapie `gasoline`.
    (r"\blpg\b|\bcng\b|\blng\b|\bgaz\b|\bgas\b|instalacj", GAZ),
    (r"elektr|electric|\bev\b|\bbev\b", ELEKTRYCZNY),
    (r"diesel|olej|napedow|\bon\b|\btdi\b|\bhdi\b|\bcrdi\b|\bdci\b", DIESEL),
    (r"benz|petrol|gasoline|etylin|\bpb\b", BENZYNA),
)

_BEZ_OGONKOW = str.maketrans("ąćęłńóśżź", "acelnoszz")
_SKOMPILOWANE = tuple((re.compile(w), paliwo) for w, paliwo in WZORCE)


def kanoniczne_paliwo(wartosc: str) -> str | None:
    """Jedna z sześciu nazw albo `None`, gdy napis nie opisuje paliwa."""
    klucz = " ".join(wartosc.split()).lower().translate(_BEZ_OGONKOW)
    if not klucz:
        return None
    for wzorzec, paliwo in _SKOMPILOWANE:
        if wzorzec.search(klucz):
            return paliwo
    return None
