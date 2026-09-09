"""Strona odczytu interfejsu na realnej bazie (SPEC.md §12).

Najważniejsza część to paginacja keyset. Da się ją napisać tak, że działa
na pierwszej stronie i cicho gubi wiersze na ostatniej — dlatego testy nie
sprawdzają „czy zwróciło coś", tylko **czy przejście przez wszystkie strony
daje dokładnie ten sam zbiór, co jedno duże zapytanie**.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import psycopg
import pytest

from app.application.read_models import Kryteria, Kursor, Sortowanie
from app.domain.entities import (
    Auction,
    OfertaUczestnika,
    PriceSnapshot,
    WatchlistEntry,
)
from app.domain.enums import AuctionStatus, Currency, FinalPriceState, RodzajPojazdu
from app.domain.value_objects import Mileage, Money, Vin
from app.infrastructure.persistence.queries import PgZapytania
from app.infrastructure.persistence.repositories import PgUnitOfWork
from tests.conftest import wymaga_postgresa
from tests.integration.test_repozytoria import zrodlo

pytestmark = wymaga_postgresa

TERAZ = dt.datetime(2026, 9, 7, 10, 0, tzinfo=dt.UTC)
PLN = Currency.PLN


def _pln(kwota: str) -> Money:
    return Money(Decimal(kwota), PLN)


async def _dane(baza: psycopg.AsyncConnection) -> dict[str, int]:
    """Mały, ale zróżnicowany zestaw: puste `ends_at`, duplikat, archiwum.

    Puste `ends_at` są tu celowo — to one wywracają naiwną paginację keyset.
    """
    uow = PgUnitOfWork(baza)
    efl = await uow.source.zapisz(zrodlo("efl"))
    pol = await uow.source.zapisz(zrodlo("poleasingowe"))
    assert efl.id is not None and pol.id is not None

    def aukcja(source_id: int, external_id: str, **pola: object) -> Auction:
        dane: dict[str, object] = {
            "source_id": source_id,
            "external_id": external_id,
            "url": f"https://przyklad.test/{external_id}",
            "status": AuctionStatus.ACTIVE,
            "first_seen_at": TERAZ - dt.timedelta(days=3),
            "last_seen_at": TERAZ,
            # Zestaw jest o samochodach osobowych — pojazdy innych rodzajów
            # dopisujemy jawnie, bo domyślny filtr listy je odsiewa.
            "vehicle_kind": RodzajPojazdu.OSOBOWY,
        }
        dane.update(pola)
        return Auction(**dane)  # type: ignore[arg-type]

    teraz = dt.datetime.now(dt.UTC)
    zapisane: dict[str, int] = {}
    definicje = [
        # klucz, źródło, pola
        (
            "audi-za-godzine",
            efl.id,
            {
                "make": "Audi",
                "model": "A6",
                "year": 2019,
                "mileage": Mileage(120_000),
                "fuel": "diesel",
                "gearbox": "automat",
                "location": "Warszawa",
                "price_current": _pln("120000"),
                "ends_at": teraz + dt.timedelta(hours=1),
                "vin": Vin("WAUZZZ4G7KN123456"),
            },
        ),
        (
            "audi-za-tydzien",
            efl.id,
            {
                "make": "Audi",
                "model": "A4",
                "year": 2021,
                "mileage": Mileage(40_000),
                "fuel": "benzyna",
                "gearbox": "manual",
                "location": "Kraków",
                "price_current": _pln("90000"),
                "ends_at": teraz + dt.timedelta(days=7),
            },
        ),
        (
            "vw-za-dwie-godziny",
            pol.id,
            {
                "make": "Volkswagen",
                "model": "Passat",
                "year": 2018,
                "mileage": Mileage(210_000),
                "fuel": "diesel",
                "gearbox": "automat",
                "location": "Warszawa",
                "price_current": _pln("48600"),
                "ends_at": teraz + dt.timedelta(hours=2),
            },
        ),
        (
            "bez-terminu-a",
            pol.id,
            {"make": "Skoda", "model": "Octavia", "price_current": _pln("55000")},
        ),
        (
            "bez-terminu-b",
            pol.id,
            {"make": "Skoda", "model": "Superb", "price_current": _pln("65000")},
        ),
        (
            "swieza",
            efl.id,
            {
                "make": "BMW",
                "model": "X3",
                "price_current": _pln("150000"),
                "ends_at": teraz + dt.timedelta(days=2),
                "first_seen_at": teraz - dt.timedelta(minutes=5),
            },
        ),
        (
            "archiwalna",
            efl.id,
            {
                "make": "Opel",
                "model": "Insignia",
                "status": AuctionStatus.ENDED,
                "price_current": _pln("30000"),
                "ends_at": teraz - dt.timedelta(days=1),
                "final_price_state": FinalPriceState.CONFIRMED,
            },
        ),
        (
            "archiwalna-nieobserwowana",
            pol.id,
            {
                "make": "Ford",
                "model": "Focus",
                "status": AuctionStatus.ENDED,
                "price_current": _pln("25000"),
                "ends_at": teraz - dt.timedelta(days=2),
            },
        ),
        (
            "zniknieta-obserwowana",
            pol.id,
            {
                "make": "Toyota",
                "model": "Yaris",
                "status": AuctionStatus.DISAPPEARED,
                "price_current": _pln("35000"),
                "ends_at": teraz - dt.timedelta(hours=12),
            },
        ),
    ]
    for klucz, source_id, pola in definicje:
        wynik = await uow.auction.zapisz(aukcja(source_id, klucz, **pola))
        assert wynik.id is not None
        zapisane[klucz] = wynik.id

    # Duplikat po VIN (§8.4) — ma NIE pojawiać się na liście.
    duplikat = await uow.auction.zapisz(
        aukcja(
            pol.id,
            "duplikat",
            make="Audi",
            model="A6",
            price_current=_pln("121000"),
            ends_at=teraz + dt.timedelta(hours=3),
            duplicate_of=zapisane["audi-za-godzine"],
        )
    )
    assert duplikat.id is not None
    zapisane["duplikat"] = duplikat.id

    await uow.watchlist.dodaj(
        WatchlistEntry(
            auction_id=zapisane["vw-za-dwie-godziny"],
            added_at=TERAZ,
            note="sprawdzić lakier",
            target_price=_pln("50000"),
        )
    )
    for klucz in ("archiwalna", "zniknieta-obserwowana"):
        await uow.watchlist.dodaj(
            WatchlistEntry(auction_id=zapisane[klucz], added_at=TERAZ)
        )
    return zapisane


async def _wszystkie_klucze(
    zapytania: PgZapytania, kryteria: Kryteria, limit: int = 100
) -> list[str]:
    strona = await zapytania.lista(kryteria, None, limit)
    return [p.external_id for p in strona.pozycje]


async def test_domyslna_lista_pokazuje_aktywne_od_najblizszego_konca(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    await _dane(pusta_baza)
    zapytania = PgZapytania(pusta_baza)
    klucze = await _wszystkie_klucze(zapytania, Kryteria())
    assert klucze == [
        "audi-za-godzine",
        "vw-za-dwie-godziny",
        "swieza",
        "audi-za-tydzien",
        # Puste `ends_at` na końcu (NULLS LAST) — nie na początku i nie
        # pominięte.
        "bez-terminu-a",
        "bez-terminu-b",
    ]


async def test_duplikat_nie_zasmieca_listy(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §8.4 — duplikat zostaje w bazie, znika z listy."""
    await _dane(pusta_baza)
    klucze = await _wszystkie_klucze(PgZapytania(pusta_baza), Kryteria(status=None))
    assert "duplikat" not in klucze


