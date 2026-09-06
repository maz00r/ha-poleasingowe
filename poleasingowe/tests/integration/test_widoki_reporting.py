"""Widoki w `reporting` jako kontrakt dla Grafany (SPEC.md §9, §14 pkt 4).

Widoki powstają **zanim pojawią się dane** — pusty widok, który działa, jest
lepszy niż dashboard budowany na danych produkcyjnych.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import psycopg
import pytest

from app.domain.entities import RunLog, WatchlistEntry
from app.domain.enums import AuctionStatus, Currency, FinalPriceState
from app.domain.value_objects import Money, Vin
from app.infrastructure.persistence.repositories import PgUnitOfWork
from tests.conftest import wymaga_postgresa
from tests.integration.test_repozytoria import TERAZ, aukcja, zrodlo

pytestmark = wymaga_postgresa

WIDOKI = [
    "v_price_history",
    "v_auction_current",
    "v_market_stats",
    "v_source_health",
]


@pytest.mark.parametrize("widok", WIDOKI)
async def test_widok_istnieje_i_dziala_na_pustej_bazie(
    pusta_baza: psycopg.AsyncConnection, widok: str
) -> None:
    """SPEC.md §14 pkt 4 — weryfikacja, że Grafana zobaczy PUSTE widoki."""
    async with pusta_baza.cursor() as cur:
        await cur.execute(f"SELECT count(*) FROM reporting.{widok}")
        wiersz = await cur.fetchone()
    assert wiersz is not None and wiersz[0] == 0


@pytest.mark.parametrize("widok", WIDOKI)
async def test_grafana_widzi_widoki(
    pusta_baza: psycopg.AsyncConnection,
    polaczenie_grafany: psycopg.AsyncConnection,
    widok: str,
) -> None:
    async with polaczenie_grafany.cursor() as cur:
        await cur.execute(f"SELECT count(*) FROM reporting.{widok}")
        wiersz = await cur.fetchone()
    assert wiersz is not None


@pytest.mark.parametrize(
    "tabela",
    ["auction", "price_snapshot", "source", "watchlist", "run_log", "saved_filter"],
)
async def test_grafana_nie_widzi_tabel_bazowych(
    pusta_baza: psycopg.AsyncConnection,
    polaczenie_grafany: psycopg.AsyncConnection,
    tabela: str,
) -> None:
    """SPEC.md §9 — Grafana **nigdy** nie odpytuje tabel bazowych.

    Gdyby dashboardy podpięły się pod `app.auction`, schemat byłby zamrożony
    i każda migracja po cichu psułaby wykresy.
    """
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with polaczenie_grafany.cursor() as cur:
            await cur.execute(f"SELECT 1 FROM app.{tabela} LIMIT 1")
    await polaczenie_grafany.rollback()


async def test_v_auction_current_pokazuje_flage_obserwowania(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    uow = PgUnitOfWork(pusta_baza)
    src = await uow.source.zapisz(zrodlo())
    assert src.id is not None
    obserwowana = await uow.auction.zapisz(aukcja(src.id, "obserwowana"))
    await uow.auction.zapisz(aukcja(src.id, "zwykla"))
    assert obserwowana.id is not None
    await uow.watchlist.dodaj(
        WatchlistEntry(
            auction_id=obserwowana.id,
            added_at=TERAZ,
            note="sprawdzić lakier",
            target_price=Money(Decimal("45000.00"), Currency.PLN),
        )
    )

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT external_id, watched, watch_note, source_key "
            "FROM reporting.v_auction_current ORDER BY external_id"
        )
        wiersze = await cur.fetchall()

    assert wiersze == [
        ("obserwowana", True, "sprawdzić lakier", "efl"),
        ("zwykla", False, None, "efl"),
    ]


async def test_v_market_stats_nie_miesza_confirmed_z_last_seen(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §9 — to jest cały powód, dla którego ten widok przepisaliśmy.

    `LAST_SEEN` to dolne oszacowanie (§11.5). Wrzucone do jednej mediany
    z `CONFIRMED` zaniżałoby obraz rynku.
    """
    uow = PgUnitOfWork(pusta_baza)
    src = await uow.source.zapisz(zrodlo())
    assert src.id is not None

    wspolne: dict[str, object] = {
        "make": "Audi",
        "model": "A4",
        "year": 2022,
        "status": AuctionStatus.ENDED,
        "price_start": Money(Decimal("40000.00"), Currency.PLN),
    }
    # Dwie potwierdzone: 60 000 i 80 000 -> mediana 70 000.
    for i, cena in enumerate(("60000.00", "80000.00")):
        await uow.auction.zapisz(
            aukcja(
                src.id,
                f"conf{i}",
                price_current=Money(Decimal(cena), Currency.PLN),
                final_price_state=FinalPriceState.CONFIRMED,
                **wspolne,
            )
        )
    # Dwie niepewne, wyraznie nizsze: 10 000 i 20 000 -> mediana 15 000.
    for i, (cena, lead) in enumerate((("10000.00", 90), ("20000.00", 30))):
        await uow.auction.zapisz(
            aukcja(
                src.id,
                f"last{i}",
                price_current=Money(Decimal(cena), Currency.PLN),
                final_price_state=FinalPriceState.LAST_SEEN,
                last_price_lead_seconds=lead,
                **wspolne,
            )
        )

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT median_confirmed, median_last_seen, n_confirmed, n_last_seen, "
            "median_lead_seconds FROM reporting.v_market_stats "
            "WHERE make = 'Audi' AND model = 'A4' AND year = 2022"
        )
        wiersz = await cur.fetchone()

    assert wiersz is not None
    mediana_conf, mediana_last, n_conf, n_last, mediana_lead = wiersz
    assert mediana_conf == Decimal("70000.00")
    assert mediana_last == Decimal("15000.00")
    assert (n_conf, n_last) == (2, 2)
    assert mediana_lead == 60
    # Gdyby widok mieszal obie grupy, mediana wyszlaby 40 000.
    assert mediana_conf != Decimal("40000.00")


