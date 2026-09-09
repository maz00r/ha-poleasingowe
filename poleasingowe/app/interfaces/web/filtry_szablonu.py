"""Formatowanie dla szablonów (SPEC.md §8.2).

Konwersja do strefy lokalnej należy **wyłącznie** do warstwy widoku. Baza
i cała reszta kodu pracują w UTC; tutaj i tylko tutaj czas staje się czasem
warszawskim.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from urllib.parse import urlencode
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


def kwota_w_polu(wartosc: Money | None) -> str:
    """Kwota do pola formularza: bez waluty, bez zbędnych zer.

    `kwota` formatuje do czytania (`60 000,00 PLN`); do edycji trzeba czegoś,
    co po zapisaniu bez zmian wróci tą samą wartością. `60000.00` w polu
    wygląda jak literówka, a `Decimal.normalize()` samo w sobie daje
    `6E+4` — stąd jawne formatowanie.
    """
    if wartosc is None:
        return ""
    return f"{wartosc.amount.normalize():f}"


def parametry_url(wartosc: object) -> str:
    """Query string z filtrów — **z powtórzonymi kluczami**.

    Wbudowany `urlencode` Jinjy dostaje słownik i bierze z niego jedną
    wartość na klucz, a filtry wielokrotnego wyboru powtarzają klucz
    (`marka=Audi&marka=BMW`). Przyjmujemy oba kształty, bo zapisane filtry
    leżą w bazie jako obiekt JSON, a bieżące kryteria przychodzą jako lista
    par — i jedne, i drugie muszą dać ten sam adres.
    """
    pary: list[tuple[str, str]] = []
    if isinstance(wartosc, Mapping):
        for klucz, surowa in wartosc.items():
            wartosci = surowa if isinstance(surowa, list | tuple) else [surowa]
            pary.extend((str(klucz), str(w)) for w in wartosci if w is not None)
    elif isinstance(wartosc, Sequence) and not isinstance(wartosc, str):
        pary.extend((str(k), str(w)) for k, w in wartosc)
    return urlencode(pary)


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


def czas_trwania(sekundy: int | None) -> str:
    """Odstęp czasu w postaci czytelnej na karcie aukcji.

    Inna funkcja niż `do_konca`, mimo podobnego wyniku: tam liczymy różnicę
    dwóch dat i „0" znaczy „po terminie", tu dostajemy gotową liczbę sekund,
    a zero jest sensowną odpowiedzią — zobaczyliśmy ofertę w tej samej
    sekundzie, w której padła.
    """
    if sekundy is None:
        return NIEZNANE
    if sekundy < 60:
        return f"{sekundy}{NBSP}s"
    minuty, reszta = divmod(sekundy, 60)
    if minuty < 60:
        return f"{minuty}{NBSP}min {reszta}{NBSP}s"
    godziny, reszta_min = divmod(minuty, 60)
    if godziny < 24:
        return f"{godziny}{NBSP}h {reszta_min}{NBSP}min"
    dni, reszta_godz = divmod(godziny, 24)
    return f"{dni}{NBSP}d {reszta_godz}{NBSP}h"


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
    filtry["kwota_w_polu"] = kwota_w_polu
    filtry["parametry_url"] = parametry_url
    filtry["liczba"] = liczba
    filtry["bajty"] = bajty
    filtry["do_konca"] = do_konca
    filtry["pilnosc"] = pilnosc
    filtry["czas_trwania"] = czas_trwania