@pytest.mark.parametrize(
    "sortowanie",
    [
        Sortowanie.KONIEC_ROSNACO,
        Sortowanie.KONIEC_MALEJACO,
        Sortowanie.CENA_ROSNACO,
        Sortowanie.CENA_MALEJACO,
        Sortowanie.ROCZNIK_MALEJACO,
        Sortowanie.PRZEBIEG_ROSNACO,
        Sortowanie.NAJNOWSZE,
    ],
)
async def test_paginacja_keyset_nie_gubi_i_nie_powtarza_wierszy(
    pusta_baza: psycopg.AsyncConnection, sortowanie: Sortowanie
) -> None:
    """Serce §12: przejście po dwa wiersze ma dać to samo, co jednym strzałem.

    Strona po dwa, przy sześciu wierszach i trzech kluczach mogących być
    `NULL`-em (`year`, `mileage_km`, `ends_at`) — czyli dokładnie sytuacja,
    w której naiwny warunek `klucz > ostatni` przeskakuje cały ogon.
    """
    await _dane(pusta_baza)
    zapytania = PgZapytania(pusta_baza)
    kryteria = Kryteria(sortowanie=sortowanie)

    jednym_strzalem = await _wszystkie_klucze(zapytania, kryteria)

    po_kawalku: list[str] = []
    kursor: Kursor | None = None
    for _ in range(10):  # bezpiecznik: przy 6 wierszach starczą 3 obroty
        strona = await zapytania.lista(kryteria, kursor, 2)
        po_kawalku.extend(p.external_id for p in strona.pozycje)
        if strona.kursor_dalej is None:
            break
        kursor = Kursor.odkoduj(strona.kursor_dalej)
    else:  # pragma: no cover — pętla kończy się przez `break`
        pytest.fail("paginacja się nie zatrzymała")

    assert po_kawalku == jednym_strzalem
    assert len(po_kawalku) == len(set(po_kawalku)), "wiersz pojawił się dwa razy"


