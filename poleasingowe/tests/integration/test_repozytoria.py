"""Testy repozytoriów na lokalnej bazie tymczasowej (SPEC.md §13, §14 pkt 3)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import psycopg
import pytest

from app.domain.entities import Auction, PriceSnapshot, RunLog, Source, WatchlistEntry
from app.domain.enums import AuctionStatus, AuthState, Currency
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

    await uow.snapshot.zapisz_jesli_zmienil_sie(_snap(aid, "1000", bid_count=1))

    # Kolejna oferta po kolei — nic nie przegapilismy.
    kolejny = await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, "1100", bid_count=2, ts=TERAZ + dt.timedelta(minutes=1))
    )
    assert kolejny is not None and kolejny.bid_gap == 0

    # Skok z 2 na 5 — trzy oferty, z czego dwie przegapione.
    skok = await uow.snapshot.zapisz_jesli_zmienil_sie(
        _snap(aid, "1400", bid_count=5, ts=TERAZ + dt.timedelta(minutes=2))
    )
    assert skok is not None and skok.bid_gap == 2

    suma = sum(s.bid_gap or 0 for s in await uow.snapshot.historia(aid))
    assert suma == 2, "suma bid_gap to liczba ofert, których nie widzieliśmy"


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
