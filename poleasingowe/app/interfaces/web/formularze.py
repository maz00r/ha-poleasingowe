"""Tłumaczenie parametrów adresu na kryteria listy (SPEC.md §12).

Osobny moduł, bo to jedyna część interfejsu, którą da się testować bez bazy
i bez HTTP — a jest to część, w której najłatwiej o cichy błąd: literówka
w nazwie parametru daje filtr, który po prostu nic nie robi.

Zasada: **nic nie wybucha na złych danych z adresu**. Adres bywa sklejony
ręcznie, skopiowany w połowie albo zapamiętany sprzed zmiany. Zły parametr
ma zostać zignorowany, a nie zwrócić 500.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

from app.application.read_models import Kryteria, Kursor, Sortowanie
from app.domain.enums import AuctionStatus, RodzajPojazdu

STATUS_Z_PARAMETRU = {
    "aktywne": AuctionStatus.ACTIVE,
    "domykane": AuctionStatus.ENDING,
    "zakonczone": AuctionStatus.ENDED,
    "zniknely": AuctionStatus.DISAPPEARED,
}
"""`wszystkie` celowo nie ma tu wpisu — to brak filtra, nie status."""

PARAMETR_ZE_STATUSU = {v: k for k, v in STATUS_Z_PARAMETRU.items()}

MAKS_GODZIN = 24 * 30
"""Sufit dla „kończy się w N h" — bez niego pole staje się filtrem na wszystko."""

RODZAJ_WSZYSTKIE = "wszystkie"
"""Jawna wartość „nie filtruj po rodzaju".

Potrzebna, bo `rodzaj` jest jedynym filtrem z niepustą wartością domyślną:
brak parametru znaczy tu „osobowe", więc „wszystkie" musi dać się powiedzieć
wprost, inaczej nie dałoby się go wyłączyć."""


def _tekst(parametry: Mapping[str, str], nazwa: str) -> str | None:
    wartosc = parametry.get(nazwa, "").strip()
    return wartosc or None


def _liczba(
    parametry: Mapping[str, str], nazwa: str, *, minimum: int = 0, maksimum: int
) -> int | None:
    surowe = parametry.get(nazwa, "").strip()
    if not surowe:
        return None
    try:
        wartosc = int(surowe)
    except ValueError:
        return None
    if wartosc < minimum or wartosc > maksimum:
        return None
    return wartosc


def _flaga(parametry: Mapping[str, str], nazwa: str) -> bool:
    return parametry.get(nazwa, "").strip().lower() in {"1", "true", "tak", "on"}


