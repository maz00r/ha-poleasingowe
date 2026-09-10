"""Parser, mapper i adapter Leasygroup na zrzutach offline."""

from __future__ import annotations

import datetime as dt
import pathlib
import ssl
from decimal import Decimal

import httpx
import pytest

from app.domain.entities import Auction, Source
from app.domain.enums import AuctionStatus, AuthState, Currency, PollTier
from app.domain.errors import ParseFailed
from app.domain.value_objects import Money
from app.infrastructure.scheduler.dispatcher import Dispatcher
from app.infrastructure.sources.leasygroup import mapper, parser, source

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "leasygroup"
TERAZ = dt.datetime(2026, 9, 10, 9, 50, 3, tzinfo=dt.UTC)


def html(nazwa: str) -> str:
    return (FIXTURES / nazwa).read_text(encoding="utf-8", errors="replace")


def test_certyfikat_posredni_leasygroup_daje_sie_wczytac_do_tls() -> None:
    """Obraz instaluje ten publiczny certyfikat do systemowego magazynu CA."""
    certyfikat = (
        pathlib.Path(__file__).resolve().parents[2]
        / "certyfikaty/certum-dv-tls-g2-r39-ca.crt"
    )
    kontekst = ssl.create_default_context()
    kontekst.load_verify_locations(cafile=certyfikat)


def test_lista_wp_uszcza_tylko_licytacje_i_bierze_id_z_url() -> None:
    pozycje = parser.sparsuj_liste(html("lista-widok-lista-01.html"))

    assert [p.external_id for p in pozycje] == ["28163"]
    pozycja = pozycje[0]
    assert pozycja.url.endswith("/aukcja/28163/honda-nsx-3-5-hybrid-581-km-4x4-2017/")
    assert pozycja.pola["numer_aukcji"] == "326196"
    assert pozycja.pola["cena"] == "878 900"
    assert pozycja.pola["cena_podstawa"] == "brutto"
    assert parser.numery_stron(html("lista-widok-lista-01.html"))[-1] == 8


def test_prawidlowa_pusta_lista_nie_udaje_waf() -> None:
    pusta = (
        '<div class="products_list_rows_container"></div>'
        '<div class="pagination_container"></div>'
    )
    assert parser.sparsuj_liste(pusta) == []
    assert parser.identyfikatory_wierszy(pusta) == []


def test_waf_lub_uszkodzony_html_to_blad() -> None:
    with pytest.raises(ParseFailed, match="listę aukcji"):
        parser.sparsuj_liste("<html><body>Just a moment…</body></html>")


def test_szczegoly_mapuja_dane_ceny_lokalizacje_i_zdjecia() -> None:
    surowa = parser.sparsuj_szczegoly(
        html("szczegoly-28163-licytacja.html"),
        "28163",
        "https://x.test/aukcja/28163/slug/",
    )
    pola = surowa.pola
    assert pola["Marka"] == "Honda"
    assert pola["Model"] == "NSX"
    # Fixtures są zredagowane przed publikacją; zachowujemy kształt VIN-u,
    # nie numer konkretnego pojazdu z rekonesansu.
    assert pola["VIN"] == "1HG1W7FWUWBJ4U8JU"
    assert pola["cena"] == "878 900"
    assert pola["cena_podstawa"] == "brutto"
    assert pola["cena_wywolawcza"] == "878 900"
    assert pola["Lokalizacja"] == "ul. Fabryczna 24, 55-080 Pietrzykowice"
    assert "tel." not in pola["Lokalizacja"].lower()

    adresy = parser.zdjecia(html("szczegoly-28163-licytacja.html"))
    assert len(adresy) > 10
    assert all("_869x489_special.webp" in adres for adres in adresy)


def test_cena_brutto_zostaje_przeliczona_na_netto_polowka_w_gore() -> None:
    surowa = parser.sparsuj_szczegoly(
        html("szczegoly-28163-licytacja.html"),
        "28163",
        "https://x.test/aukcja/28163/slug/",
    )
    aukcja = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)

    oczekiwana = Money(Decimal("714552.85"), Currency.PLN)
    assert aukcja.price_current == oczekiwana
    assert aukcja.price_start == oczekiwana
    assert (aukcja.make, aukcja.model, aukcja.year) == ("Honda", "NSX", 2017)
    assert aukcja.ends_at == TERAZ + dt.timedelta(days=3, hours=15, minutes=48)


