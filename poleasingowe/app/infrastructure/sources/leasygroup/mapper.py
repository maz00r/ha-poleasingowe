"""Warstwa antykorupcyjna Leasygroup.

Serwis miesza ceny netto i brutto. Model aplikacji porównuje ceny między
źródłami, dlatego ten adapter zapisuje wyłącznie PLN netto; gdy źródło poda
brutto, przelicza je po ustalonej stawce VAT 23%.
"""

from __future__ import annotations

import datetime as dt
import re
from contextlib import suppress
from decimal import ROUND_HALF_UP, Decimal

from app.application.ports import SurowaOferta
from app.domain.entities import Auction
from app.domain.enums import AuctionStatus, Currency
from app.domain.errors import ParseFailed
from app.domain.value_objects import Mileage, Money, NieprawidlowaWartosc, Vin
from app.infrastructure.sources.marki import kanoniczna_marka, podziel_marke_model
from app.infrastructure.sources.paliwa import kanoniczne_paliwo
from app.infrastructure.sources.rodzaje import rozpoznaj

_ODLICZANIE = re.compile(r"^(\d+)\s*:\s*(\d{1,2})\s*:\s*(\d{1,2})$")
_LICZBA = re.compile(r"\d+")
_MOC = re.compile(r"\b(\d+)\s*KM\b", re.I)
_GROSZ = Decimal("0.01")
_VAT = Decimal("1.23")


def _int_lub_none(wartosc: str | None) -> int | None:
    if not wartosc:
        return None
    dopasowanie = _LICZBA.search(wartosc.replace(" ", ""))
    return int(dopasowanie.group()) if dopasowanie is not None else None


def _vin(pola: dict[str, str]) -> Vin | None:
    wartosc = pola.get("VIN", "").strip()
    if not wartosc:
        return None
    try:
        return Vin(wartosc)
    except NieprawidlowaWartosc:
        return None


def _cena_netto(pola: dict[str, str], klucz: str) -> Money | None:
    wartosc = pola.get(klucz)
    if not wartosc:
        return None
    podstawa = pola.get(f"{klucz}_podstawa")
    if podstawa not in {"netto", "brutto"}:
        raise ParseFailed(f"Leasygroup: brak podstawy VAT dla {klucz}")
    try:
        kwota = Money.z_tekstu(wartosc, Currency.PLN).amount
    except NieprawidlowaWartosc as exc:
        raise ParseFailed(f"Leasygroup: nieprawidłowa {klucz}: {wartosc!r}") from exc
    if podstawa == "brutto":
        kwota = (kwota / _VAT).quantize(_GROSZ, rounding=ROUND_HALF_UP)
    return Money(kwota, Currency.PLN)


def _koniec_z_odliczania(wartosc: str | None, teraz: dt.datetime) -> dt.datetime | None:
    if not wartosc:
        return None
    dopasowanie = _ODLICZANIE.match(wartosc)
    if dopasowanie is None:
        raise ParseFailed(f"Leasygroup: nieznany format odliczania: {wartosc!r}")
    dni, godziny, minuty = (int(x) for x in dopasowanie.groups())
    return teraz + dt.timedelta(days=dni, hours=godziny, minutes=minuty)


def na_aukcje(surowa: SurowaOferta, source_id: int, teraz: dt.datetime) -> Auction:
    """Buduje encję z danych listy lub strony szczegółów Leasygroup."""
    pola = surowa.pola
    nazwa = pola.get("nazwa", "")
    marka = pola.get("Marka")
    model = pola.get("Model")
    wersja = None
    if marka:
        marka = kanoniczna_marka(marka)
    else:
        marka, model_z_nazwy, wersja = podziel_marke_model(nazwa)
        model = model or model_z_nazwy

    przebieg = None
    if wartosc := pola.get("Przebieg"):
        with suppress(NieprawidlowaWartosc):
            przebieg = Mileage.z_tekstu(wartosc)

    status = (
        AuctionStatus.ENDED
        if pola.get("zakonczona") == "true"
        else AuctionStatus.ACTIVE
    )
    return Auction(
        source_id=source_id,
        external_id=surowa.external_id,
        url=surowa.url,
        status=status,
        first_seen_at=teraz,
        last_seen_at=teraz,
        make=marka,
        model=model,
        variant=wersja,
        year=_int_lub_none(pola.get("Rok produkcji")),
        mileage=przebieg,
        fuel=kanoniczne_paliwo(pola["Paliwo"]) if pola.get("Paliwo") else None,
        gearbox=pola.get("Skrzynia biegów"),
        engine_ccm=_int_lub_none(pola.get("Pojemność")),
        engine_hp=(
            int(dopasowanie.group(1))
            if (dopasowanie := _MOC.search(nazwa)) is not None
            else None
        ),
        vin=_vin(pola),
        body=pola.get("Karoseria"),
        vehicle_kind=rozpoznaj(
            kategoria=None,
            nazwa=" ".join((nazwa, pola.get("Karoseria", ""))),
        ),
        color=pola.get("Kolor"),
        location=pola.get("Lokalizacja"),
        seller=pola.get("Sprzedający"),
        price_start=_cena_netto(pola, "cena_wywolawcza"),
        price_current=_cena_netto(pola, "cena"),
        ends_at=_koniec_z_odliczania(pola.get("odliczanie"), teraz),
        content_hash=surowa.content_hash or None,
    )
