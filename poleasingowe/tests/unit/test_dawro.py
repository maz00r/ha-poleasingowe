"""Parser, mapper i adapter dawro.pl na zrzutach z rekonesansu (RECON.md §4.5)."""

from __future__ import annotations

import datetime as dt
import pathlib
from decimal import Decimal

import httpx
import pytest

from app.domain.entities import Auction
from app.domain.enums import AuctionStatus, Currency, RodzajPojazdu
from app.domain.errors import ParseFailed
from app.domain.value_objects import Money
from app.infrastructure.sources.dawro import mapper, parser, source

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "dawro"
TERAZ = dt.datetime(2026, 9, 14, 7, 45, 0, tzinfo=dt.UTC)


def html(nazwa: str) -> str:
    return (FIXTURES / nazwa).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# parser — lista
# --------------------------------------------------------------------------


def test_lista_daje_24_pozycje_z_cena_i_terminem() -> None:
    pozycje = parser.sparsuj_liste(html("lista-01.html"))

    assert len(pozycje) == 24
    hummer = next(p for p in pozycje if p.external_id == "16761")
    assert hummer.url == "https://www.dawro.pl/aukcja/16761,hummer-h2"
    assert hummer.pola["nazwa"] == "Hummer H2"
    assert hummer.pola["koniec"] == "2026-09-14 10:00"
    assert hummer.pola["cena_wywolawcza"] == "85 900,00 zł"
    assert hummer.pola["Sprzedający"] == "MultiDealer"
    # Bez ofert — pole nie jest ustawione, nie jest zerem czy pustym stringiem.
    assert "najwyzsza_oferta" not in hummer.pola


def test_lista_czyta_najwyzsza_oferte_z_atrybutu_kwota_na_kafelku() -> None:
    pozycje = parser.sparsuj_liste(html("lista-01.html"))

    audi = next(p for p in pozycje if p.external_id == "16762")
    assert audi.pola["najwyzsza_oferta"] == "75000.00"
    assert audi.pola["cena_wywolawcza"] == "119 900,00 zł"


def test_lista_bez_kontenera_to_blad() -> None:
    with pytest.raises(ParseFailed, match="listę aukcji"):
        parser.sparsuj_liste("<html><body>Just a moment…</body></html>")


def test_lista_pusta_z_przelacznikiem_widoku_nie_jest_bledem() -> None:
    pusta = '<div id="tresc-strony"><div class="wyswietlanie"></div></div>'
    assert parser.sparsuj_liste(pusta) == []


def test_landing_bez_kafelkow_i_przelacznika_to_blad_a_nie_pusta_lista() -> None:
    """RECON.md §4.5: `/aukcje` bez parametrów to landing, nie lista.

    Landing uznany za „pustą, kompletną listę" oznaczyłby po dwóch
    przemiatach każdą aukcję dawro jako znikniętą.
    """
    with pytest.raises(ParseFailed, match="landing"):
        parser.sparsuj_liste('<div id="tresc-strony"><p>Polecane</p></div>')


def test_lista_toleruje_absolutny_href_i_nietypowy_slug() -> None:
    kafelek = (
        '<div id="tresc-strony"><div class="fl aukcja-box">'
        '<a href="https://www.dawro.pl/aukcja/99,MAN-TGX_2.0/">'
        '<h2 class="nazwa">MAN TGX, WX12345</h2>'
        '<div class="parametry"></div></a></div></div>'
    )
    (pozycja,) = parser.sparsuj_liste(kafelek)

    assert pozycja.external_id == "99"
    assert pozycja.url == "https://www.dawro.pl/aukcja/99,MAN-TGX_2.0"
    # Bez atrybutu `title` nazwa idzie z tekstu — bez tablicy rejestracyjnej.
    assert pozycja.pola["nazwa"] == "MAN TGX"


# --------------------------------------------------------------------------
# parser — szczegóły
# --------------------------------------------------------------------------


