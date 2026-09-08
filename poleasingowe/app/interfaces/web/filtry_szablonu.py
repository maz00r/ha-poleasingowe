"""Formatowanie dla szablonów (SPEC.md §8.2).

Konwersja do strefy lokalnej należy **wyłącznie** do warstwy widoku. Baza
i cała reszta kodu pracują w UTC; tutaj i tylko tutaj czas staje się czasem
warszawskim.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.domain.value_objects import Money

STREFA = ZoneInfo("Europe/Warsaw")
NIEZNANE = "—"
"""Jeden znak na brak danych. „0" albo pusta komórka kłamią inaczej."""

NBSP = "\u00a0"
"""Spacja nierozdzielająca. Jawnie przez kod ucieczki, bo w źródle jest
nieodróżnialna od zwykłej spacji — i ruff słusznie się na to skarży."""


def czas_lokalny(wartosc: dt.datetime | None, *, z_sekundami: bool = False) -> str:
    if wartosc is None:
        return NIEZNANE
    lokalny = wartosc.astimezone(STREFA)
    wzorzec = "%Y-%m-%d %H:%M:%S" if z_sekundami else "%Y-%m-%d %H:%M"
    return lokalny.strftime(wzorzec)


def kwota(wartosc: Money | None) -> str:
    if wartosc is None:
        return NIEZNANE
    # Spacja nierozdzielająca jako separator tysięcy — tak samo, jak podają
    # to serwisy (RECON.md §4.1), i tak, żeby kwota nie łamała się w pół.
    calosc = f"{wartosc.amount:,.2f}".replace(",", NBSP).replace(".", ",")
    return f"{calosc}{NBSP}{wartosc.currency.value}"


def liczba(wartosc: int | None) -> str:
    if wartosc is None:
        return NIEZNANE
    return f"{wartosc:,}".replace(",", NBSP)


def bajty(wartosc: int | None) -> str:
    if wartosc is None:
        return NIEZNANE
    jednostki = ("B", "kB", "MB", "GB", "TB")
    rozmiar = float(wartosc)
    for jednostka in jednostki:
        if rozmiar < 1024 or jednostka == jednostki[-1]:
            return f"{rozmiar:.1f}{NBSP}{jednostka}".replace(".", ",")
        rozmiar /= 1024
    return NIEZNANE  # pragma: no cover — pętla zawsze kończy się wcześniej


def do_konca(ends_at: dt.datetime | None, teraz: dt.datetime) -> str:
    """Ile zostało, w postaci, którą da się przeczytać jednym spojrzeniem."""
    if ends_at is None:
        return NIEZNANE
    pozostalo = ends_at - teraz
    sekundy = int(pozostalo.total_seconds())
    if sekundy <= 0:
        return "po terminie"
    dni, reszta = divmod(sekundy, 86_400)
    godziny, reszta = divmod(reszta, 3_600)
    minuty = reszta // 60
    if dni:
        return f"{dni}{NBSP}d {godziny}{NBSP}h"
    if godziny:
        return f"{godziny}{NBSP}h {minuty}{NBSP}min"
    return f"{minuty}{NBSP}min"


def pilnosc(ends_at: dt.datetime | None, teraz: dt.datetime) -> str:
    """Klasa CSS zależna od tego, jak blisko końca jest aukcja.

    Kolor niesie tu informację operacyjną, nie ozdobę: w liście
    kilkudziesięciu pozycji to, że coś kończy się za kwadrans, musi być
    widoczne bez czytania kolumny z datą.
    """
    if ends_at is None:
        return ""
    zostalo = (ends_at - teraz).total_seconds()
    if zostalo <= 0:
        return "po-terminie"
    if zostalo <= 15 * 60:
        return "konczy-sie"
    if zostalo <= 6 * 3600:
        return "dzis"
    return ""


def zarejestruj(srodowisko: object) -> None:
    """Podpina filtry pod środowisko Jinja2."""
    filtry = getattr(srodowisko, "filters")  # noqa: B009 — Jinja2 API
    filtry["czas_lokalny"] = czas_lokalny
    filtry["kwota"] = kwota
    filtry["liczba"] = liczba
    filtry["bajty"] = bajty
    filtry["do_konca"] = do_konca
    filtry["pilnosc"] = pilnosc
