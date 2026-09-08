"""Parser i mapper autoprzetarg.pl na fixtures (SPEC.md §4, §13).

Offline, bez sieci. Oczekiwane wartości pochodzą z plików
w `fixtures/autoprzetarg/`.
"""

from __future__ import annotations

import datetime as dt
import pathlib
from decimal import Decimal

import pytest

from app.domain.enums import AuctionStatus, Currency
from app.domain.errors import ParseFailed
from app.domain.value_objects import Money
from app.infrastructure.sources.autoprzetarg import mapper, parser

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "autoprzetarg"
TERAZ = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.UTC)


def html(nazwa: str) -> str:
    return (FIXTURES / nazwa).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# Lista
# --------------------------------------------------------------------------


def test_lista_daje_dwanascie_pozycji_na_strone() -> None:
    """RECON.md §4.4 — 12 pozycji na stronę, paginacja `?page=N` od 1."""
    pozycje = parser.sparsuj_liste(html("lista-01.html"))
    assert len(pozycje) == 12
    assert parser.numery_stron(html("lista-01.html"))[:3] == [2, 3, 4]


def test_identyfikator_to_srodkowy_segment_adresu() -> None:
    """`/aukcja/<TYTUL>,<ID>,<Kategoria>` — nie liczba, nie sekwencyjny."""
    pozycje = parser.sparsuj_liste(html("lista-01.html"))
    assert pozycje[0].external_id == "LmDE4GUH3wg"
    assert "Samochody-dostawcze" in pozycje[0].url


def test_vin_jest_juz_na_liscie() -> None:
    """Jedyny z czterech serwisów, który to robi (RECON.md §4.4).

    Deduplikacja po VIN z §8.4 może tu działać bez wchodzenia w szczegóły,
    czyli bez ani jednego dodatkowego żądania.
    """
    pozycje = parser.sparsuj_liste(html("lista-01.html"))
    assert pozycje[0].pola["VIN"] == "VF30JTGK24ZNZKP5R"
    assert all("VIN" in p.pola for p in pozycje), "VIN ma być na każdym kafelku"


def test_termin_czytamy_z_zasiegu_kafelka_a_nie_z_calej_strony() -> None:
    """To jest ten błąd, który raz przesunął mi cały wynik o jeden.

    Ukryte `auctionEndDate` stoi **wewnątrz** kafelka, ale **po** jego
    `href`. Parowanie „pole → następny link" przypisuje datę sąsiedniej
    aukcji: lista dawała 10:05, a strona szczegółów tej samej aukcji 10:10.

    Test przypina konkretną datę do konkretnego identyfikatora, więc
    przesunięcie o jeden natychmiast go wywala.
    """
    pozycje = parser.sparsuj_liste(html("lista-01.html"))
    po_id = {p.external_id: p.pola.get("end_date") for p in pozycje}
    assert po_id["LmDE4GUH3wg"] == "2026-09-07 08:00:00"

    terminy = [p.pola["end_date"] for p in pozycje]
    assert terminy == sorted(terminy), "lista jest posortowana po terminie"


def test_kafelek_niesie_komplet_danych_technicznych() -> None:
    pola = parser.sparsuj_liste(html("lista-01.html"))[0].pola
    assert pola["Rok produkcji"] == "2013"
    assert pola["Pojemność silnika"] == "2198,00 ccm / 110 KM"
    assert pola["Rodzaj paliwa"] == "Diesel"
    assert pola["Sprzedający"] == "Poczta Polska"
    assert pola["Lokalizacja"] == "Bydgoszcz, ul. Gajowa 99"
    assert pola["Aktualna cena aukcji"] == "2989,76 zł"


def test_pusty_przebieg_zostaje_pusty() -> None:
    """„Przebieg:" bez liczby to normalny stan w tym serwisie.

    Zero znaczyłoby „przejechał zero kilometrów", a to co innego niż
    „serwis nie podał".
    """
    pozycja = parser.sparsuj_liste(html("lista-01.html"))[0]
    assert "Przebieg" not in pozycja.pola
    assert mapper.na_aukcje(pozycja, source_id=1, teraz=TERAZ).mileage is None