def test_cena_netto_pozostaje_bez_przeliczenia() -> None:
    surowa = parser.sparsuj_liste(html("lista-widok-lista-01.html"))[0]
    netto = mapper.na_aukcje(
        type(surowa)(
            external_id=surowa.external_id,
            url=surowa.url,
            pola={**surowa.pola, "cena": "1 000,00", "cena_podstawa": "netto"},
        ),
        source_id=1,
        teraz=TERAZ,
    )
    assert netto.price_current == Money(Decimal("1000.00"), Currency.PLN)


def test_last_minute_jest_aktywny_i_nie_wymysla_terminu() -> None:
    surowa = parser.sparsuj_szczegoly(
        html("domkniecie-post+0s.html"), "28163", "https://x.test/aukcja/28163/slug/"
    )
    aukcja = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)

    assert surowa.pola["last_minute"] == "true"
    assert aukcja.status is AuctionStatus.ACTIVE
    assert aukcja.ends_at is None


def test_zakonczona_aukcja_zachowuje_cene() -> None:
    surowa = parser.sparsuj_szczegoly(
        html("domkniecie-post+5s.html"), "28163", "https://x.test/aukcja/28163/slug/"
    )
    aukcja = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)

    assert aukcja.status is AuctionStatus.ENDED
    assert aukcja.price_current == Money(Decimal("714552.85"), Currency.PLN)


async def test_przerwana_lub_zapetlona_paginacja_przerywa_skan() -> None:
    def obsluz(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html("lista-widok-lista-01.html"))

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.LeasygroupSource(klient) as adapter:
        with pytest.raises(ParseFailed, match="zapętlona paginacja"):
            await adapter.przemiec_liste()


async def test_lista_z_prawidlowym_html_mimo_http_404_jest_przetwarzana() -> None:
    """Serwis zwraca listę z 404; jej HTML jest ważniejszy niż zły status."""

    def obsluz(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text=html("lista-widok-lista-01.html"))

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.LeasygroupSource(klient) as adapter:
        tresc = await adapter._pobierz_liste(parser.SCIEZKA_LISTY.format(1))

    assert parser.sparsuj_liste(tresc.decode("utf-8"))[0].external_id == "28163"


async def test_pusta_strona_404_nie_udaje_listy_aukcji() -> None:
    def obsluz(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<html><body>Nie znaleziono</body></html>")

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.LeasygroupSource(klient) as adapter:
        with pytest.raises(ParseFailed, match="HTTP 404 bez poprawnej listy"):
            await adapter._pobierz_liste(parser.SCIEZKA_LISTY.format(1))


async def test_szczegoly_i_galeria_korzystaja_z_adresu_z_bazy() -> None:
    zadania: list[str] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        zadania.append(str(zadanie.url))
        return httpx.Response(200, text=html("szczegoly-28163-licytacja.html"))

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    adres = "https://aukcje.leasygroup.pl/aukcja/28163/honda-nsx-3-5-hybrid-581-km-4x4-2017/"
    async with source.LeasygroupSource(klient) as adapter:
        szczegoly = await adapter.pobierz_szczegoly("28163", url=adres)
        assert szczegoly is not None and szczegoly.url == adres
        await adapter.zdjecia("28163", url=adres)

    assert zadania == [adres, adres]


async def test_obcy_adres_nie_steruje_pobraniem_szczegolow() -> None:
    zadania: list[str] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        zadania.append(str(zadanie.url))
        return httpx.Response(200, text=html("szczegoly-28163-licytacja.html"))

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.LeasygroupSource(klient) as adapter:
        await adapter.pobierz_szczegoly(
            "28163", url="https://zly.example/aukcja/28163/"
        )

    assert zadania == ["https://aukcje.leasygroup.pl/aukcja/28163/"]


def test_obserwowana_aukcja_bez_terminu_dostaje_floor_zamiast_doby() -> None:
    aukcja = Auction(
        source_id=1,
        external_id="28163",
        url="https://aukcje.leasygroup.pl/aukcja/28163/slug/",
        status=AuctionStatus.ACTIVE,
        first_seen_at=TERAZ,
        last_seen_at=TERAZ,
    )
    zrodlo = Source(
        key="leasygroup",
        name="aukcje.leasygroup.pl",
        enabled=True,
        sweep_interval_seconds=21_600,
        rate_limit_per_minute=30,
        floor_seconds=60,
        auth_state=AuthState.ANONYMOUS,
        consecutive_auth_failures=0,
        overtime_window_seconds=120,
        overtime_extension_seconds=120,
        overtime_cap_seconds=None,
    )

    wynik = Dispatcher._termin_po_odpycie(aukcja, zrodlo, TERAZ, obserwowana=True)
    assert wynik.poll_tier is PollTier.ENDGAME
    assert wynik.next_poll_at == TERAZ + dt.timedelta(seconds=60)