def test_szczegoly_mapuja_vin_cene_lokalizacje_i_termin() -> None:
    surowa = parser.sparsuj_szczegoly(
        html("szczegoly-16761.html"), "16761", "https://x.test/aukcja/16761,hummer-h2"
    )
    pola = surowa.pola

    assert pola["VIN"] == "5GRLVGBCA0U3RTK31"
    assert pola["cena_wywolawcza"] == "85 900,00 zł"
    assert pola["Lokalizacja"] == "Dawro Wrocław Wodzisławska 8 52-017 Wrocław"
    assert pola["Przebieg"] == "102294 km"
    assert pola["Moc"] == "329 KM"
    assert pola["koniec_ts"] == "1789372800"
    assert pola["zamknieta"] == "0"
    # Serwis nie podaje paliwa, skrzyni ani nadwozia w żadnym polu — parser
    # nie ma skąd ich wziąć i nie wymyśla kluczy.
    assert not {"Paliwo", "Skrzynia biegów", "Karoseria"} & pola.keys()


def test_szczegoly_bez_paska_licytacji_to_blad() -> None:
    with pytest.raises(ParseFailed, match="aukcję"):
        parser.sparsuj_szczegoly("<html><body>nic</body></html>", "1", "u")


def test_szczegoly_rozdzielaja_opis_firmy_od_karuzeli_z_ta_sama_klasa() -> None:
    """`div.opis` na górze strony (karuzela) nie ma prawa wejść do wyniku."""
    tresc = html("szczegoly-16761.html")
    assert tresc.count('class="opis"') > 1  # karuzela + opis firmy w jednym pliku

    surowa = parser.sparsuj_szczegoly(tresc, "16761", "u")
    # `Lokalizacja` pochodzi z `parking-informacje`, nie z żadnej karuzeli —
    # gdyby cięcie bloku nie działało, złapalibyśmy przypadkowy opis.
    assert "Wrocław" in surowa.pola["Lokalizacja"]


def test_zdjecia_bez_duplikatow_bxslidera() -> None:
    adresy = parser.zdjecia(html("szczegoly-16761.html"))

    assert len(adresy) == 8
    assert len(set(adresy)) == len(adresy)
    assert all(a.startswith("https://www.dawro.pl/cache/zdjecia/") for a in adresy)


@pytest.mark.parametrize(
    ("fixture", "zamknieta", "ma_cene"),
    [
        ("domkniecie-pre-0s.html", "0", True),
        ("domkniecie-post+0s.html", "1", False),
        ("domkniecie-post+2s.html", "1", False),
        ("domkniecie-post+600s.html", "1", False),
    ],
)
def test_domkniecie_wykrywane_z_jawnego_tekstu_serwera(
    fixture: str, zamknieta: str, ma_cene: bool
) -> None:
    """`AUKCJA ZAKOŃCZONA` (`a.przycisk-licytuj`) — zmierzone w 32/32 próbkach."""
    surowa = parser.sparsuj_szczegoly(html(fixture), "16761", "u")

    assert surowa.pola["zamknieta"] == zamknieta
    assert ("cena_wywolawcza" in surowa.pola) is ma_cene


def test_odcisk_aukcji_stabilny_dla_tej_samej_strony() -> None:
    tresc = html("szczegoly-16761.html")
    inna = html("szczegoly-16762.html")
    assert parser.odcisk_aukcji(tresc) == parser.odcisk_aukcji(tresc)
    assert parser.odcisk_aukcji(tresc) != parser.odcisk_aukcji(inna)


# --------------------------------------------------------------------------
# mapper
# --------------------------------------------------------------------------


def test_mapper_nie_przelicza_vat_bo_podstawa_jest_nieznana() -> None:
    """Etykieta to zawsze „Cena wywoławcza", bez „netto”/„brutto” (RECON.md §4.5).

    W przeciwieństwie do Leasygroup/mLeasing adapter NIE zgaduje podstawy —
    kwota trafia do `Money` taka, jaką podał serwis.
    """
    surowa = parser.sparsuj_szczegoly(
        html("szczegoly-16761.html"), "16761", "https://x.test/aukcja/16761,hummer-h2"
    )
    aukcja = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)

    assert aukcja.price_start == Money(Decimal("85900.00"), Currency.PLN)