# --------------------------------------------------------------------------
# Szczegóły
# --------------------------------------------------------------------------


def test_szczegoly_daja_cene_i_termin() -> None:
    pola = parser.sparsuj_szczegoly(
        html("szczegoly-bFhGo2gH3wg.html"), "bFhGo2gH3wg", "u"
    ).pola
    assert pola["Aktualna cena aukcji"] == "11579,31 zł"
    assert pola["end_date"] == "2026-09-07 08:10:00"
    assert pola["VIN"] == "VF7EYUUNRGS8J1XZD"
    assert pola["Przebieg"] == "506382"


def test_liczby_ofert_nie_ma_bez_zalogowania() -> None:
    """RECON.md §4.4 — serwis nie podaje jej nawet na stronie szczegółów.

    `bid_count` zostaje `None`, nie `0`: zero znaczyłoby „nikt nie licytował",
    a my po prostu nie wiemy. Konsekwencja dla §11.8: `bid_gap` jest tu bez
    sesji niewykonalny.
    """
    aukcja = mapper.na_aukcje(
        parser.sparsuj_szczegoly(
            html("szczegoly-bFhGo2gH3wg.html"), "bFhGo2gH3wg", "u"
        ),
        source_id=1,
        teraz=TERAZ,
    )
    assert aukcja.bid_count is None


def test_strona_bez_aukcji_jest_bledem_parsowania() -> None:
    with pytest.raises(ParseFailed, match="nie wygląda na aukcję"):
        parser.sparsuj_szczegoly("<html><body>strona główna</body></html>", "x", "u")


# --------------------------------------------------------------------------
# Mapper
# --------------------------------------------------------------------------


def test_nazwa_jest_odklejana_od_danych_technicznych() -> None:
    """Nagłówek skleja markę, model, rocznik i silnik **bez spacji**:
    `CITROEN JUMPER2018 / 1997,00 ccm / 131 KM`.

    Bez obcięcia ogona rocznik wszedłby w model.
    """
    aukcja = mapper.na_aukcje(
        parser.sparsuj_szczegoly(
            html("szczegoly-bFhGo2gH3wg.html"), "bFhGo2gH3wg", "u"
        ),
        source_id=1,
        teraz=TERAZ,
    )
    assert (aukcja.make, aukcja.model) == ("CITROEN", "JUMPER")
    assert aukcja.year == 2018


def test_pojemnosc_i_moc_z_jednego_pola() -> None:
    """`2198,00 ccm / 110 KM` — przecinek jest tu separatorem dziesiętnym."""
    aukcja = mapper.na_aukcje(
        parser.sparsuj_liste(html("lista-01.html"))[0], source_id=1, teraz=TERAZ
    )
    assert aukcja.engine_ccm == 2198, "część całkowita, nie 219800"
    assert aukcja.engine_hp == 110


def test_czas_konca_idzie_do_bazy_w_utc() -> None:
    """SPEC.md §8.2. Strefa nie jest podana, zakładamy Europe/Warsaw (§4.4).

    7 września to czas letni, więc 08:00 lokalnie to 06:00 UTC.
    """
    aukcja = mapper.na_aukcje(
        parser.sparsuj_liste(html("lista-01.html"))[0], source_id=1, teraz=TERAZ
    )
    assert aukcja.ends_at == dt.datetime(2026, 9, 7, 6, 0, tzinfo=dt.UTC)


def test_cena_z_przecinkiem_dziesietnym() -> None:
    aukcja = mapper.na_aukcje(
        parser.sparsuj_szczegoly(
            html("szczegoly-bFhGo2gH3wg.html"), "bFhGo2gH3wg", "u"
        ),
        source_id=1,
        teraz=TERAZ,
    )
    assert aukcja.price_current == Money(Decimal("11579.31"), Currency.PLN)


def test_cala_lista_mapuje_sie_bez_wyjatku() -> None:
    pozycje = parser.sparsuj_liste(html("lista-01.html"))
    aukcje = [mapper.na_aukcje(p, source_id=1, teraz=TERAZ) for p in pozycje]
    assert len(aukcje) == 12
    assert all(a.ends_at is not None for a in aukcje)


