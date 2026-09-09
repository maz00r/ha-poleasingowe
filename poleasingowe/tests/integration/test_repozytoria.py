"""Testy repozytoriów na lokalnej bazie tymczasowej (SPEC.md §13, §14 pkt 3)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import psycopg
import pytest

from app.domain.entities import (
    Auction,
    OfertaUczestnika,
    PriceSnapshot,
    RunLog,
    Source,
    WatchlistEntry,
    WycenaAukcji,
)
from app.domain.enums import AuctionStatus, AuthState, Currency, FinalPriceState
from app.domain.value_objects import Mileage, Money, Vin
from app.infrastructure.persistence.repositories import PgUnitOfWork
from tests.conftest import wymaga_postgresa

pytestmark = wymaga_postgresa

TERAZ = dt.datetime(2026, 9, 6, 12, 0, tzinfo=dt.UTC)


def zrodlo(key: str = "efl", **nadpisz: object) -> Source:
    dane: dict[str, object] = {
        "key": key,
        "name": key.upper(),
        "enabled": True,
        "sweep_interval_seconds": 21600,
        "rate_limit_per_minute": 30,
        "floor_seconds": 60,
        "auth_state": AuthState.ANONYMOUS,
        "consecutive_auth_failures": 0,
        "overtime_window_seconds": 0,
        "overtime_extension_seconds": 0,
        "overtime_cap_seconds": None,
    }
    dane.update(nadpisz)
    return Source(**dane)  # type: ignore[arg-type]


def aukcja(source_id: int, external_id: str = "435508", **nadpisz: object) -> Auction:
    dane: dict[str, object] = {
        "source_id": source_id,
        "external_id": external_id,
        "url": f"https://aukcje.efl.com.pl/Auction/x-id{external_id}",
        "status": AuctionStatus.ACTIVE,
        "first_seen_at": TERAZ,
        "last_seen_at": TERAZ,
    }
    dane.update(nadpisz)
    return Auction(**dane)  # type: ignore[arg-type]


async def test_source_zapis_odczyt_i_upsert(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    uow = PgUnitOfWork(pusta_baza)
    zapisane = await uow.source.zapisz(zrodlo("efl"))
    assert zapisane.id is not None

    odczytane = await uow.source.po_kluczu("efl")
    assert odczytane == zapisane

    # Drugi zapis tego samego klucza ma AKTUALIZOWAC, nie duplikowac.
    zaktualizowane = await uow.source.zapisz(zrodlo("efl", name="EFL nowa nazwa"))
    assert zaktualizowane.id == zapisane.id
    assert zaktualizowane.name == "EFL nowa nazwa"


async def test_source_wlaczone_pomija_wylaczone(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    uow = PgUnitOfWork(pusta_baza)
    await uow.source.zapisz(zrodlo("efl"))
    await uow.source.zapisz(zrodlo("leasygroup", enabled=False))
    assert [z.key for z in await uow.source.wlaczone()] == ["efl"]


async def test_source_zapisuje_parametry_dogrywki(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.2 — dogrywka jest kolumną, nie stałą w kodzie."""
    uow = PgUnitOfWork(pusta_baza)
    # poleasingowe.pl: okno 30 s, +30 s, sufit 30 min (RECON.md §4.2)
    zapisane = await uow.source.zapisz(
        zrodlo(
            "poleasingowe",
            overtime_window_seconds=30,
            overtime_extension_seconds=30,
            overtime_cap_seconds=1800,
        )
    )
    assert zapisane.ma_dogrywke
    assert zapisane.overtime_cap_seconds == 1800

    # EFL nie ma dogrywki wcale (RECON.md §4.1)
    efl = await uow.source.zapisz(zrodlo("efl"))
    assert not efl.ma_dogrywke


