"""Eksport listy do CSV (SPEC.md §12, §14 pkt 11).

Czysta zamiana modelu odczytu na wiersze — bez bazy i bez HTTP, więc daje
się sprawdzić tabelą przypadków.

TRZY DECYZJE, KTÓRE WYGLĄDAJĄ NA DROBIAZGI, A DECYDUJĄ O UŻYTECZNOŚCI PLIKU:

- **Średnik, nie przecinek.** Excel w polskiej lokalizacji dzieli kolumny po
  średniku; przecinek daje jedną kolumnę z całym wierszem w środku.
- **Przecinek dziesiętny.** `48600.00` w polskim Excelu jest tekstem, nie
  liczbą — nie da się z tego zrobić sumy ani wykresu.
- **BOM na początku.** Bez niego Excel czyta UTF-8 jako Windows-1250
  i „Škoda" zamienia się w krzaki.

Czas podajemy w strefie lokalnej, bo plik ogląda człowiek, a nie maszyna
(SPEC.md §8.2 dotyczy zapisu w bazie, nie eksportu do arkusza).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Iterable, Iterator

from app.application.read_models import PozycjaListy
from app.interfaces.web.filtry_szablonu import STREFA

BOM = "﻿"
SEPARATOR = ";"

NAGLOWKI = (
    "Źródło",
    "Identyfikator",
    "Marka",
    "Model",
    "Wersja",
    "Rodzaj",
    "Rocznik",
    "Przebieg [km]",
    "Paliwo",
    "Skrzynia",
    "Lokalizacja",
    "Cena wywoławcza",
    "Cena bieżąca",
    "Waluta",
    "Ofert",
    "Koniec",
    "Status",
    "Stan ceny",
    "Obserwowana",
    "Adres",
)


def _kwota(wartosc: object) -> str:
    """Liczba z przecinkiem dziesiętnym; pusto, gdy jej nie ma."""
    if wartosc is None:
        return ""
    return str(wartosc).replace(".", ",")


def _czas(wartosc: dt.datetime | None) -> str:
    if wartosc is None:
        return ""
    return wartosc.astimezone(STREFA).strftime("%Y-%m-%d %H:%M:%S")


def wiersz(pozycja: PozycjaListy) -> tuple[str, ...]:
    """Jedna aukcja jako krotka pól — w kolejności `NAGLOWKI`."""
    return (
        pozycja.source_key,
        pozycja.external_id,
        pozycja.make or "",
        pozycja.model or "",
        pozycja.variant or "",
        pozycja.vehicle_kind.value,
        str(pozycja.year) if pozycja.year else "",
        str(pozycja.mileage_km) if pozycja.mileage_km else "",
        pozycja.fuel or "",
        pozycja.gearbox or "",
        pozycja.location or "",
        _kwota(pozycja.price_start.amount if pozycja.price_start else None),
        _kwota(pozycja.price_current.amount if pozycja.price_current else None),
        pozycja.price_current.currency.value if pozycja.price_current else "",
        str(pozycja.bid_count) if pozycja.bid_count is not None else "",
        _czas(pozycja.ends_at),
        pozycja.status.value,
        pozycja.final_price_state.value,
        "tak" if pozycja.obserwowana else "nie",
        pozycja.url,
    )


def _linia(pola: tuple[str, ...]) -> str:
    """Jeden wiersz CSV jako napis, z regułami cytowania z `csv`."""
    bufor = io.StringIO()
    csv.writer(bufor, delimiter=SEPARATOR, lineterminator="\r\n").writerow(pola)
    return bufor.getvalue()


def naglowek() -> str:
    """Pierwsza linia pliku — z BOM-em, inaczej Excel zrobi z „Škody" krzaki."""
    return BOM + _linia(NAGLOWKI)


def linia_pozycji(pozycja: PozycjaListy) -> str:
    return _linia(wiersz(pozycja))


def na_csv(pozycje: Iterable[PozycjaListy]) -> Iterator[str]:
    """Cały plik jako strumień linii — nagłówek, potem wiersze.

    Generator, a nie jeden napis: eksport całej bazy ma nie budować
    kilkumegabajtowego łańcucha w pamięci procesu, któremu §1.1 daje
    170 MB RSS na wszystko.
    """
    yield naglowek()
    for pozycja in pozycje:
        yield linia_pozycji(pozycja)


def nazwa_pliku(teraz: dt.datetime) -> str:
    """Nazwa z datą — plik eksportu jest zdjęciem stanu, nie dokumentem."""
    return f"aukcje-{teraz.astimezone(STREFA):%Y%m%d-%H%M}.csv"