def test_mapper_nie_wymysla_paliwa_skrzyni_i_nadwozia() -> None:
    surowa = parser.sparsuj_szczegoly(
        html("szczegoly-16761.html"), "16761", "https://x.test/aukcja/16761,hummer-h2"
    )
    aukcja = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)

    assert aukcja.fuel is None
    assert aukcja.gearbox is None
    assert aukcja.body is None


def test_mapper_rozpoznaje_marke_model_i_rodzaj_z_nazwy() -> None:
    pozycje = parser.sparsuj_liste(html("lista-01.html"))
    hummer = next(p for p in pozycje if p.external_id == "16761")

    aukcja = mapper.na_aukcje(hummer, source_id=1, teraz=TERAZ)

    assert aukcja.make == "Hummer"
    assert aukcja.model == "H2"
    # "Hummer H2" nie ma słowa nadwozia w nazwie i dawro nie podaje kategorii
    # — `rozpoznaj` słusznie nie zgaduje z samej marki (RECON.md, rodzaje.py).
    assert aukcja.vehicle_kind is RodzajPojazdu.NIEZNANY
    assert aukcja.status is AuctionStatus.ACTIVE
    assert aukcja.ends_at == dt.datetime(2026, 9, 14, 8, 0, tzinfo=dt.UTC)


def test_mapper_zamknieta_aukcja_daje_minimalna_encje_disappeared() -> None:
    """Cena i przycisk znikają NARAZ (RECON.md §4.5) — ten odczyt nie niesie
    żadnych danych, tylko fakt zakończenia. Nadpisanie reszty pól wartością
    `None` wymazałoby jedyną znaną cenę u dispatchera (`_scal`), więc mapper
    oddaje encję tak minimalną jak `autoprzetarg`'s `zniknela`.
    """
    surowa = parser.sparsuj_szczegoly(html("domkniecie-post+0s.html"), "16761", "u")
    aukcja = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)

    assert aukcja == Auction(
        source_id=1,
        external_id="16761",
        url="u",
        status=AuctionStatus.DISAPPEARED,
        first_seen_at=TERAZ,
        last_seen_at=TERAZ,
    )


def test_mapper_najwyzsza_oferta_tylko_z_listy_nie_ze_szczegolow() -> None:
    """Szczegóły nigdy nie niosą bieżącej oferty — to placeholder AJAX-u."""
    z_listy = next(
        p
        for p in parser.sparsuj_liste(html("lista-01.html"))
        if p.external_id == "16762"
    )
    ze_szczegolow = parser.sparsuj_szczegoly(html("szczegoly-16762.html"), "16762", "u")

    aukcja_z_listy = mapper.na_aukcje(z_listy, source_id=1, teraz=TERAZ)
    aukcja_ze_szczegolow = mapper.na_aukcje(ze_szczegolow, source_id=1, teraz=TERAZ)

    assert aukcja_z_listy.price_current == Money(Decimal("75000.00"), Currency.PLN)
    assert aukcja_ze_szczegolow.price_current is None


# --------------------------------------------------------------------------
# source — sieć (httpx.MockTransport)
# --------------------------------------------------------------------------


async def test_przemiat_konczy_sie_na_powtorce_strony_bez_licznika() -> None:
    """dawro nie ma widgetu z liczbą stron — przekracza się przez próbę."""
    zadania: list[str] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        zadania.append(str(zadanie.url))
        # Serwis realnie przekierowuje za stronę 1 na stronę 1; tu wprost
        # oddajemy tę samą treść dla KAŻDEJ strony, co ma ten sam efekt
        # widziany przez adapter: brak nowych ID od drugiej strony.
        return httpx.Response(200, content=html("lista-01.html").encode("utf-8"))

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.DawroSource(klient) as adapter:
        oferty = await adapter.przemiec_liste()

    assert len(oferty) == 24
    assert len(zadania) == 2  # strona 1 (dane), strona 2 (potwierdzenie końca)


