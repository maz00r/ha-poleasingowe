"""Czyste parsowanie odpowiedzi JSON publicznego API mLeasing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.application.ports import SurowaOferta
from app.domain.errors import ParseFailed

BAZOWY_URL = "https://portalaukcyjny.mleasing.pl"
SCIEZKA_SZUKANIA = "/api/offer-read/search"
KATEGORIE = ("Passenger", "Vans")


@dataclass(slots=True, frozen=True)
class WynikStrony:
    """Jedna strona i licznik wszystkich surowych rekordów kategorii."""

    pozycje: tuple[SurowaOferta, ...]
    identyfikatory: tuple[str, ...]
    total_count: int


def _json(bajty: bytes, kontekst: str) -> Any:
    try:
        return json.loads(bajty)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseFailed(f"mLeasing: nieprawidłowy JSON ({kontekst})") from exc


def _slownik(wartosc: Any, kontekst: str) -> dict[str, Any]:
    if not isinstance(wartosc, dict):
        raise ParseFailed(f"mLeasing: oczekiwano obiektu JSON ({kontekst})")
    return wartosc


def _napis(wartosc: Any) -> str | None:
    if wartosc is None:
        return None
    if isinstance(wartosc, bool):
        return "true" if wartosc else "false"
    if isinstance(wartosc, str | int | float):
        tekst = str(wartosc).strip()
        return tekst or None
    return None


def _lokalizacja(wartosc: Any) -> str | None:
    """Składa sam adres; opis z telefonami i dane kontaktowe są pomijane."""
    if not isinstance(wartosc, list) or not wartosc:
        return None
    pierwszy = wartosc[0]
    if not isinstance(pierwszy, dict):
        return None
    czesci_ulicy = (
        _napis(pierwszy.get("street")),
        _napis(pierwszy.get("houseNumber")),
    )
    ulica = " ".join(x for x in czesci_ulicy if x)
    czesci_miejscowosci = (
        _napis(pierwszy.get("postalCode")),
        _napis(pierwszy.get("city")),
    )
    miejscowosc = " ".join(x for x in czesci_miejscowosci if x)
    return ", ".join(x for x in (ulica, miejscowosc) if x) or None


def _pola_oferty(dane: dict[str, Any], *, kategoria: str) -> dict[str, str]:
    mapa = {
        "name": "nazwa",
        "make": "Marka",
        "model": "Model",
        "type": "Wersja",
        "productionYear": "Rok produkcji",
        "milage": "Przebieg",
        "fuelType": "Paliwo",
        "gearBoxType": "Skrzynia biegów",
        "engineCapacity": "Pojemność",
        "enginePowerHp": "Moc",
        "vin": "VIN",
        "bodyType": "Karoseria",
        "color": "Kolor",
        "to": "koniec",
        "offerState": "stan",
        "auctionType": "typ_aukcji",
        "incrementAmount": "postapienie",
    }
    pola = {"kategoria": kategoria}
    for zrodlo, cel in mapa.items():
        if (wartosc := _napis(dane.get(zrodlo))) is not None:
            pola[cel] = wartosc

    # Wynik wyszukiwania ma `amount`, a szczegóły rozdzielają kwotę
    # wywoławczą i aktualną. Null pozostaje brakiem ceny, także po końcu
    # bez ofert (pomiar 182076, RECON.md §4.5).
    start = _napis(dane.get("startingAmount"))
    biezaca = _napis(
        dane.get("currentAmount") if "currentAmount" in dane else dane.get("amount")
    )
    if start is not None:
        pola["cena_wywolawcza"] = start
    if biezaca is not None:
        pola["cena"] = biezaca
    if "isGrossAmount" in dane and isinstance(dane["isGrossAmount"], bool):
        podstawa = "brutto" if dane["isGrossAmount"] else "netto"
        if "cena" in pola:
            pola["cena_podstawa"] = podstawa
        if "cena_wywolawcza" in pola:
            pola["cena_wywolawcza_podstawa"] = podstawa
    if dane.get("isWithdrawn") is True:
        pola["stan"] = "Withdrawn"
    if (adres := _lokalizacja(dane.get("locations"))) is not None:
        pola["Lokalizacja"] = adres
    return pola


def sparsuj_strone(bajty: bytes, *, kategoria: str) -> WynikStrony:
    korzen = _slownik(_json(bajty, "lista"), "lista")
    rekordy = korzen.get("items")
    liczba = korzen.get("totalCount")
    if not isinstance(rekordy, list) or not isinstance(liczba, int) or liczba < 0:
        raise ParseFailed("mLeasing: odpowiedź listy nie ma items i totalCount")

    pozycje: list[SurowaOferta] = []
    identyfikatory: list[str] = []
    for numer, wartosc in enumerate(rekordy, start=1):
        dane = _slownik(wartosc, f"lista, rekord {numer}")
        identyfikator = _napis(dane.get("id"))
        if identyfikator is None or not identyfikator.isdigit():
            raise ParseFailed(f"mLeasing: rekord {numer} nie ma liczbowego id")
        identyfikatory.append(identyfikator)
        if dane.get("auctionType") != "Auction":
            continue
        pozycje.append(
            SurowaOferta(
                external_id=identyfikator,
                url=f"{BAZOWY_URL}/oferta/{identyfikator}/",
                pola=_pola_oferty(dane, kategoria=kategoria),
            )
        )
    return WynikStrony(tuple(pozycje), tuple(identyfikatory), liczba)


def sparsuj_szczegoly(
    bajty: bytes,
    lokalizacje: bytes,
    *,
    external_id: str,
    url: str,
) -> SurowaOferta:
    korzen = _slownik(_json(bajty, "szczegóły"), "szczegóły")
    oferta = _slownik(korzen.get("offer"), "offer")
    pojazd = _slownik(korzen.get("leaseObject"), "leaseObject")
    if _napis(oferta.get("id")) != external_id:
        raise ParseFailed("mLeasing: szczegóły dotyczą innej aukcji")
    dane = {**pojazd, **oferta}
    miejsca = _json(lokalizacje, "lokalizacje")
    if not isinstance(miejsca, list):
        raise ParseFailed("mLeasing: lokalizacje nie są listą")
    dane["locations"] = miejsca
    # Szczegóły nie zwracają kategorii wyszukiwarki. `productCardType=POJ`
    # obejmuje więcej niż auta osobowe, więc nie udajemy, że jest równoważne
    # `Passenger`. Aktualizacja w bazie zachowa pewny rodzaj ze skanu listy.
    kategoria = ""
    return SurowaOferta(
        external_id=external_id,
        url=url,
        pola=_pola_oferty(dane, kategoria=kategoria),
    )


def zdjecia(bajty: bytes) -> tuple[str, ...]:
    rekordy = _json(bajty, "zdjęcia")
    if not isinstance(rekordy, list):
        raise ParseFailed("mLeasing: galeria nie jest listą")
    wynik: list[tuple[bool, str]] = []
    dozwolone = {
        "portalaukcyjny.mleasing.pl",
        "pliki-portalaukcyjny.mleasing.pl",
    }
    for rekord in rekordy:
        if not isinstance(rekord, dict) or not isinstance(rekord.get("url"), str):
            raise ParseFailed("mLeasing: nieprawidłowy rekord galerii")
        adres = urljoin(f"{BAZOWY_URL}/", rekord["url"])
        if urlsplit(adres).scheme == "https" and urlsplit(adres).netloc in dozwolone:
            wynik.append((rekord.get("isMain") is True, adres))
    wynik.sort(key=lambda x: not x[0])
    return tuple(adres for _, adres in wynik)