async def test_auction_upsert_po_kluczu_naturalnym(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    uow = PgUnitOfWork(pusta_baza)
    src = await uow.source.zapisz(zrodlo())
    assert src.id is not None

    a = await uow.auction.zapisz(
        aukcja(
            src.id,
            price_current=Money(Decimal("48600.00"), Currency.PLN),
            bid_count=1,
            mileage=Mileage(180848),
            vin=Vin("WAUZZZGY2PA052888"),
            year=2022,
            make="Audi",
            model="A3",
        )
    )
    assert a.id is not None
    assert a.price_current == Money(Decimal("48600.00"), Currency.PLN)
    assert a.vin is not None and a.vin.value == "WAUZZZGY2PA052888"
    assert a.mileage == Mileage(180848)

    # Ta sama aukcja z nowa cena — ma sie zaktualizowac, nie zdublowac.
    b = await uow.auction.zapisz(
        aukcja(
            src.id, price_current=Money(Decimal("50000.00"), Currency.PLN), bid_count=2
        )
    )
    assert b.id == a.id
    assert b.bid_count == 2

    znalezione = await uow.auction.po_kluczu_naturalnym(src.id, "435508")
    assert znalezione is not None and znalezione.id == a.id


async def test_auction_do_odpytu_bierze_tylko_aktywne_i_sortuje_po_koncu(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.1 i §11.6 — priorytet ma bliższy `ends_at`."""
    uow = PgUnitOfWork(pusta_baza)
    src = await uow.source.zapisz(zrodlo())
    assert src.id is not None

    minelo = TERAZ - dt.timedelta(minutes=5)
    await uow.auction.zapisz(
        aukcja(
            src.id, "pozno", next_poll_at=minelo, ends_at=TERAZ + dt.timedelta(days=2)
        )
    )
    await uow.auction.zapisz(
        aukcja(
            src.id,
            "wczesnie",
            next_poll_at=minelo,
            ends_at=TERAZ + dt.timedelta(hours=1),
        )
    )
    # Zakonczona — nie ma prawa sie pojawic.
    await uow.auction.zapisz(
        aukcja(src.id, "zakonczona", next_poll_at=minelo, status=AuctionStatus.ENDED)
    )
    # Termin w przyszlosci — jeszcze nie teraz.
    await uow.auction.zapisz(
        aukcja(src.id, "pozniej", next_poll_at=TERAZ + dt.timedelta(hours=1))
    )

    do_odpytu = await uow.auction.do_odpytu(TERAZ, limit=10)
    assert [a.external_id for a in do_odpytu] == ["wczesnie", "pozno"]


async def test_auction_po_vin_znajduje_duplikaty_miedzy_zrodlami(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §8.4 — deduplikacja po VIN: oznaczamy, nie usuwamy."""
    uow = PgUnitOfWork(pusta_baza)
    a_src = await uow.source.zapisz(zrodlo("efl"))
    b_src = await uow.source.zapisz(zrodlo("poleasingowe"))
    assert a_src.id is not None and b_src.id is not None

    vin = Vin("TMBJH7NP0P7055920")
    pierwsza = await uow.auction.zapisz(aukcja(a_src.id, "111", vin=vin))
    await uow.auction.zapisz(aukcja(b_src.id, "222", vin=vin))

    znalezione = await uow.auction.po_vin(vin)
    assert len(znalezione) == 2
    assert {a.source_id for a in znalezione} == {a_src.id, b_src.id}
    assert pierwsza.id is not None


async def test_odnotuj_widziana_zmienia_tylko_last_seen(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §8.4 — odpyt bez zmiany aktualizuje tylko `last_seen_at`."""
    uow = PgUnitOfWork(pusta_baza)
    src = await uow.source.zapisz(zrodlo())
    assert src.id is not None
    a = await uow.auction.zapisz(
        aukcja(src.id, price_current=Money(Decimal("100.00"), Currency.PLN))
    )
    assert a.id is not None

    pozniej = TERAZ + dt.timedelta(minutes=30)
    await uow.auction.odnotuj_widziana(a.id, pozniej)

    po = await uow.auction.po_kluczu_naturalnym(src.id, a.external_id)
    assert po is not None
    assert po.last_seen_at == pozniej
    assert po.first_seen_at == a.first_seen_at
    assert po.price_current == a.price_current


# --------------------------------------------------------------------------
# price_snapshot — reguła §8.4 i bid_gap z §11.8
# --------------------------------------------------------------------------


async def _aukcja_do_snapshotow(uow: PgUnitOfWork) -> int:
    src = await uow.source.zapisz(zrodlo())
    assert src.id is not None
    a = await uow.auction.zapisz(aukcja(src.id))
    assert a.id is not None
    return a.id


def _snap(auction_id: int, cena: str, **nadpisz: object) -> PriceSnapshot:
    dane: dict[str, object] = {
        "auction_id": auction_id,
        "ts": TERAZ,
        "price": Money(Decimal(cena), Currency.PLN),
    }
    dane.update(nadpisz)
    return PriceSnapshot(**dane)  # type: ignore[arg-type]


async def test_snapshot_nie_zapisuje_sie_bez_zmiany(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §8.4 — to jest reguła, która chroni instancję przed zalaniem.

    Bez niej dogrywka generuje setki identycznych wierszy na aukcję.
    """
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_snapshotow(uow)

    pierwszy = await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, "1000", bid_count=1)
    )
    assert pierwszy is not None

    identyczny = await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, "1000", bid_count=1, ts=TERAZ + dt.timedelta(minutes=1))
    )
    assert identyczny is None, "identyczny stan nie ma prawa utworzyć wiersza"

    assert len(await uow.snapshot.historia(aid)) == 1


