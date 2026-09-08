"""Ujednolicenie rodzaju paliwa (SPEC.md §6.2).

Ten sam problem co z markami, tylko ostrzejszy: serwisy używają **różnych
słów**, nie tylko różnej wielkości liter. Zmierzone w danych:

| Serwis | Co pisze |
|---|---|
| poleasingowe.pl | `Benzyna`, `Hybryda/benzyna`, `Olej napedowy` (bez ogonka) |
| autoprzetarg.pl | `Benzyna`, `Diesel`, `Elektryczny` |
| aukcje.efl.com.pl | `Hybryda`, `Olej napędowy` (z ogonkiem) |

Czyli jedno paliwo pod trzema nazwami (`Olej napedowy`, `Olej napędowy`,
`Diesel`) i hybryda pod dwiema. Lista rozwijana w filtrach miała przez to
pięć pozycji na trzy paliwa, a filtr po którejkolwiek gubił wyniki
z pozostałych serwisów.

**Słownik jest jawny, nie heurystyczny.** Że `Olej napędowy` to `Diesel`,
wie człowiek, a nie algorytm — z kształtu napisu tego nie widać. Lista ma
rosnąć, gdy w danych pojawi się kolejna nazwa; to jej normalny tryb życia.

Klucze podajemy w obu wariantach zapisu, z ogonkami i bez, bo `lower()` ich
nie zrówna, a rozszerzenia `unaccent` nie mamy (SPEC.md §8.3 zabrania
rozszerzeń).
"""

from __future__ import annotations

import re

SLOWNIK: dict[str, str] = {
    # Diesel
    "olej napedowy": "Diesel",
    "olej napędowy": "Diesel",
    "on": "Diesel",
    "diesel": "Diesel",
    # Benzyna
    "benzyna": "Benzyna",
    "petrol": "Benzyna",
    "pb": "Benzyna",
    # Hybrydy. Scalamy je świadomie: EFL pisze samo `Hybryda`, a poleasingowe
    # `Hybryda/benzyna` — najczęściej o tym samym rodzaju auta. Trzymanie ich
    # osobno gwarantowałoby duplikat między serwisami, a rodzaj silnika
    # i tak widać w danych technicznych aukcji.
    "hybryda": "Hybryda",
    "hybryda/benzyna": "Hybryda",
    "hybryda/olej napedowy": "Hybryda",
    "hybryda/olej napędowy": "Hybryda",
    "hybrid": "Hybryda",
    # Hybryda ładowana z gniazdka to osobna kategoria — inaczej się jej
    # używa i inaczej wycenia, więc nie zlewamy jej ze zwykłą hybrydą.
    "hybryda plug-in": "Hybryda plug-in",
    "plug-in": "Hybryda plug-in",
    "phev": "Hybryda plug-in",
    # Elektryczne
    "elektryczny": "Elektryczny",
    "elektryczne": "Elektryczny",
    "electric": "Elektryczny",
    "ev": "Elektryczny",
    # Gaz
    "lpg": "LPG",
    "benzyna+lpg": "LPG",
    "benzyna/lpg": "LPG",
    "cng": "CNG",
}


def kanoniczne_paliwo(wartosc: str) -> str:
    """Jedna nazwa paliwa niezależnie od tego, jak zapisał ją serwis.

    Nazwa spoza słownika wraca z ujednoliconą wielkością liter, a nie jako
    `None`: nieznane paliwo to nadal informacja, tylko nie umiemy jej jeszcze
    scalić z żadną inną. Pokaże się w filtrach osobno i to jest sygnał, żeby
    dopisać ją do słownika.
    """
    oczyszczone = re.sub(r"\s+", " ", wartosc).strip()
    if not oczyszczone:
        return oczyszczone
    if (kanoniczne := SLOWNIK.get(oczyszczone.lower())) is not None:
        return kanoniczne
    return oczyszczone[:1].upper() + oczyszczone[1:].lower()