async def test_ostatnia_strona_nie_obiecuje_kolejnej(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """`ma_wiecej` bierze się z wiersza ponad limit, nie z `COUNT(*)`."""
    await _dane(pusta_baza)
    strona = await PgZapytania(pusta_baza).lista(Kryteria(), None, 100)
    assert strona.kursor_dalej is None
    assert strona.ma_wiecej is False


async def test_filtry_zawezaja_liste(pusta_baza: psycopg.AsyncConnection) -> None:
    zapytania = PgZapytania(pusta_baza)
    await _dane(pusta_baza)

    assert await _wszystkie_klucze(zapytania, Kryteria(marki=("Audi",))) == [
        "audi-za-godzine",
        "audi-za-tydzien",
    ]
    assert await _wszystkie_klucze(zapytania, Kryteria(zrodla=("poleasingowe",))) == [
        "vw-za-dwie-godziny",
        "bez-terminu-a",
        "bez-terminu-b",
    ]
    assert await _wszystkie_klucze(
        zapytania, Kryteria(cena_od=60_000, cena_do=100_000)
    ) == ["audi-za-tydzien", "bez-terminu-b"]
    assert await _wszystkie_klucze(zapytania, Kryteria(paliwa=("diesel",))) == [
        "audi-za-godzine",
        "vw-za-dwie-godziny",
    ]
    assert await _wszystkie_klucze(zapytania, Kryteria(przebieg_do=50_000)) == [
        "audi-za-tydzien"
    ]


async def test_szukanie_obejmuje_vin(pusta_baza: psycopg.AsyncConnection) -> None:
    """VIN jest tym, czym najczęściej szuka się konkretnego egzemplarza."""
    await _dane(pusta_baza)
    klucze = await _wszystkie_klucze(
        PgZapytania(pusta_baza), Kryteria(szukaj="WAUZZZ4G7KN123456")
    )
    assert klucze == ["audi-za-godzine"]


async def test_widok_konczacych_sie_w_24h(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §12. Aukcje po terminie NIE należą do „kończą się w 24 h"."""
    await _dane(pusta_baza)
    klucze = await _wszystkie_klucze(
        PgZapytania(pusta_baza), Kryteria(konczy_sie_w_h=24, status=None)
    )
    assert klucze == ["audi-za-godzine", "vw-za-dwie-godziny"]
    assert "archiwalna" not in klucze


async def test_widok_nowych_od_ostatniej_wizyty(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    await _dane(pusta_baza)
    klucze = await _wszystkie_klucze(
        PgZapytania(pusta_baza),
        Kryteria(nowe_od=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30)),
    )
    assert klucze == ["swieza"]


async def test_archiwum_niesie_znacznik_pewnosci_ceny(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §12 — archiwum z ceną końcową i znacznikiem pewności."""
    await _dane(pusta_baza)
    strona = await PgZapytania(pusta_baza).lista(
        Kryteria(status=AuctionStatus.ENDED, tylko_obserwowane=True), None, 100
    )
    assert {p.external_id for p in strona.pozycje} == {
        "archiwalna",
        "zniknieta-obserwowana",
    }
    archiwalna = next(p for p in strona.pozycje if p.external_id == "archiwalna")
    assert archiwalna.final_price_state is FinalPriceState.CONFIRMED


async def test_lista_niesie_stan_obserwacji_i_prog(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Bez tego wyróżnienie z §12 wymagałoby zapytania na każdy wiersz."""
    await _dane(pusta_baza)
    strona = await PgZapytania(pusta_baza).lista(
        Kryteria(tylko_obserwowane=True), None, 100
    )
    assert [p.external_id for p in strona.pozycje] == ["vw-za-dwie-godziny"]
    pozycja = strona.pozycje[0]
    assert pozycja.obserwowana
    assert pozycja.cena_docelowa == _pln("50000")
    assert pozycja.notatka == "sprawdzić lakier"
    assert pozycja.ponizej_progu, "48 600 zł jest poniżej progu 50 000 zł"


async def test_szczegoly_lacza_aukcje_z_watchlista(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    identyfikatory = await _dane(pusta_baza)
    dane = await PgZapytania(pusta_baza).szczegoly(identyfikatory["vw-za-dwie-godziny"])
    assert dane is not None
    assert dane.pozycja.make == "Volkswagen"
    assert dane.pozycja.obserwowana
    assert dane.pozycja.notatka == "sprawdzić lakier"


async def test_porownania_rynkowe_oddzielaja_ceny_pewne_od_ostatnich(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    identyfikatory = await _dane(pusta_baza)
    uow = PgUnitOfWork(pusta_baza)
    efl = await uow.source.po_kluczu("efl")
    assert efl is not None and efl.id is not None

    for external_id, cena, stan in (
        ("a6-pewna", "100000", FinalPriceState.CONFIRMED),
        ("a6-ostatnia", "90000", FinalPriceState.LAST_SEEN),
    ):
        await uow.auction.zapisz(
            Auction(
                source_id=efl.id,
                external_id=external_id,
                url=f"https://przyklad.test/{external_id}",
                status=AuctionStatus.ENDED,
                first_seen_at=TERAZ,
                last_seen_at=TERAZ,
                make="Audi",
                model="A6",
                year=2020,
                price_current=_pln(cena),
                final_price_state=stan,
                last_price_lead_seconds=(
                    None if stan is FinalPriceState.CONFIRMED else 30
                ),
            )
        )

    wynik = await PgZapytania(pusta_baza).porownania_rynkowe(
        identyfikatory["audi-za-godzine"]
    )

    assert len(wynik) == 1
    assert wynik[0].year == 2020
    assert wynik[0].mediana_potwierdzona == 100_000
    assert wynik[0].liczba_potwierdzonych == 1
    assert wynik[0].mediana_ostatnia == 90_000
    assert wynik[0].liczba_ostatnich == 1


async def test_szczegoly_duplikatu_wskazuja_oryginal(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Duplikat znika z listy, ale musi dać się obejrzeć i wyjaśnić (§8.4)."""
    identyfikatory = await _dane(pusta_baza)
    dane = await PgZapytania(pusta_baza).szczegoly(identyfikatory["duplikat"])
    assert dane is not None
    assert dane.duplicate_of == identyfikatory["audi-za-godzine"]


async def test_szczegoly_nieistniejacej_aukcji_to_none_a_nie_wyjatek(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    assert await PgZapytania(pusta_baza).szczegoly(999_999) is None


async def test_wartosci_filtrow_biora_sie_z_danych(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Filtr pokazujący markę, której nie ma w bazie, zawsze zwraca pustkę."""
    await _dane(pusta_baza)
    wartosci = await PgZapytania(pusta_baza).wartosci_filtrow()
    assert "Audi" in wartosci["marka"]
    assert "Fiat" not in wartosci["marka"]
    assert set(wartosci["zrodlo"]) == {"efl", "poleasingowe"}
    assert set(wartosci["paliwo"]) == {"benzyna", "diesel"}


async def test_diagnostyka_czyta_widok_a_nie_tabele(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §9 — widoki są kontraktem, panel korzysta z nich jak Grafana."""
    await _dane(pusta_baza)
    zrodla = await PgZapytania(pusta_baza).diagnostyka()
    assert [z.source_key for z in zrodla] == ["efl", "poleasingowe"]
    efl = zrodla[0]
    assert efl.aktywne_aukcje == 3, "trzy aktywne w EFL — archiwalna się nie liczy"
    assert efl.closing_ladder_seconds == (2, 5, 10, 20, 40)
    assert efl.bid_count_semantics == "UNKNOWN"
    assert not efl.zablokowane


async def test_rozmiar_bazy_i_czas_serwera(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §12, §11.7 — rozmiar do budżetu, zegar do wykrywania dryfu."""
    zapytania = PgZapytania(pusta_baza)
    rozmiar = await zapytania.rozmiar_bazy()
    assert rozmiar is not None and rozmiar > 0

    czas = await zapytania.czas_serwera()
    assert czas.tzinfo is not None, "zegar bez strefy nie nadaje się do porównań"
    dryf = abs((dt.datetime.now(dt.UTC) - czas).total_seconds())
    assert dryf < 60, "lokalny Postgres i proces testów stoją na tej samej maszynie"


async def test_domyslny_filtr_pokazuje_tylko_osobowe(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Serwisy sprzedają naczepy i motocykle w tej samej kategorii co auta.

    Bez zawężenia lista wygląda tak, jak zgłosił to użytkownik: samochody
    wymieszane z przyczepami. `rodzaj=None` musi nadal pokazywać wszystko —
    zawężenie ma być wyborem, nie ukryciem danych.
    """
    zapytania = PgZapytania(pusta_baza)
    identyfikatory = await _dane(pusta_baza)

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "INSERT INTO app.auction (source_id, external_id, url, make, model,"
            " vehicle_kind, ends_at, first_seen_at, last_seen_at)"
            " SELECT source_id, 'naczepa-krone', 'https://przyklad.test/naczepa',"
            " 'Krone', 'SD', 'PRZYCZEPA', ends_at, first_seen_at, last_seen_at"
            " FROM app.auction WHERE id = %s",
            (identyfikatory["audi-za-godzine"],),
        )

    domyslne = await _wszystkie_klucze(zapytania, Kryteria())
    assert "naczepa-krone" not in domyslne

    wszystkie = await _wszystkie_klucze(zapytania, Kryteria(rodzaje=()))
    assert "naczepa-krone" in wszystkie

    tylko_przyczepy = await _wszystkie_klucze(
        zapytania, Kryteria(rodzaje=(RodzajPojazdu.PRZYCZEPA,))
    )
    assert tylko_przyczepy == ["naczepa-krone"]


async def test_wybor_wielokrotny_laczy_wartosci_alternatywa(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """„Diesel ALBO benzyna" w jednym przejściu, a nie dwa przeglądania listy.

    Wewnątrz wymiaru wartości łączy OR, a wymiary między sobą AND — czyli
    „(diesel lub benzyna) ORAZ (Audi lub Volkswagen)".
    """
    zapytania = PgZapytania(pusta_baza)
    await _dane(pusta_baza)

    assert await _wszystkie_klucze(
        zapytania, Kryteria(paliwa=("diesel", "benzyna"))
    ) == ["audi-za-godzine", "vw-za-dwie-godziny", "audi-za-tydzien"]

    assert await _wszystkie_klucze(
        zapytania, Kryteria(marki=("Audi", "Volkswagen"), paliwa=("diesel",))
    ) == ["audi-za-godzine", "vw-za-dwie-godziny"]

    # Pusta krotka to brak filtru, a nie „żadna wartość nie pasuje".
    assert len(await _wszystkie_klucze(zapytania, Kryteria(paliwa=()))) == 6


async def test_zakres_mocy_silnika_zawezaja_liste(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    zapytania = PgZapytania(pusta_baza)
    identyfikatory = await _dane(pusta_baza)
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "UPDATE app.auction SET engine_hp = CASE external_id"
            " WHEN 'audi-za-godzine' THEN 190 WHEN 'audi-za-tydzien' THEN 150"
            " WHEN 'vw-za-dwie-godziny' THEN 120 END"
            " WHERE id = ANY(%s)",
            (
                [
                    identyfikatory[k]
                    for k in (
                        "audi-za-godzine",
                        "audi-za-tydzien",
                        "vw-za-dwie-godziny",
                    )
                ],
            ),
        )

    assert await _wszystkie_klucze(zapytania, Kryteria(moc_od=150)) == [
        "audi-za-godzine",
        "audi-za-tydzien",
    ]
    assert await _wszystkie_klucze(zapytania, Kryteria(moc_od=130, moc_do=180)) == [
        "audi-za-tydzien"
    ]


async def test_zakresy_suwakow_biora_sie_z_danych(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Granice suwaka mają pokrywać to, co w bazie jest — nie teorię.

    Suwak rocznika od 1900 do 2100 miałby cały ruch na trzech procentach
    długości i byłby nie do użycia.
    """
    zapytania = PgZapytania(pusta_baza)
    await _dane(pusta_baza)
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "UPDATE app.auction SET engine_hp = 150 WHERE engine_hp IS NULL"
        )

    zakresy = await zapytania.zakresy_filtrow()
    # Dolna granica rocznika jest STAŁA (1980), a nie brana z danych: suwak
    # zaczynający się od najstarszego zebranego rocznika przeskakiwałby przy
    # każdej nowej aukcji.
    assert (zakresy["rocznik"].minimum, zakresy["rocznik"].maksimum) == (1980, 2021)
    assert zakresy["moc"].minimum == 150
    assert zakresy["moc"].uzyteczny is False, "jedna wartość to nie jest zakres"


async def test_pusta_baza_nie_wywraca_zakresow(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    zakresy = await PgZapytania(pusta_baza).zakresy_filtrow()
    assert zakresy["rocznik"].uzyteczny is False
    assert zakresy["moc"].uzyteczny is False


async def _wystaw_ponownie(
    baza: psycopg.AsyncConnection, wzor: str, nowy: str, **zmiany: object
) -> int:
    """Kopia aukcji z nowym identyfikatorem — tak wygląda ponowne wystawienie."""
    kolumny = {
        "external_id": nowy,
        "url": f"https://przyklad.test/{nowy}",
        **zmiany,
    }
    ustawienia = ", ".join(f"{k} = %({k})s" for k in kolumny)
    async with baza.cursor() as cur:
        await cur.execute(
            "INSERT INTO app.auction"
            " (source_id, external_id, url, make, model, year, mileage_km, vin,"
            "  engine_ccm, engine_hp, color, price_current, currency, status,"
            "  ends_at, first_seen_at, last_seen_at, vehicle_kind)"
            " SELECT source_id, %(external_id)s, %(url)s, make, model, year,"
            "  mileage_km, vin, engine_ccm, engine_hp, color, price_current,"
            "  currency, status, ends_at, first_seen_at, last_seen_at,"
            "  vehicle_kind"
            " FROM app.auction WHERE external_id = %(wzor)s RETURNING id",
            {**kolumny, "wzor": wzor},
        )
        wiersz = await cur.fetchone()
        assert wiersz is not None
        nowe_id = wiersz[0]
        # Nadpisanie różnic osobno: `SELECT` kopiuje wzór, a dopiero to
        # odróżnia nowe wystawienie od starego.
        if zmiany:
            await cur.execute(
                f"UPDATE app.auction SET {ustawienia} WHERE id = %(id)s",
                {**kolumny, "id": nowe_id},
            )
    return int(nowe_id)


async def test_to_samo_auto_wystawione_ponownie_laczy_sie_po_vin(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Niesprzedany samochód wraca na aukcję — obie karty mają o sobie wiedzieć.

    Bez tego archiwum kłamie przez przemilczenie: pokazuje „zakończona"
    i nie mówi, że ta sama sztuka poszła miesiąc później taniej.
    """
    zapytania = PgZapytania(pusta_baza)
    identyfikatory = await _dane(pusta_baza)
    stare = identyfikatory["audi-za-godzine"]  # ma VIN
    nowe = await _wystaw_ponownie(
        pusta_baza,
        "audi-za-godzine",
        "audi-drugie-podejscie",
        ends_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
        price_current=Decimal("110000"),
    )

    z_perspektywy_starej = await zapytania.powiazane_wystawienia(stare)
    assert [w.id for w in z_perspektywy_starej] == [nowe]
    assert z_perspektywy_starej[0].pewnosc.value == "VIN"
    assert z_perspektywy_starej[0].pozniejsze is True

    # I w drugą stronę — powiązanie musi działać z obu kart.
    z_perspektywy_nowej = await zapytania.powiazane_wystawienia(nowe)
    assert [w.id for w in z_perspektywy_nowej] == [stare]
    assert z_perspektywy_nowej[0].pozniejsze is False


async def test_flota_identycznych_aut_nie_jest_jednym_samochodem(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Najgroźniejszy fałszywy alarm tego mechanizmu.

    Leasingodawca kupuje auta hurtem: ten sam model, rocznik i silnik,
    zbliżony przebieg. Bez VIN-u wystarczy RÓŻNICA PRZEBIEGU albo koloru,
    żeby to były dwa różne egzemplarze — i tak ma to działać.
    """
    zapytania = PgZapytania(pusta_baza)
    identyfikatory = await _dane(pusta_baza)
    bez_vinu = identyfikatory["vw-za-dwie-godziny"]
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "UPDATE app.auction SET color = 'Biały', engine_ccm = 1968,"
            " engine_hp = 150 WHERE id = %s",
            (bez_vinu,),
        )

    # Bliźniak z floty: wszystko to samo poza przebiegiem o 40 tys. wyższym.
    await _wystaw_ponownie(
        pusta_baza, "vw-za-dwie-godziny", "vw-blizniak", mileage_km=250_000
    )
    assert await zapytania.powiazane_wystawienia(bez_vinu) == ()

    # Ta sama sztuka po miesiącu: przebieg podrósł o 1200 km.
    ponownie = await _wystaw_ponownie(
        pusta_baza, "vw-za-dwie-godziny", "vw-ponownie", mileage_km=211_200
    )
    powiazane = await zapytania.powiazane_wystawienia(bez_vinu)
    assert [w.id for w in powiazane] == [ponownie]
    assert powiazane[0].pewnosc.value == "PODOBNE", "bez VIN-u to tylko przypuszczenie"


async def test_historia_licytacji_jest_widoczna_z_aplikacji(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Snapshoty zbierały się od pierwszego dnia, ale panel ich nie pokazywał.

    Kolejność malejąca: ostatnia zmiana ceny jest najważniejsza.
    """
    zapytania = PgZapytania(pusta_baza)
    identyfikatory = await _dane(pusta_baza)
    aukcja = identyfikatory["audi-za-godzine"]
    uow = PgUnitOfWork(pusta_baza)
    for minuty, kwota, oferty in (
        (30, "120000", 3),
        (20, "121500", 4),
        (5, "124000", 6),
    ):
        await uow.snapshot.zapisz_jesli_zmienil_sie(
            PriceSnapshot(
                auction_id=aukcja,
                ts=TERAZ - dt.timedelta(minutes=minuty),
                price=_pln(kwota),
                bid_count=oferty,
            )
        )

    historia = await zapytania.historia_cen(aukcja)
    assert [str(p.price.amount) for p in historia] == [
        "124000.00",
        "121500.00",
        "120000.00",
    ]
    assert historia[0].bid_count == 6


async def test_brak_historii_to_pusta_krotka(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    identyfikatory = await _dane(pusta_baza)
    assert (
        await PgZapytania(pusta_baza).historia_cen(identyfikatory["bez-terminu-a"])
        == ()
    )


async def test_oferty_na_karcie_dostaja_etykiety_w_kolejnosci_pojawienia(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.8 — karta pokazuje oferty, nie pseudonimy z bazy.

    Pseudonim jest szesnastkowym skrótem (`012_oferty.sql`) i na ekranie nie
    niósłby żadnej informacji. Litera niesie dokładnie tyle, ile trzeba: ilu
    było licytantów i który przebijał którego. Numeracja idzie po **pierwszym
    pojawieniu się** licytanta, nie po kwocie — inaczej etykiety zmieniałyby
    się przy każdej nowej ofercie.
    """
    zapytania = PgZapytania(pusta_baza)
    identyfikatory = await _dane(pusta_baza)
    aukcja_id = identyfikatory["audi-za-godzine"]
    uow = PgUnitOfWork(pusta_baza)

    # „aaa" wchodzi pierwszy, potem „bbb" przebija, potem „aaa" podbija.
    await uow.oferta.zapisz_nowe(
        [
            OfertaUczestnika(
                auction_id=aukcja_id,
                uczestnik=uczestnik,
                amount=_pln(kwota),
                placed_at=TERAZ - dt.timedelta(minutes=minuty),
                first_seen_at=TERAZ - dt.timedelta(minutes=minuty - 2),
            )
            for uczestnik, kwota, minuty in (
                ("aaa", "120000", 30),
                ("bbb", "121000", 20),
                ("aaa", "122000", 10),
            )
        ]
    )

    oferty = await zapytania.oferty(aukcja_id)

    # Od najnowszej — tak samo jak historia cen.
    assert [str(o.amount.amount) for o in oferty] == [
        "122000.00",
        "121000.00",
        "120000.00",
    ]
    assert [o.uczestnik for o in oferty] == [
        "Licytant A",
        "Licytant B",
        "Licytant A",
    ], "ten sam licytant ma tę samą etykietę w obu swoich ofertach"


async def test_opoznienie_liczy_sie_tylko_dla_ofert_po_rozpoczeciu_obserwacji(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Inaczej „opóźnienie" mierzyłoby wiek aukcji, a nie nasze spóźnienie.

    Ofertę złożoną przed odkryciem aukcji zobaczyliśmy dopiero przy pierwszym
    odpycie — różnica `first_seen_at - placed_at` byłaby wtedy liczbą dni,
    czytaną jak „spóźniliśmy się o tydzień". Kreska mówi prawdę.
    """
    zapytania = PgZapytania(pusta_baza)
    identyfikatory = await _dane(pusta_baza)
    aukcja_id = identyfikatory["audi-za-godzine"]  # first_seen_at = TERAZ minus 3 dni
    uow = PgUnitOfWork(pusta_baza)

    await uow.oferta.zapisz_nowe(
        [
            OfertaUczestnika(
                auction_id=aukcja_id,
                uczestnik="stary",
                amount=_pln("100000"),
                placed_at=TERAZ - dt.timedelta(days=5),
                first_seen_at=TERAZ - dt.timedelta(days=3),
            ),
            OfertaUczestnika(
                auction_id=aukcja_id,
                uczestnik="nowy",
                amount=_pln("130000"),
                placed_at=TERAZ - dt.timedelta(minutes=10),
                first_seen_at=TERAZ - dt.timedelta(minutes=8),
            ),
        ]
    )

    wedlug_uczestnika = {o.uczestnik: o for o in await zapytania.oferty(aukcja_id)}
    assert wedlug_uczestnika["Licytant B"].opoznienie_s == 120, "dwie minuty"
    assert wedlug_uczestnika["Licytant A"].opoznienie_s is None