# --------------------------------------------------------------------------
# Zniknięcie aukcji — jedyny marker końca w tym serwisie
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kod,lokalizacja",
    [(302, "https://autoprzetarg.pl/"), (301, "/"), (200, "https://autoprzetarg.pl")],
)
def test_przekierowanie_znaczy_ze_aukcja_zniknela(kod: int, lokalizacja: str) -> None:
    """RECON.md §3.4 — 10-15 s po terminie serwis odsyła na stronę główną.

    To jedyny marker stanu końcowego, jaki ten serwis daje: nie ma etykiety
    w treści, bo nie ma już treści.
    """
    assert parser.czy_zakonczona(kod, lokalizacja)


def test_zwykla_odpowiedz_nie_jest_znikiem() -> None:
    assert not parser.czy_zakonczona(200, "https://autoprzetarg.pl/aukcja/x,y,z")


def test_zniknieta_aukcja_mapuje_sie_na_status_bez_danych() -> None:
    """Ten odczyt niesie sam fakt zniknięcia, nie dane.

    Gdyby mapper wyprodukował aukcję z pustymi polami i statusem ACTIVE,
    zapis wyczyściłby ostatnią znaną cenę — czyli jedyne, co nam po tej
    aukcji zostaje (§8.4).
    """
    from app.application.ports import SurowaOferta

    aukcja = mapper.na_aukcje(
        SurowaOferta(external_id="x", url="u", pola={"zniknela": "1"}),
        source_id=1,
        teraz=TERAZ,
    )
    assert aukcja.status is AuctionStatus.DISAPPEARED
    assert aukcja.price_current is None


def test_odcisk_ignoruje_szum_otoczki_strony() -> None:
    """Zmierzone 2026-09-08: cztery niezależne fragmenty zmieniane per żądanie.

    Znacznik odświeżania cache przy CSS-ach, token CSRF wyszukiwarki, link
    Cloudflare `cdn-cgi/content` i obfuskacja adresu e-mail. Żaden nie ma nic
    wspólnego z aukcją, a każdy sam wystarczał, żeby hash całej odpowiedzi
    był za każdym razem inny — czyli żeby §11.3 krok 2 nie oszczędzał nic.
    """
    surowy = html("szczegoly-bFhGo2gH3wg.html")
    zaszumiony = (
        surowy.replace("?639244770886855807", "?639299999999999999")
        .replace('data-cfemail="', 'data-cfemail="ff')
        .replace("__RequestVerificationToken", "__RequestVerificationToken")
    )
    zaszumiony += "<!-- cdn-cgi/content?id=INNY-ZA-KAZDYM-RAZEM -->"

    assert zaszumiony != surowy
    assert parser.odcisk_aukcji(zaszumiony) == parser.odcisk_aukcji(surowy)


def test_odcisk_reaguje_na_zmiane_ceny_i_terminu() -> None:
    """Odcisk ma być czuły na to, co nas obchodzi — inaczej gubi zmiany."""
    surowy = html("szczegoly-bFhGo2gH3wg.html")
    bazowy = parser.odcisk_aukcji(surowy)

    drozej = surowy.replace("11579,31 zł", "11679,31 zł", 1)
    assert drozej != surowy
    assert parser.odcisk_aukcji(drozej) != bazowy

    przesuniety = surowy.replace("2026-09-07 08:10:00", "2026-09-07 08:12:00")
    assert przesuniety != surowy
    assert parser.odcisk_aukcji(przesuniety) != bazowy


def test_zdjecia_sa_rozwijane_do_adresow_bezwzglednych() -> None:
    """W HTML-u stoją jako `/uploads/photos/<id>/N.jpg` — bez hosta."""
    adresy = parser.zdjecia(html("szczegoly-bFhGo2gH3wg.html"))
    assert adresy
    assert all(u.startswith("https://autoprzetarg.pl/uploads/photos/") for u in adresy)
