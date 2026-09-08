"""Warstwa antykorupcyjna EFL (SPEC.md §6.2).

Tłumaczy surowy kształt serwisu na model domenowy. Dziwactwa EFL — formaty
kwot, sklejona marka z modelem, moc zaszyta w opisie silnika — **żyją tutaj
i nie wyciekają poza ten plik**.
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

# RECON.md §4.1: serwis podaje czas bez strefy. Zakladamy czas lokalny Polski,
# bo taki pokazuje uzytkownikowi. Do bazy idzie UTC (SPEC.md §8.2).
STREFA_SERWISU = zoneinfo.ZoneInfo("Europe/Warsaw")

_PRZEBIEG = re.compile(r"(\d[\d\s\xa0]*)\s*km", re.I)
_POJEMNOSC = re.compile(r"(\d[\d\s\xa0]*)\s*ccm", re.I)
_MOC = re.compile(r"(\d+)\s*KM", re.I)
_KONIEC = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})$")


def _int_lub_none(tekst: str | None) -> int | None:
    if not tekst:
        return None
    cyfry = re.sub(r"[^\d]", "", tekst)
    return int(cyfry) if cyfry else None


def _koniec_na_utc(wartosc: str) -> dt.datetime:
    dopasowanie = _KONIEC.match(wartosc.strip())
    if dopasowanie is None:
        raise ParseFailed(f"EFL: nieznany format czasu zakończenia: {wartosc!r}")
    d, m, r, gg, mm, ss = (int(x) for x in dopasowanie.groups())
    lokalny = dt.datetime(r, m, d, gg, mm, ss, tzinfo=STREFA_SERWISU)
    return lokalny.astimezone(dt.UTC)


def _paliwo(wartosc: str | None) -> str | None:
    """Nazwa paliwa w postaci wspólnej dla wszystkich źródeł (§6.2)."""
    return kanoniczne_paliwo(wartosc) if wartosc else None


def na_aukcje(surowa: SurowaOferta, source_id: int, teraz: dt.datetime) -> Auction:
    """Buduje encję domenową z surowej pozycji EFL."""
    pola = surowa.pola

    marka = model = wersja = None
    if "Marka, Model, Wersja wyposażenia" in pola:
        marka, model, wersja = podziel_marke_model(
            pola["Marka, Model, Wersja wyposażenia"]
        )
    elif "tytul" in pola:
        marka, model, wersja = podziel_marke_model(pola["tytul"])

    przebieg = None
    surowy_przebieg = pola.get("Przebieg odczytany")
    if surowy_przebieg:
        trafienie = _PRZEBIEG.search(surowy_przebieg)
        if trafienie is not None:
            try:
                przebieg = Mileage.z_tekstu(trafienie.group(1))
            except NieprawidlowaWartosc:
                przebieg = None

    # Na liscie: "Pojemnosc silnika: 1968ccm" i "Moc silnika: 163KM".
    # W szczegolach jedno pole: "Silnik: 1968ccm (Moc 150KM/110KW)".
    opis_silnika = pola.get("Silnik", "")
    pojemnosc = _int_lub_none(pola.get("Pojemność silnika"))
    if pojemnosc is None and opis_silnika:
        trafienie = _POJEMNOSC.search(opis_silnika)
        pojemnosc = _int_lub_none(trafienie.group(1)) if trafienie else None
    moc = _int_lub_none(pola.get("Moc silnika"))
    if moc is None and opis_silnika:
        trafienie = _MOC.search(opis_silnika)
        moc = int(trafienie.group(1)) if trafienie else None

    vin = None
    surowy_vin = pola.get("VIN")
    if surowy_vin:
        try:
            vin = Vin(surowy_vin)
        except NieprawidlowaWartosc:
            # Niechlujny VIN jest gorszy niz jego brak — to podstawa
            # deduplikacji miedzy serwisami (SPEC.md §8.4).
            vin = None

    cena = None
    surowa_cena = pola.get("cena")
    if surowa_cena:
        try:
            cena = Money.z_tekstu(surowa_cena, Currency.PLN)
        except NieprawidlowaWartosc as exc:
            raise ParseFailed(f"EFL {surowa.external_id}: {exc}") from exc

    koniec = _koniec_na_utc(pola["koniec"]) if pola.get("koniec") else None

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
        year=_int_lub_none(pola.get("Rok produkcji")),
        mileage=przebieg,
        fuel=_paliwo(pola.get("Rodzaj paliwa")),
        gearbox=pola.get("Skrzynia biegów"),
        engine_ccm=pojemnosc,
        engine_hp=moc,
        vin=vin,
        body=pola.get("Typ nadwozia"),
        color=pola.get("Kolor"),
        location=pola.get("Lokalizacja"),
        seller=None,
        price_current=cena,
        bid_count=_int_lub_none(pola.get("liczba_ofert")),
        # EFL nie podaje postapienia na stronie; wynika z regulaminu §4 ust. 5
        # (10/100/200 zl wg ceny wywolawczej). Pole jest informacyjne (§8.2),
        # wiec zapisujemy to, co wiemy, bez wyliczania.
        bid_increment_raw="wg regulaminu EFL §4 ust. 5: 10/100/200 zł",
        ends_at=koniec,
    )
