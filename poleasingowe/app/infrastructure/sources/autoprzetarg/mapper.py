"""Warstwa antykorupcyjna autoprzetarg.pl (SPEC.md §6.2).

Dziwactwa serwisu żyją tutaj:

- nagłówek pojazdu skleja markę, model, rocznik i silnik **bez spacji**:
  `CITROEN JUMPER2018 / 1997,00 ccm / 131 KM`;
- pojemność i moc stoją w jednym polu, po ukośniku;
- przecinek jest separatorem dziesiętnym również w pojemności
  (`2198,00 ccm`), a nie tylko w cenie;
- `Przebieg` bywa **pusty** i to normalny stan tego serwisu.

Czego nie ma: **liczby ofert**. Serwis nie podaje jej bez zalogowania nawet
na stronie szczegółów (RECON.md §4.4), więc `bid_count` zostaje `None`.
Zero znaczyłoby „nikt nie licytował", a to co innego niż „nie wiemy".
"""

from __future__ import annotations

import datetime as dt
import re
import zoneinfo

from app.application.ports import SurowaOferta
from app.domain.entities import Auction
from app.domain.enums import AuctionStatus, Currency
from app.domain.errors import ParseFailed
from app.domain.value_objects import Mileage, Money, NieprawidlowaWartosc, Vin
from app.infrastructure.sources.marki import podziel_marke_model
from app.infrastructure.sources.paliwa import kanoniczne_paliwo

# RECON.md §4.4: strefa nie jest podana przy `auctionEndDate`. Zakładamy czas
# lokalny Polski, bo taki serwis pokazuje użytkownikowi. Do bazy idzie UTC.
STREFA_SERWISU = zoneinfo.ZoneInfo("Europe/Warsaw")

_KONIEC = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$")
_POJEMNOSC = re.compile(r"([\d\s\xa0]+(?:,\d+)?)\s*(?:ccm|cm3)", re.I)
_MOC = re.compile(r"(\d+)\s*KM", re.I)
# Wszystko od rocznika, pojemności albo paliwa w prawo to już dane techniczne,
# nie nazwa pojazdu. Serwis używa wymiennie `ccm`, `cm3` i np. `3,0 DIESEL`.
_OGON_NAZWY = re.compile(
    r"\s*\d{4}\s*/.*$|" r"\s*\d+[,.]?\d*\s*(?:ccm|cm3|diesel|benzyna|hybryda)\b.*$",
    re.I,
)


def _liczba(tekst: str | None) -> int | None:
    """Część całkowita liczby. `2198,00 ccm` → 2198, pusty tekst → `None`."""
    if not tekst:
        return None
    oczyszczony = tekst.replace("\xa0", " ").split(",")[0]
    cyfry = re.sub(r"[^\d]", "", oczyszczony)
    return int(cyfry) if cyfry else None


def _koniec_na_utc(wartosc: str) -> dt.datetime:
    dopasowanie = _KONIEC.match(wartosc.strip())
    if dopasowanie is None:
        raise ParseFailed(f"autoprzetarg: nieznany format czasu: {wartosc!r}")
    r, m, d, gg, mm, ss = (int(x) for x in dopasowanie.groups())
    return dt.datetime(r, m, d, gg, mm, ss, tzinfo=STREFA_SERWISU).astimezone(dt.UTC)


def _nazwa_pojazdu(surowa: str) -> str:
    """`CITROEN JUMPER2018 / 1997,00 ccm / 131 KM` → `CITROEN JUMPER`.

    Rocznik skleja się z modelem bez spacji, więc obcinamy ogon od miejsca,
    w którym zaczynają się dane techniczne — one i tak mają własne pola.
    """
    return _OGON_NAZWY.sub("", surowa).strip()


def _cena(pola: dict[str, str]) -> Money | None:
    surowa = pola.get("Aktualna cena aukcji")
    if not surowa:
        return None
    try:
        return Money.z_tekstu(surowa.replace("zł", "").strip(), Currency.PLN)
    except NieprawidlowaWartosc:
        return None


def _vin(pola: dict[str, str]) -> Vin | None:
    surowy = (pola.get("VIN") or "").strip()
    if not surowy:
        return None
    try:
        return Vin(surowy)
    except NieprawidlowaWartosc:
        # SPEC.md §8.4 dedupikuje po VIN, więc niechlujny VIN jest gorszy
        # niż jego brak: skleiłby ze sobą dwa różne pojazdy.
        return None


def _paliwo(wartosc: str | None) -> str | None:
    """Nazwa paliwa w postaci wspólnej dla wszystkich źródeł (§6.2)."""
    return kanoniczne_paliwo(wartosc) if wartosc else None


def na_aukcje(surowa: SurowaOferta, source_id: int, teraz: dt.datetime) -> Auction:
    """Buduje encję domenową z surowej pozycji autoprzetarg.pl."""
    pola = surowa.pola

    marka = model = wersja = None
    if nazwa := pola.get("nazwa"):
        marka, model, wersja = podziel_marke_model(_nazwa_pojazdu(nazwa))

    silnik = pola.get("Pojemność silnika") or ""
    pojemnosc = _POJEMNOSC.search(silnik)
    moc = _MOC.search(silnik)

    przebieg = None
    if km := _liczba(pola.get("Przebieg")):
        przebieg = Mileage(km)

    ends_at = None
    if koniec := pola.get("end_date"):
        ends_at = _koniec_na_utc(koniec)

    # Aukcja po terminie przestaje istnieć pod swoim adresem (RECON.md §3.4).
    # Ten odczyt nie niesie ŻADNYCH danych — sam fakt zniknięcia. Warstwa
    # wyżej musi to odróżnić od pustej strony, żeby nie nadpisać ostatniej
    # znanej ceny niczym.
    if surowa.pola.get("zniknela") == "1":
        return Auction(
            source_id=source_id,
            external_id=surowa.external_id,
            url=surowa.url,
            status=AuctionStatus.DISAPPEARED,
            first_seen_at=teraz,
            last_seen_at=teraz,
        )

    return Auction(
        source_id=source_id,
        external_id=surowa.external_id,
        url=surowa.url,
        status=AuctionStatus.ACTIVE,
        first_seen_at=teraz,
        last_seen_at=teraz,
        make=marka,
        model=model,
        variant=wersja,
        year=_liczba(pola.get("Rok produkcji")),
        mileage=przebieg,
        fuel=_paliwo(pola.get("Rodzaj paliwa")),
        engine_ccm=_liczba(pojemnosc.group(1)) if pojemnosc else None,
        engine_hp=int(moc.group(1)) if moc else None,
        vin=_vin(pola),
        location=pola.get("Lokalizacja"),
        seller=pola.get("Sprzedający"),
        price_current=_cena(pola),
        # Bez sesji serwis nie podaje liczby ofert (RECON.md §4.4).
        # `None`, nie `0` — to różnica między „nie wiemy" a „nikt nie licytował".
        bid_count=None,
        ends_at=ends_at,
        content_hash=surowa.content_hash or None,
    )