@pytest.mark.parametrize(
    "zmiana",
    [
        {"cena": "1100", "bid_count": 1},
        {"cena": "1000", "bid_count": 2},
        {"cena": "1000", "bid_count": 1, "ends_at": TERAZ + dt.timedelta(minutes=2)},
    ],
    ids=["zmiana ceny", "zmiana liczby ofert", "przesunięty ends_at"],
)
async def test_snapshot_zapisuje_sie_przy_kazdej_z_trzech_zmian(
    pusta_baza: psycopg.AsyncConnection, zmiana: dict[str, object]
) -> None:
    """SPEC.md §8.4 wymienia trzy wyzwalacze: cena, liczba ofert, `ends_at`."""
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_snapshotow(uow)
    await uow.snapshot.zapisz_jesli_zmienil_sie(_snap(aid, "1000", bid_count=1))

    cena = str(zmiana.pop("cena"))
    drugi = await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, cena, ts=TERAZ + dt.timedelta(minutes=1), **zmiana)
    )
    assert drugi is not None
    assert len(await uow.snapshot.historia(aid)) == 2


async def test_bid_gap_pierwszy_snapshot_ma_null_nie_zero(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.8 — zero znaczyłoby „komplet", a to byłoby kłamstwo.

    W chwili pierwszej obserwacji aukcja mogła już mieć oferty.
    """
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_snapshotow(uow)
    pierwszy = await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, "1000", bid_count=7)
    )
    assert pierwszy is not None
    assert pierwszy.bid_gap is None


async def test_bid_gap_liczy_przegapione_oferty(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.8 — przyrost `bid_count` ponad 1 to oferty,
    których nie widzieliśmy.
    """
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_snapshotow(uow)

    await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, "1000", bid_count=1), licznik_liczy_oferty=True
    )

    # Kolejna oferta po kolei — nic nie przegapilismy.
    kolejny = await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, "1100", bid_count=2, ts=TERAZ + dt.timedelta(minutes=1)),
        licznik_liczy_oferty=True,
    )
    assert kolejny is not None and kolejny.bid_gap == 0

    # Skok z 2 na 5 — trzy oferty, z czego dwie przegapione.
    skok = await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, "1400", bid_count=5, ts=TERAZ + dt.timedelta(minutes=2)),
        licznik_liczy_oferty=True,
    )
    assert skok is not None and skok.bid_gap == 2

    suma = sum(s.bid_gap or 0 for s in await uow.snapshot.historia(aid))
    assert suma == 2, "suma bid_gap to liczba ofert, których nie widzieliśmy"