async def test_v_market_stats_pomija_duplikaty_i_trwajace(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §8.4 — duplikaty po VIN oznaczamy, ale nie liczymy dwa razy."""
    uow = PgUnitOfWork(pusta_baza)
    src = await uow.source.zapisz(zrodlo())
    assert src.id is not None

    oryginal = await uow.auction.zapisz(
        aukcja(
            src.id,
            "oryginal",
            make="Skoda",
            model="Superb",
            year=2023,
            status=AuctionStatus.ENDED,
            final_price_state=FinalPriceState.CONFIRMED,
            price_current=Money(Decimal("50000.00"), Currency.PLN),
            vin=Vin("TMBJH7NP0P7055920"),
        )
    )
    assert oryginal.id is not None
    await uow.auction.zapisz(
        aukcja(
            src.id,
            "duplikat",
            make="Skoda",
            model="Superb",
            year=2023,
            status=AuctionStatus.ENDED,
            final_price_state=FinalPriceState.CONFIRMED,
            price_current=Money(Decimal("999999.00"), Currency.PLN),
            vin=Vin("TMBJH7NP0P7055920"),
            duplicate_of=oryginal.id,
        )
    )
    # Trwajaca — nie ma ceny koncowej, wiec nie ma prawa wejsc do statystyk.
    await uow.auction.zapisz(
        aukcja(
            src.id,
            "trwajaca",
            make="Skoda",
            model="Superb",
            year=2023,
            price_current=Money(Decimal("1.00"), Currency.PLN),
        )
    )

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT n_confirmed, median_confirmed FROM reporting.v_market_stats "
            "WHERE make = 'Skoda'"
        )
        wiersz = await cur.fetchone()

    assert wiersz is not None
    assert wiersz[0] == 1, "duplikat i trwająca nie mają prawa się liczyć"
    assert wiersz[1] == Decimal("50000.00")


async def test_v_source_health_pokazuje_ostatni_przebieg(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §13 — RSS i rozmiar bazy mają być widoczne, nie deklarowane."""
    uow = PgUnitOfWork(pusta_baza)
    src = await uow.source.zapisz(
        zrodlo(
            "poleasingowe",
            overtime_window_seconds=30,
            overtime_extension_seconds=30,
            overtime_cap_seconds=1800,
        )
    )
    assert src.id is not None
    await uow.auction.zapisz(aukcja(src.id, "aktywna"))

    from dataclasses import replace

    stary = await uow.run_log.rozpocznij(
        RunLog(source_id=src.id, started_at=TERAZ - dt.timedelta(hours=2))
    )
    await uow.run_log.zakoncz(replace(stary, finished_at=TERAZ, new_count=99))
    nowy = await uow.run_log.rozpocznij(RunLog(source_id=src.id, started_at=TERAZ))
    await uow.run_log.zakoncz(
        replace(
            nowy,
            finished_at=TERAZ + dt.timedelta(seconds=10),
            new_count=3,
            rss_bytes=142_000_000,
            database_bytes=5_000_000,
        )
    )

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT last_run_new, last_run_rss_bytes, aktywne_aukcje, "
            "overtime_window_seconds, overtime_cap_seconds "
            "FROM reporting.v_source_health WHERE source_key = 'poleasingowe'"
        )
        wiersz = await cur.fetchone()

    assert wiersz is not None
    assert wiersz[0] == 3, "ma pokazywać NAJNOWSZY przebieg, nie pierwszy"
    assert wiersz[1] == 142_000_000
    assert wiersz[2] == 1
    assert (wiersz[3], wiersz[4]) == (30, 1800)


async def test_v_price_history_niesie_bid_gap(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.8 — kompletność historii ofert ma być widoczna w Grafanie."""
    from app.domain.entities import PriceSnapshot

    uow = PgUnitOfWork(pusta_baza)
    src = await uow.source.zapisz(zrodlo())
    assert src.id is not None
    a = await uow.auction.zapisz(aukcja(src.id))
    assert a.id is not None

    await uow.snapshot.zapisz_jesli_zmienil_sie(
        PriceSnapshot(
            auction_id=a.id,
            ts=TERAZ,
            price=Money(Decimal("1000.00"), Currency.PLN),
            bid_count=1,
        )
    )
    await uow.snapshot.zapisz_jesli_zmienil_sie(
        PriceSnapshot(
            auction_id=a.id,
            ts=TERAZ + dt.timedelta(minutes=1),
            price=Money(Decimal("1400.00"), Currency.PLN),
            bid_count=4,
        )
    )

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT bid_gap FROM reporting.v_price_history "
            "WHERE auction_id = %s ORDER BY ts",
            (a.id,),
        )
        wiersze = [w[0] for w in await cur.fetchall()]

    assert wiersze == [None, 2], "pierwszy snapshot NULL, potem dwie przegapione"
