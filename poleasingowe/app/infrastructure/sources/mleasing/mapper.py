"""Warstwa antykorupcyjna publicznego API mLeasing."""

from __future__ import annotations

import datetime as dt
from contextlib import suppress
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.application.ports import SurowaOferta
from app.domain.entities import Auction
from app.domain.enums import AuctionStatus, Currency
from app.domain.errors import ParseFailed
from app.domain.value_objects import Mileage, Money, NieprawidlowaWartosc, Vin
from app.infrastructure.sources.marki import kanoniczna_marka, podziel_marke_model
from app.infrastructure.sources.paliwa import kanoniczne_paliwo
from app.infrastructure.sources.rodzaje import rozpoznaj

_VAT = Decimal("1.23")
_GROSZ = Decimal("0.01")


def _int(wartosc: str | None) -> int | None:
    if wartosc is None:
        return None
    try:
        return int(Decimal(wartosc))
    except (InvalidOperation, ValueError):
        return None


def _cena(pola: dict[str, str], klucz: str) -> Money | None:
    wartosc = pola.get(klucz)
    if wartosc is None:
        return None
    podstawa = pola.get(f"{klucz}_podstawa")
    if podstawa not in {"netto", "brutto"}:
        raise ParseFailed(f"mLeasing: brak podstawy VAT dla {klucz}")
    try:
        kwota = Decimal(wartosc)
    except InvalidOperation as exc:
        raise ParseFailed(f"mLeasing: nieprawidłowa {klucz}: {wartosc!r}") from exc
    if podstawa == "brutto":
        kwota = (kwota / _VAT).quantize(_GROSZ, rounding=ROUND_HALF_UP)
    return Money(kwota, Currency.PLN)


def _czas(wartosc: str | None) -> dt.datetime | None:
    if wartosc is None:
        return None
    try:
        wynik = dt.datetime.fromisoformat(wartosc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ParseFailed(f"mLeasing: nieprawidłowy termin: {wartosc!r}") from exc
    if wynik.tzinfo is None:
        raise ParseFailed("mLeasing: termin bez strefy czasowej")
    return wynik.astimezone(dt.UTC)


def _vin(wartosc: str | None) -> Vin | None:
    if wartosc is None:
        return None
    with suppress(NieprawidlowaWartosc):
        return Vin(wartosc)
    return None


def na_aukcje(surowa: SurowaOferta, source_id: int, teraz: dt.datetime) -> Auction:
    pola = surowa.pola
    nazwa = pola.get("nazwa") or pola.get("tytul", "")
    marka = pola.get("Marka")
    model = pola.get("Model")
    wersja = pola.get("Wersja")
    if marka:
        marka = kanoniczna_marka(marka)
    else:
        marka, model_z_nazwy, wersja_z_nazwy = podziel_marke_model(nazwa)
        model = model or model_z_nazwy
        wersja = wersja or wersja_z_nazwy

    przebieg = None
    if (wartosc := pola.get("Przebieg")) is not None:
        liczba = _int(wartosc)
        if liczba is not None:
            with suppress(NieprawidlowaWartosc):
                przebieg = Mileage(liczba)

    koniec = _czas(pola.get("koniec"))
    stan = pola.get("stan")
    zakonczona = stan in {"Expired", "Withdrawn", "Sold"}
    if stan is None and koniec is not None and koniec <= teraz:
        zakonczona = True

    return Auction(
        source_id=source_id,
        external_id=surowa.external_id,
        url=surowa.url,
        status=AuctionStatus.ENDED if zakonczona else AuctionStatus.ACTIVE,
        first_seen_at=teraz,
        last_seen_at=teraz,
        make=marka,
        model=model,
        variant=wersja,
        year=_int(pola.get("Rok produkcji")),
        mileage=przebieg,
        fuel=kanoniczne_paliwo(pola["Paliwo"]) if pola.get("Paliwo") else None,
        gearbox=pola.get("Skrzynia biegów"),
        engine_ccm=_int(pola.get("Pojemność")),
        engine_hp=_int(pola.get("Moc")),
        vin=_vin(pola.get("VIN")),
        body=pola.get("Karoseria"),
        color=pola.get("Kolor"),
        location=pola.get("Lokalizacja"),
        seller="mLeasing",
        vehicle_kind=rozpoznaj(kategoria=pola.get("kategoria"), nazwa=nazwa),
        price_start=_cena(pola, "cena_wywolawcza"),
        price_current=_cena(pola, "cena"),
        bid_increment_raw=pola.get("postapienie"),
        ends_at=koniec,
        content_hash=surowa.content_hash or None,
    )
