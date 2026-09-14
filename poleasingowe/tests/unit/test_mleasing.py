"""Parser, mapper i adapter publicznego API mLeasing."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from decimal import Decimal

import httpx
import pytest

from app.domain.enums import AuctionStatus, AuthState, Currency
from app.domain.errors import ParseFailed, SourceUnavailable
from app.domain.value_objects import Money
from app.infrastructure.sources import parametry
from app.infrastructure.sources.mleasing import mapper, parser, source

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "mleasing"
TERAZ = dt.datetime(2026, 9, 13, 12, 0, tzinfo=dt.UTC)


def dane(nazwa: str) -> bytes:
    return (FIXTURES / nazwa).read_bytes()


def test_lista_przyjmuje_tylko_licytacje_i_id_z_api() -> None:
    wynik = parser.sparsuj_strone(dane("lista-01.json"), kategoria="Passenger")

    assert wynik.total_count == 2
    assert wynik.identyfikatory == ("182214", "182215")
    assert [x.external_id for x in wynik.pozycje] == ["182214"]
    assert wynik.pozycje[0].url == f"{parser.BAZOWY_URL}/oferta/182214/"


def test_lista_brutto_jest_przeliczona_na_netto() -> None:
    surowa = parser.sparsuj_strone(
        dane("lista-01.json"), kategoria="Passenger"
    ).pozycje[0]
    aukcja = mapper.na_aukcje(surowa, 1, TERAZ)

    assert aukcja.price_current == Money(Decimal("100000.00"), Currency.PLN)
    assert aukcja.make == "Škoda"
    assert aukcja.location == "Testowa 1, 00-001 Warszawa"
    assert "600" not in aukcja.location


def test_pusta_lista_jest_poprawna_a_zly_kontrakt_nie() -> None:
    wynik = parser.sparsuj_strone(
        b'{"items": [], "totalCount": 0}', kategoria="Passenger"
    )
    assert wynik.pozycje == ()
    with pytest.raises(ParseFailed, match="items i totalCount"):
        parser.sparsuj_strone(b'{"message": "WAF"}', kategoria="Passenger")
    with pytest.raises(ParseFailed, match="JSON"):
        parser.sparsuj_strone(b"<html>blad</html>", kategoria="Passenger")


def test_szczegoly_mapuja_pojazd_lokalizacje_i_ceny() -> None:
    surowa = parser.sparsuj_szczegoly(
        dane("szczegoly-182214.json"),
        dane("lokalizacje-182214.json"),
        external_id="182214",
        url=f"{parser.BAZOWY_URL}/oferta/182214/",
    )
    aukcja = mapper.na_aukcje(surowa, 1, TERAZ)

    assert aukcja.status is AuctionStatus.ACTIVE
    assert aukcja.price_start == Money(Decimal("90000.00"), Currency.PLN)
    assert aukcja.price_current == Money(Decimal("100000.00"), Currency.PLN)
    assert aukcja.ends_at == dt.datetime(2026, 9, 18, 10, 0, tzinfo=dt.UTC)
    assert aukcja.location == "Testowa 1, 00-001 Warszawa"
    assert (aukcja.engine_ccm, aukcja.engine_hp) == (1968, 150)
    assert str(aukcja.vin) == "WVWZZZ1JZXW000001"


@pytest.mark.parametrize("stan", ["Expired", "Withdrawn", "Sold"])
def test_stany_koncowe_zachowuja_cene(stan: str) -> None:
    szczegoly = json.loads(dane("szczegoly-182214.json"))
    szczegoly["offer"]["offerState"] = stan
    surowa = parser.sparsuj_szczegoly(
        json.dumps(szczegoly).encode(),
        dane("lokalizacje-182214.json"),
        external_id="182214",
        url=f"{parser.BAZOWY_URL}/oferta/182214/",
    )
    aukcja = mapper.na_aukcje(surowa, 1, TERAZ)
    assert aukcja.status is AuctionStatus.ENDED
    assert aukcja.price_current == Money(Decimal("100000.00"), Currency.PLN)


def test_wygasla_182076_bez_ofert_nie_ma_ceny_biezacej() -> None:
    # Pola zaobserwowane w pomiarze 2026-09-14, T+0…T+600 s (RECON §4.5).
    oferta = {
        "id": 182076,
        "auctionType": "Auction",
        "offerState": "Expired",
        "to": "2026-09-14T12:00:00+02:00",
        "startingAmount": 886000.0,
        "currentAmount": None,
        "isGrossAmount": False,
    }
    surowa = parser.sparsuj_szczegoly(
        json.dumps({"offer": oferta, "leaseObject": {}}).encode(),
        b"[]",
        external_id="182076",
        url=f"{parser.BAZOWY_URL}/oferta/182076/",
    )
    aukcja = mapper.na_aukcje(surowa, 1, dt.datetime(2026, 9, 14, 10, tzinfo=dt.UTC))

    assert aukcja.status is AuctionStatus.ENDED
    assert aukcja.price_start == Money(Decimal("886000.00"), Currency.PLN)
    assert aukcja.price_current is None
    assert aukcja.ends_at == dt.datetime(2026, 9, 14, 10, tzinfo=dt.UTC)


def test_galeria_zwraca_duze_zdjecia_z_glownym_na_poczatku() -> None:
    assert parser.zdjecia(dane("zdjecia-182214.json")) == (
        f"{parser.BAZOWY_URL}/files/test/duze-1.jpg",
        "https://pliki-portalaukcyjny.mleasing.pl/files/test/duze-2.jpg",
    )


def _rekord(identyfikator: int) -> dict[str, object]:
    return {
        "id": identyfikator,
        "name": f"Auto {identyfikator}",
        "amount": 10000,
        "isGrossAmount": False,
        "to": "2026-09-18T12:00:00+02:00",
        "auctionType": "Auction",
    }


async def test_wyszukiwanie_najpierw_zaklada_sesje_i_wysyla_token_xsrf() -> None:
    zadania: list[httpx.Request] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        zadania.append(zadanie)
        if zadanie.method == "GET":
            assert zadanie.url.path == "/oferty/osobowe/"
            return httpx.Response(
                200,
                headers={"set-cookie": "XSRF-TOKEN=token%20sesji; Path=/"},
            )
        assert zadanie.method == "POST"
        parametry = json.loads(zadanie.content)
        assert zadanie.headers["X-XSRF-TOKEN"] == "token sesji"
        assert zadanie.headers["Origin"] == parser.BAZOWY_URL
        assert zadanie.headers["Referer"] == f"{parser.BAZOWY_URL}/oferty/osobowe/"
        assert parametry["auctionTypes"] is None
        assert parametry["category"] == "Passenger"
        return httpx.Response(200, json={"items": [], "totalCount": 0})

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.MleasingSource(klient) as adapter:
        assert await adapter.przemiec_liste() == []

    assert [(zadanie.method, zadanie.url.path) for zadanie in zadania] == [
        ("GET", "/oferty/osobowe/"),
        ("POST", "/api/offer-read/search"),
    ]


async def test_http_400_odswieza_token_i_ponawia_ta_sama_strone() -> None:
    liczba_wizyt = 0

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        nonlocal liczba_wizyt
        if zadanie.method == "GET":
            liczba_wizyt += 1
            return httpx.Response(
                200,
                headers={"set-cookie": f"XSRF-TOKEN=token{liczba_wizyt}; Path=/"},
            )
        token = zadanie.headers["X-XSRF-TOKEN"]
        if token == "token1":
            return httpx.Response(400)
        assert token in {"token2", "token3"}
        return httpx.Response(200, json={"items": [], "totalCount": 0})

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.MleasingSource(klient) as adapter:
        assert await adapter.przemiec_liste() == []

    # Jedyna obsługiwana kategoria to obecnie Passenger: pierwsze wejście
    # zakłada sesję, drugie odświeża token po odpowiedzi HTTP 400.
    assert liczba_wizyt == 2


async def test_pelna_paginacja_kategorii_osobowej() -> None:
    zadania: list[tuple[str, int]] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        if zadanie.method == "GET":
            return httpx.Response(200, headers={"set-cookie": "XSRF-TOKEN=token"})
        body = json.loads(zadanie.content)
        kategoria, numer = body["category"], body["pageNumber"]
        zadania.append((kategoria, numer))
        if kategoria == "Passenger":
            rekordy = (
                [_rekord(x) for x in range(1, 16)] if numer == 1 else [_rekord(16)]
            )
            return httpx.Response(200, json={"items": rekordy, "totalCount": 16})
        raise AssertionError(f"nieoczekiwana kategoria: {kategoria}")

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.MleasingSource(klient) as adapter:
        wynik = await adapter.przemiec_liste()

    assert len(wynik) == 16
    assert zadania == [("Passenger", 1), ("Passenger", 2)]


async def test_zapetlona_paginacja_i_zmiana_licznika_przerywaja_skan() -> None:
    def zapetlona(zadanie: httpx.Request) -> httpx.Response:
        if zadanie.method == "GET":
            return httpx.Response(200, headers={"set-cookie": "XSRF-TOKEN=token"})
        rekordy = [_rekord(x) for x in range(1, 16)]
        return httpx.Response(200, json={"items": rekordy, "totalCount": 16})

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(zapetlona)
    )
    async with source.MleasingSource(klient) as adapter:
        with pytest.raises(ParseFailed, match="zapętlona"):
            await adapter.przemiec_liste()

    def zmienna(zadanie: httpx.Request) -> httpx.Response:
        if zadanie.method == "GET":
            return httpx.Response(200, headers={"set-cookie": "XSRF-TOKEN=token"})
        numer = json.loads(zadanie.content)["pageNumber"]
        liczba = 16 if numer == 1 else 17
        rekordy = [_rekord(x) for x in range(1, 16)] if numer == 1 else [_rekord(16)]
        return httpx.Response(200, json={"items": rekordy, "totalCount": liczba})

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(zmienna)
    )
    async with source.MleasingSource(klient) as adapter:
        with pytest.raises(ParseFailed, match="zmieniła się"):
            await adapter.przemiec_liste()


async def test_http_400_nie_udaje_pustego_pelnego_skanu() -> None:
    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        if zadanie.url.path.startswith("/oferty/"):
            return httpx.Response(200, headers={"set-cookie": "XSRF-TOKEN=token"})
        return httpx.Response(400)

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL,
        transport=httpx.MockTransport(obsluz),
    )
    async with source.MleasingSource(klient) as adapter:
        with pytest.raises(SourceUnavailable, match="HTTP 400"):
            await adapter.przemiec_liste()


async def test_http_400_nie_siega_po_nieskategoryzowane_oferty_awaryjne() -> None:
    zadania: list[str] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        zadania.append(zadanie.url.path)
        if zadanie.url.path.startswith("/oferty/"):
            return httpx.Response(200, headers={"set-cookie": "XSRF-TOKEN=token"})
        if zadanie.url.path.endswith("/search"):
            return httpx.Response(400)
        raise AssertionError(str(zadanie.url))

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.MleasingSource(klient) as adapter:
        with pytest.raises(SourceUnavailable, match="HTTP 400"):
            await adapter.przemiec_liste()

    assert "/api/offer-read/latest-offers" not in zadania
    assert "/api/offer-read/promoted-offers" not in zadania


async def test_szczegoly_i_zdjecia_uzywaja_publicznych_endpointow() -> None:
    zadania: list[str] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        zadania.append(str(zadanie.url))
        if zadanie.url.path.endswith("/get"):
            return httpx.Response(200, content=dane("szczegoly-182214.json"))
        if zadanie.url.path.endswith("/get-locations"):
            return httpx.Response(200, content=dane("lokalizacje-182214.json"))
        return httpx.Response(200, content=dane("zdjecia-182214.json"))

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    adres = f"{parser.BAZOWY_URL}/oferta/182214/"
    async with source.MleasingSource(klient) as adapter:
        szczegoly = await adapter.pobierz_szczegoly("182214", url=adres)
        assert szczegoly is not None and szczegoly.url == adres
        assert (
            await adapter.pobierz_szczegoly(
                "182214", znany_hash=szczegoly.content_hash, url=adres
            )
            is None
        )
        await adapter.zdjecia("182214", url=adres)

    assert zadania[-1].endswith("get-images?offerId=182214")
    assert all("zly.example" not in x for x in zadania)


def test_parametry_zrodla_sa_oparte_na_regulaminie() -> None:
    wynik = parametry.zbuduj_source(
        "mleasing", enabled=True, rate_limit_per_minute=30, floor_seconds=60
    )
    assert wynik.name == "portalaukcyjny.mleasing.pl"
    assert wynik.auth_state is AuthState.ANONYMOUS
    assert (wynik.overtime_window_seconds, wynik.overtime_extension_seconds) == (
        120,
        120,
    )
    assert wynik.closing_ladder_seconds == (2, 5, 10, 20, 40)