async def test_przemiat_odsiewa_duplikaty_takze_w_obrebie_jednej_strony() -> None:
    """Dwa kafelki tej samej aukcji w jednym `zapisz_z_przemiatu` to konflikt."""
    kafelek = (
        '<div class="fl aukcja-box"><a href="/aukcja/7,x">'
        '<h2 class="nazwa" title="A">A</h2><div class="parametry"></div></a></div>'
    )
    strona = f'<div id="tresc-strony">{kafelek}{kafelek}</div>'
    licznik = 0

    def obsluz(_: httpx.Request) -> httpx.Response:
        nonlocal licznik
        licznik += 1
        return httpx.Response(200, content=strona.encode("utf-8"))

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.DawroSource(klient) as adapter:
        strony = [s async for s in adapter.strony_przemiatu()]

    assert [p.external_id for s in strony for p in s.pozycje] == ["7"]
    assert licznik == 2


async def test_klient_wysyla_user_agent_z_rekonesansu() -> None:
    """Serwis mierzono wyłącznie tym UA; domyślny `python-httpx` jest niezmierzony."""
    naglowki: dict[str, str] = {}

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        naglowki.update(zadanie.headers)
        return httpx.Response(200, content=html("lista-01.html").encode("utf-8"))

    adapter = source.DawroSource.utworz()
    prawdziwy = adapter._klient
    adapter._klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL,
        headers=prawdziwy.headers,
        transport=httpx.MockTransport(obsluz),
    )
    await prawdziwy.aclose()
    async with adapter:
        await adapter.przemiec_liste()

    assert naglowki["user-agent"].startswith("Mozilla/5.0")
    assert "Chrome/126" in naglowki["user-agent"]


async def test_pobierz_szczegoly_zwraca_none_gdy_hash_bez_zmian() -> None:
    def obsluz(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html("szczegoly-16761.html").encode("utf-8"))

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    adres = "https://www.dawro.pl/aukcja/16761,hummer-h2"
    async with source.DawroSource(klient) as adapter:
        pierwszy = await adapter.pobierz_szczegoly("16761", url=adres)
        assert pierwszy is not None
        drugi = await adapter.pobierz_szczegoly(
            "16761", znany_hash=pierwszy.content_hash, url=adres
        )

    assert drugi is None


async def test_szczegoly_i_galeria_korzystaja_z_adresu_z_bazy() -> None:
    zadania: list[str] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        zadania.append(str(zadanie.url))
        return httpx.Response(200, content=html("szczegoly-16761.html").encode("utf-8"))

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    adres = "https://www.dawro.pl/aukcja/16761,hummer-h2"
    async with source.DawroSource(klient) as adapter:
        szczegoly = await adapter.pobierz_szczegoly("16761", url=adres)
        assert szczegoly is not None and szczegoly.url == adres
        await adapter.zdjecia("16761", url=adres)

    assert zadania == [adres, adres]


async def test_koduje_odpowiedz_jako_utf8_mimo_naglowka_iso88591() -> None:
    """RECON.md §4.5 — nagłówek `iso-8859-1`, treść UTF-8. `.text` by ją zepsuł."""

    def obsluz(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=html("szczegoly-16761.html").encode("utf-8"),
            headers={"content-type": "text/html; charset=iso-8859-1"},
        )

    klient = httpx.AsyncClient(
        base_url=parser.BAZOWY_URL, transport=httpx.MockTransport(obsluz)
    )
    async with source.DawroSource(klient) as adapter:
        szczegoly = await adapter.pobierz_szczegoly(
            "16761", url="https://www.dawro.pl/aukcja/16761,hummer-h2"
        )

    assert szczegoly is not None
    lokalizacja = "Dawro Wrocław Wodzisławska 8 52-017 Wrocław"
    assert szczegoly.pola["Lokalizacja"] == lokalizacja