async def test_bid_gap_jest_null_gdy_licznik_nie_liczy_ofert(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.8 — dla `PARTICIPANTS` i `UNKNOWN` właściwą odpowiedzią
    jest `NULL`.

    EFL prowadzi licytację proxy: `bid_count` liczy tam uczestników, nie
    oferty. Przyrost tego licznika o 3 nie znaczy „przegapiliśmy dwie
    oferty" — znaczy „doszło trzech licytantów". Liczba wyliczona mimo to
    wygląda wiarygodnie i **dlatego** jest groźniejsza niż jej brak.
    """
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_snapshotow(uow)

    await uow.snapshot.zapisz_jesli_zmienil_sie(_snap(aid, "1000", bid_count=1))
    skok = await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, "1400", bid_count=5, ts=TERAZ + dt.timedelta(minutes=2))
    )

    assert skok is not None
    assert skok.bid_gap is None, "brak dowodu, że licznik liczy oferty → NULL"


async def test_bid_gap_jest_null_gdy_serwis_nie_podaje_liczby_ofert(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """autoprzetarg.pl nie podaje `bid_count` bez logowania (RECON.md §4.4).

    Bez `bid_count` nie ma z czego liczyć przyrostu — `bid_gap` musi zostać
    `None`, a nie udawać zero.
    """
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_snapshotow(uow)
    await uow.snapshot.zapisz_jesli_zmienil_sie(_snap(aid, "1000"))
    drugi = await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, "1100", ts=TERAZ + dt.timedelta(minutes=1))
    )
    assert drugi is not None and drugi.bid_gap is None


# --------------------------------------------------------------------------
# watchlist, run_log, Unit of Work
# --------------------------------------------------------------------------


async def test_watchlist_dodaj_sprawdz_usun(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_snapshotow(uow)

    assert not await uow.watchlist.obserwowana(aid)
    wpis = await uow.watchlist.dodaj(
        WatchlistEntry(
            auction_id=aid,
            added_at=TERAZ,
            note="sprawdzić lakier",
            target_price=Money(Decimal("45000.00"), Currency.PLN),
        )
    )
    assert wpis.id is not None
    assert wpis.target_price == Money(Decimal("45000.00"), Currency.PLN)
    assert await uow.watchlist.obserwowana(aid)

    assert await uow.watchlist.usun(aid)
    assert not await uow.watchlist.obserwowana(aid)
    assert not await uow.watchlist.usun(aid), "drugie usunięcie nie ma nic do zrobienia"


async def test_watchlist_jedna_aukcja_raz(pusta_baza: psycopg.AsyncConnection) -> None:
    """SPEC.md §8.3 — UNIQUE na `auction_id`."""
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_snapshotow(uow)
    a = await uow.watchlist.dodaj(WatchlistEntry(auction_id=aid, added_at=TERAZ))
    b = await uow.watchlist.dodaj(
        WatchlistEntry(auction_id=aid, added_at=TERAZ, note="druga próba")
    )
    assert a.id == b.id
    assert b.note == "druga próba"


async def test_run_log_mierzy_przebieg(pusta_baza: psycopg.AsyncConnection) -> None:
    """SPEC.md §13 — budżet z §1.1 ma być mierzalny, nie deklaratywny."""
    uow = PgUnitOfWork(pusta_baza)
    src = await uow.source.zapisz(zrodlo())
    assert src.id is not None

    wpis = await uow.run_log.rozpocznij(RunLog(source_id=src.id, started_at=TERAZ))
    assert wpis.id is not None and wpis.finished_at is None

    from dataclasses import replace

    zamkniety = await uow.run_log.zakoncz(
        replace(
            wpis,
            finished_at=TERAZ + dt.timedelta(seconds=42),
            new_count=3,
            changed_count=5,
            error_count=1,
            errors=["SourceUnavailable: timeout"],
            rss_bytes=142_000_000,
            database_bytes=5_000_000,
        )
    )
    assert zamkniety.id == wpis.id
    assert zamkniety.new_count == 3
    assert zamkniety.errors == ["SourceUnavailable: timeout"]
    assert zamkniety.rss_bytes == 142_000_000


async def test_unit_of_work_wycofuje_wszystko_przy_wyjatku(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §6.2 — commit na wyjściu, rollback na wyjątku."""

    class WlasnyBlad(RuntimeError):
        pass

    uow = PgUnitOfWork(pusta_baza)
    with pytest.raises(WlasnyBlad):
        async with uow:
            await uow.source.zapisz(zrodlo("efl"))
            await uow.source.zapisz(zrodlo("leasygroup"))
            raise WlasnyBlad

    assert await uow.source.po_kluczu("efl") is None
    assert await uow.source.po_kluczu("leasygroup") is None


async def test_unit_of_work_zapisuje_na_wyjsciu(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    uow = PgUnitOfWork(pusta_baza)
    async with uow:
        await uow.source.zapisz(zrodlo("efl"))
    assert await uow.source.po_kluczu("efl") is not None


async def test_source_zapisuje_parametry_domkniecia(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.5, §11.8 — drabinka i semantyka licznika też są kolumnami.

    Round-trip przez bazę, bo `integer[]` idzie tam jako lista, a wraca
    do encji jako krotka — miejsce, w którym łatwo o cichy rozjazd typów.
    """
    from app.domain.enums import BidCountSemantics

    uow = PgUnitOfWork(pusta_baza)

    # autoprzetarg.pl: cena znika 10-15 s po końcu (RECON.md §3.6)
    ap = await uow.source.zapisz(
        zrodlo(
            "autoprzetarg",
            closing_ladder_seconds=(2, 5, 8, 11, 14),
            bid_count_semantics=BidCountSemantics.OFFERS,
        )
    )
    assert ap.closing_ladder_seconds == (2, 5, 8, 11, 14)
    assert ap.liczy_oferty

    # EFL: strona zamrożona po końcu, ale licznik zlicza uczestników (§3.5)
    efl = await uow.source.zapisz(
        zrodlo(
            "efl",
            closing_ladder_seconds=(2, 30),
            bid_count_semantics=BidCountSemantics.PARTICIPANTS,
        )
    )
    assert not efl.liczy_oferty, "licytacja proxy — bid_gap zostaje NULL"

    # poleasingowe: cena trzyma się bezterminowo, historia ofert nie (§3.6)
    pl = await uow.source.zapisz(
        zrodlo(
            "poleasingowe",
            closing_ladder_seconds=(2, 30),
            bid_history_ttl_seconds=120,
        )
    )
    assert pl.bid_history_ttl_seconds == 120

    odczytane = await uow.source.po_kluczu("autoprzetarg")
    assert odczytane is not None
    assert odczytane.closing_ladder_seconds == (2, 5, 8, 11, 14)


async def test_aukcja_po_terminie_przestaje_byc_aktywna(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Zgłoszenie z użytkowania: zakończona aukcja siedziała w „Aktywne".

    Mechanizm: aukcji nieobserwowanej nie odpytujemy po raz drugi (§11.2),
    a marker końca stoi wyłącznie na stronie szczegółów — więc bez zegara
    status `ACTIVE` nie miał jak się nigdy zmienić.
    """
    uow = PgUnitOfWork(pusta_baza)
    # Karencja liczy się z okna dogrywki źródła: poleasingowe.pl przedłuża
    # aukcję maksymalnie o pół godziny, więc przed jej upływem „po terminie"
    # nie znaczy jeszcze „zakończona".
    zapisane = await uow.source.zapisz(
        zrodlo(
            "poleasingowe",
            overtime_window_seconds=30,
            overtime_extension_seconds=30,
            overtime_cap_seconds=1800,
        )
    )
    assert zapisane.id is not None
    teraz = TERAZ + dt.timedelta(days=1)

    swieżo_po = await uow.auction.zapisz(
        aukcja(
            zapisane.id,
            "swiezo-po-terminie",
            ends_at=teraz - dt.timedelta(minutes=20),
            last_seen_at=teraz - dt.timedelta(minutes=25),
        )
    )
    dawno_po = await uow.auction.zapisz(
        aukcja(
            zapisane.id,
            "dawno-po-terminie",
            ends_at=teraz - dt.timedelta(hours=3),
            last_seen_at=teraz - dt.timedelta(hours=4),
        )
    )
    trwajaca = await uow.auction.zapisz(
        aukcja(zapisane.id, "trwa", ends_at=teraz + dt.timedelta(hours=2))
    )
    bez_terminu = await uow.auction.zapisz(aukcja(zapisane.id, "bez-terminu"))

    assert await uow.auction.zamknij_po_terminie(teraz) == 1

    async def stan(auction_id: int | None) -> Auction:
        assert auction_id is not None
        wynik = await uow.auction.po_kluczu_naturalnym(
            zapisane.id,  # type: ignore[arg-type]
            next(
                a.external_id
                for a in (swieżo_po, dawno_po, trwajaca, bez_terminu)
                if a.id == auction_id
            ),
        )
        assert wynik is not None
        return wynik

    zamknieta = await stan(dawno_po.id)
    assert zamknieta.status is AuctionStatus.ENDED
    # Ceny po zamknięciu nikt nie odczytał, więc to dolne oszacowanie —
    # nigdy `CONFIRMED` (SPEC.md §8.2, §11.5).
    assert zamknieta.final_price_state is FinalPriceState.LAST_SEEN
    assert zamknieta.last_price_lead_seconds == 3600
    assert zamknieta.next_poll_at is None

    # W oknie dogrywki jeszcze nie zamykamy: aukcja mogła zostać przedłużona.
    assert (await stan(swieżo_po.id)).status is AuctionStatus.ACTIVE
    assert (await stan(trwajaca.id)).status is AuctionStatus.ACTIVE
    # Bez `ends_at` nie ma czego mierzyć — zgadywanie byłoby gorsze.
    assert (await stan(bez_terminu.id)).status is AuctionStatus.ACTIVE


async def test_zrodlo_bez_dogrywki_nie_czeka_godziny_na_zamkniecie(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Zgłoszenie z użytkowania: „przez pewien czas widać zakończone w Aktywne".

    Karencja była liczona z `overtime_cap_seconds`, a ta jest `NULL` zarówno
    dla źródła **z nieznanym** limitem dogrywki, jak i dla źródła, które
    dogrywki **nie ma wcale**. `COALESCE(..., 3600)` traktował oba przypadki
    tak samo, więc zakończona aukcja EFL siedziała w „Aktywne" 70 minut —
    i to na samej górze, bo lista jest domyślnie sortowana po najbliższym
    terminie.

    EFL nie przedłuża aukcji: `ends_at` jest twardy (RECON.md §3.2). Nie ma
    więc czego przeczekiwać poza karencją na dryf zegara.
    """
    uow = PgUnitOfWork(pusta_baza)
    efl = await uow.source.zapisz(
        zrodlo(
            "efl",
            overtime_window_seconds=0,
            overtime_extension_seconds=0,
            overtime_cap_seconds=None,
        )
    )
    assert efl.id is not None
    teraz = TERAZ + dt.timedelta(days=1)

    po_karencji = await uow.auction.zapisz(
        aukcja(
            efl.id,
            "efl-20-minut-po",
            ends_at=teraz - dt.timedelta(minutes=20),
            last_seen_at=teraz - dt.timedelta(minutes=25),
        )
    )
    # Wciąż w karencji — tu nie zamykamy, bo `ends_at` pochodzi z ostatniego
    # odpytu i mógł się rozjechać z zegarem serwisu (SPEC.md §11.7).
    w_karencji = await uow.auction.zapisz(
        aukcja(
            efl.id,
            "efl-piec-minut-po",
            ends_at=teraz - dt.timedelta(minutes=5),
            last_seen_at=teraz - dt.timedelta(minutes=10),
        )
    )

    assert await uow.auction.zamknij_po_terminie(teraz) == 1

    async def stan(external_id: str) -> Auction:
        wynik = await uow.auction.po_kluczu_naturalnym(
            efl.id,  # type: ignore[arg-type]
            external_id,
        )
        assert wynik is not None
        return wynik

    assert (await stan(po_karencji.external_id)).status is AuctionStatus.ENDED
    assert (await stan(w_karencji.external_id)).status is AuctionStatus.ACTIVE


async def test_zegar_nie_zamyka_aukcji_w_trakcie_drabinki(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Zamknięcie z zegara nie ma prawa uciąć fazy 2 (SPEC.md §11.5).

    To ostatnie stopnie drabinki łapią potwierdzenie zakończenia — EFL
    dopisuje je 5-7 minut po terminie. Gdyby zegar zamknął aukcję wcześniej,
    `CONFIRMED` stałoby się nieosiągalne, a stan wyglądałby na policzony.
    """
    uow = PgUnitOfWork(pusta_baza)
    efl = await uow.source.zapisz(
        zrodlo(
            "efl",
            overtime_window_seconds=0,
            overtime_extension_seconds=0,
            overtime_cap_seconds=None,
            # Drabinka dłuższa niż karencja — celowo, bo to ten przypadek
            # graniczny psuł się po cichu.
            closing_ladder_seconds=(2, 30, 1200),
        )
    )
    assert efl.id is not None
    teraz = TERAZ + dt.timedelta(days=1)

    w_drabince = await uow.auction.zapisz(
        aukcja(
            efl.id,
            "efl-drabinka-trwa",
            ends_at=teraz - dt.timedelta(minutes=15),
            last_seen_at=teraz - dt.timedelta(minutes=15),
        )
    )

    assert await uow.auction.zamknij_po_terminie(teraz) == 0

    wynik = await uow.auction.po_kluczu_naturalnym(efl.id, w_drabince.external_id)
    assert wynik is not None
    assert wynik.status is AuctionStatus.ACTIVE, "drabinka ma dobiec do końca"


async def test_wycena_ai_zapisuje_sie_na_stale(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Wycena ma przetrwać i **nie zmieniać się sama** (SPEC.md §12).

    Wcześniej leżała w cache'u na dysku kluczowanym danymi wejściowymi —
    razem z porównaniami z zakończonych aukcji. Każda kolejna zakończona
    aukcja tego modelu zmieniała klucz, więc karta liczyła wycenę od nowa:
    inna kwota i kolejne płatne żądanie przy każdym wejściu.
    """
    uow = PgUnitOfWork(pusta_baza)
    zapisane_zrodlo = await uow.source.zapisz(zrodlo("efl"))
    assert zapisane_zrodlo.id is not None
    zapisana_aukcja = await uow.auction.zapisz(aukcja(zapisane_zrodlo.id))
    assert zapisana_aukcja.id is not None

    assert await uow.wycena.dla_aukcji(zapisana_aukcja.id) is None

    await uow.wycena.zapisz(
        WycenaAukcji(
            auction_id=zapisana_aukcja.id,
            wartosc=Money(Decimal("78000"), Currency.PLN),
            minimum=Money(Decimal("72000"), Currency.PLN),
            maksimum=Money(Decimal("84000"), Currency.PLN),
            cena_portale=Money(Decimal("91000"), Currency.PLN),
            pewnosc="średnia",
            uzasadnienie="Dwie podobne aukcje.",
            zalozenia=("Brak poważnych szkód.",),
            model="model-testowy",
            wersja_promptu=2,
            utworzono=TERAZ,
        )
    )

    odczytana = await uow.wycena.dla_aukcji(zapisana_aukcja.id)
    assert odczytana is not None
    assert odczytana.wartosc.amount == Decimal("78000.00")
    assert odczytana.cena_portale is not None
    assert odczytana.zalozenia == ("Brak poważnych szkód.",)
    assert odczytana.model == "model-testowy"
    assert odczytana.utworzono == TERAZ


async def test_przeliczenie_nadpisuje_poprzednia_wycene(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Jedna wycena na aukcję. Dwie różniłyby się kwotą i nie byłoby
    wiadomo, która obowiązuje."""
    uow = PgUnitOfWork(pusta_baza)
    zapisane_zrodlo = await uow.source.zapisz(zrodlo("efl"))
    assert zapisane_zrodlo.id is not None
    zapisana_aukcja = await uow.auction.zapisz(aukcja(zapisane_zrodlo.id))
    identyfikator = zapisana_aukcja.id
    assert identyfikator is not None

    def wycena(kwota: str, kiedy: dt.datetime) -> WycenaAukcji:
        return WycenaAukcji(
            auction_id=identyfikator,
            wartosc=Money(Decimal(kwota), Currency.PLN),
            minimum=Money(Decimal(kwota), Currency.PLN),
            maksimum=Money(Decimal(kwota), Currency.PLN),
            pewnosc="niska",
            uzasadnienie="—",
            zalozenia=(),
            model="m",
            wersja_promptu=2,
            utworzono=kiedy,
        )

    await uow.wycena.zapisz(wycena("70000", TERAZ))
    await uow.wycena.zapisz(wycena("81000", TERAZ + dt.timedelta(days=1)))

    odczytana = await uow.wycena.dla_aukcji(identyfikator)
    assert odczytana is not None
    assert odczytana.wartosc.amount == Decimal("81000.00")

    async with pusta_baza.cursor() as cur:
        await cur.execute("SELECT count(*) FROM app.ai_valuation")
        wiersz = await cur.fetchone()
    assert wiersz is not None and wiersz[0] == 1


async def test_niespojna_wycena_nie_wchodzi_do_bazy(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Ostatnia linia obrony: `minimum > wartosc` wygląda wiarygodnie, więc
    nie ma prawa wejść nawet, gdyby kod to przepuścił."""
    uow = PgUnitOfWork(pusta_baza)
    zapisane_zrodlo = await uow.source.zapisz(zrodlo("efl"))
    assert zapisane_zrodlo.id is not None
    zapisana_aukcja = await uow.auction.zapisz(aukcja(zapisane_zrodlo.id))
    assert zapisana_aukcja.id is not None

    with pytest.raises(psycopg.errors.CheckViolation):
        async with pusta_baza.cursor() as cur:
            await cur.execute(
                "INSERT INTO app.ai_valuation (auction_id, value_amount,"
                " min_amount, max_amount, confidence, rationale, model,"
                " prompt_version)"
                " VALUES (%s, 100, 200, 300, 'niska', '—', 'm', 2)",
                (zapisana_aukcja.id,),
            )


# --------------------------------------------------------------------------
# Oferty odczytane wprost ze strony aukcji (SPEC.md §11.8)
# --------------------------------------------------------------------------


def _oferta(
    auction_id: int,
    uczestnik: str,
    kwota: str,
    *,
    zlozona: dt.datetime,
    widziana: dt.datetime = TERAZ,
) -> OfertaUczestnika:
    return OfertaUczestnika(
        auction_id=auction_id,
        uczestnik=uczestnik,
        amount=Money(Decimal(kwota), Currency.PLN),
        placed_at=zlozona,
        first_seen_at=widziana,
    )


async def _aukcja_do_ofert(uow: PgUnitOfWork) -> int:
    src = await uow.source.zapisz(zrodlo())
    assert src.id is not None
    a = await uow.auction.zapisz(aukcja(src.id))
    assert a.id is not None
    return a.id


async def test_oferty_zapisuja_sie_i_wracaja_chronologicznie(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_ofert(uow)

    nowe = await uow.oferta.zapisz_nowe(
        [
            _oferta(aid, "aaa", "51600", zlozona=TERAZ - dt.timedelta(minutes=5)),
            _oferta(aid, "bbb", "49000", zlozona=TERAZ - dt.timedelta(minutes=1)),
        ]
    )
    assert nowe == 2

    wszystkie = await uow.oferta.dla_aukcji(aid)
    assert [str(o.amount.amount) for o in wszystkie] == ["51600.00", "49000.00"]


async def test_ta_sama_oferta_nie_duplikuje_sie_przy_powtorzonym_odpycie(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.5 — w fazie domknięcia ta sama strona wraca po kilka razy.

    Sześć odpytów w kilkadziesiąt sekund to sześć identycznych list ofert.
    Bez klucza naturalnego karta aukcji pokazywałaby tę samą ofertę sześć razy
    i wyglądałoby to jak sześć postąpień.
    """
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_ofert(uow)
    zlozona = TERAZ - dt.timedelta(minutes=5)

    pierwszy = await uow.oferta.zapisz_nowe(
        [_oferta(aid, "aaa", "51600", zlozona=zlozona)]
    )
    drugi = await uow.oferta.zapisz_nowe(
        [_oferta(aid, "aaa", "51600", zlozona=zlozona)]
    )

    assert (pierwszy, drugi) == (1, 0)
    assert len(await uow.oferta.dla_aukcji(aid)) == 1


async def test_podniesiona_oferta_tego_samego_licytanta_to_nowy_wiersz(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """To jest ta rzecz, której serwis o sobie nie pamięta (`012_oferty.sql`).

    EFL trzyma jeden wiersz na uczestnika i **nadpisuje** go: kod 106125 miał
    48 600 zł 4.09, a 7.09 już 51 600 zł — starszej kwoty nie widać nigdzie.
    U nas zostają obie, bo różni je moment złożenia.
    """
    uow = PgUnitOfWork(pusta_baza)
    aid = await _aukcja_do_ofert(uow)

    await uow.oferta.zapisz_nowe(
        [_oferta(aid, "aaa", "48600", zlozona=TERAZ - dt.timedelta(days=3))]
    )
    await uow.oferta.zapisz_nowe(
        [_oferta(aid, "aaa", "51600", zlozona=TERAZ - dt.timedelta(minutes=5))]
    )

    kwoty = [str(o.amount.amount) for o in await uow.oferta.dla_aukcji(aid)]
    assert kwoty == ["48600.00", "51600.00"], "archiwum pamięta więcej niż serwis"