def zbuduj_kryteria(
    parametry: Mapping[str, str], *, ostatnia_wizyta: dt.datetime | None = None
) -> Kryteria:
    """Składa `Kryteria` z parametrów adresu.

    `ostatnia_wizyta` przychodzi z ciasteczka i włącza się dopiero wtedy, gdy
    użytkownik poprosił o widok „nowe od ostatniej wizyty" — sama obecność
    ciasteczka niczego nie filtruje.
    """
    surowy_sort = parametry.get("sort", "").strip()
    try:
        sortowanie = Sortowanie(surowy_sort)
    except ValueError:
        sortowanie = Sortowanie.KONIEC_ROSNACO

    surowy_status = parametry.get("status", "").strip()
    if surowy_status == "wszystkie":
        status: AuctionStatus | None = None
    else:
        status = STATUS_Z_PARAMETRU.get(surowy_status, AuctionStatus.ACTIVE)

    nowe_od = ostatnia_wizyta if _flaga(parametry, "nowe") else None

    surowy_rodzaj = parametry.get("rodzaj", "").strip()
    if surowy_rodzaj == RODZAJ_WSZYSTKIE:
        rodzaj: RodzajPojazdu | None = None
    elif surowy_rodzaj:
        try:
            rodzaj = RodzajPojazdu(surowy_rodzaj.upper())
        except ValueError:
            # Śmieć w adresie nie ma prawa dać 500 ani po cichu pokazać
            # wszystkiego — wracamy do wartości domyślnej.
            rodzaj = RodzajPojazdu.OSOBOWY
    else:
        rodzaj = RodzajPojazdu.OSOBOWY

    return Kryteria(
        szukaj=_tekst(parametry, "szukaj"),
        marka=_tekst(parametry, "marka"),
        model=_tekst(parametry, "model"),
        zrodlo=_tekst(parametry, "zrodlo"),
        rodzaj=rodzaj,
        paliwo=_tekst(parametry, "paliwo"),
        skrzynia=_tekst(parametry, "skrzynia"),
        lokalizacja=_tekst(parametry, "lokalizacja"),
        cena_od=_liczba(parametry, "cena_od", maksimum=9_999_999),
        cena_do=_liczba(parametry, "cena_do", maksimum=9_999_999),
        rocznik_od=_liczba(parametry, "rocznik_od", minimum=1900, maksimum=2100),
        rocznik_do=_liczba(parametry, "rocznik_do", minimum=1900, maksimum=2100),
        przebieg_do=_liczba(parametry, "przebieg_do", maksimum=9_999_999),
        konczy_sie_w_h=_liczba(
            parametry, "do_konca_h", minimum=1, maksimum=MAKS_GODZIN
        ),
        status=status,
        # Archiwum jest historią watchlisty, nie wysypiskiem wszystkich
        # zakończonych aukcji. Jawna flaga nadal obsługuje obserwowane aktywne.
        tylko_obserwowane=(
            status is AuctionStatus.ENDED or _flaga(parametry, "obserwowane")
        ),
        nowe_od=nowe_od,
        sortowanie=sortowanie,
    )


def kursor_z_parametrow(parametry: Mapping[str, str]) -> Kursor | None:
    surowy = parametry.get("kursor", "").strip()
    return Kursor.odkoduj(surowy) if surowy else None


def na_parametry(kryteria: Kryteria) -> dict[str, str]:
    """Odwrotność `zbuduj_kryteria` — do budowania linków „dalej" i sortowania.

    Bez tego każdy link w liście gubiłby filtry: użytkownik ustawia markę,
    klika „po cenie" i dostaje całą bazę od nowa.
    """
    wynik: dict[str, str] = {}
    proste = (
        ("szukaj", kryteria.szukaj),
        ("marka", kryteria.marka),
        ("model", kryteria.model),
        ("zrodlo", kryteria.zrodlo),
        ("paliwo", kryteria.paliwo),
        ("skrzynia", kryteria.skrzynia),
        ("lokalizacja", kryteria.lokalizacja),
    )
    for nazwa, wartosc in proste:
        if wartosc:
            wynik[nazwa] = wartosc

    liczby = (
        ("cena_od", kryteria.cena_od),
        ("cena_do", kryteria.cena_do),
        ("rocznik_od", kryteria.rocznik_od),
        ("rocznik_do", kryteria.rocznik_do),
        ("przebieg_do", kryteria.przebieg_do),
        ("do_konca_h", kryteria.konczy_sie_w_h),
    )
    for nazwa, liczba in liczby:
        if liczba is not None:
            wynik[nazwa] = str(liczba)

    # Rodzaj wpisujemy ZAWSZE, także domyślny: bez tego „Wyczyść" i linki
    # nawigacji gubiłyby wybór „wszystkie rodzaje" przy pierwszym kliknięciu.
    wynik["rodzaj"] = (
        RODZAJ_WSZYSTKIE if kryteria.rodzaj is None else kryteria.rodzaj.value
    )

    if kryteria.status is None:
        wynik["status"] = "wszystkie"
    else:
        wynik["status"] = PARAMETR_ZE_STATUSU[kryteria.status]
    if kryteria.tylko_obserwowane:
        wynik["obserwowane"] = "1"
    if kryteria.nowe_od is not None:
        wynik["nowe"] = "1"
    wynik["sort"] = kryteria.sortowanie.value
    return wynik
