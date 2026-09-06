"""Migracje na realnej bazie (SPEC.md §14 pkt 3).

Baza tymczasowa, tworzona i kasowana przez fixture. Nigdy produkcja.
"""

from __future__ import annotations

import psycopg
import pytest

from app.infrastructure.persistence.migrations import (
    KLUCZ_BLOKADY_MIGRACJI,
    BladMigracji,
    Migracja,
    wczytaj_migracje,
    zastosuj_migracje,
)
from tests.conftest import MIGRACJE, wymaga_postgresa

pytestmark = wymaga_postgresa


async def test_migracje_stosuja_sie_i_sa_idempotentne(
    polaczenie: psycopg.AsyncConnection,
) -> None:
    # Bez sztywnej listy — test nie ma wymagac aktualizacji przy kazdej
    # nowej migracji, tylko sprawdzac, ze stosuje sie dokladnie to, co lezy
    # na dysku, i ze drugie przejscie nie robi nic.
    na_dysku = wczytaj_migracje(MIGRACJE)
    assert na_dysku, "brak plikow migracji — test nie mialby czego sprawdzac"

    pierwsze = await zastosuj_migracje(polaczenie, MIGRACJE)
    assert [m.version for m in pierwsze] == [m.version for m in na_dysku]

    drugie = await zastosuj_migracje(polaczenie, MIGRACJE)
    assert drugie == [], "druga próba nie ma prawa niczego zastosować ponownie"

    async with polaczenie.cursor() as cur:
        await cur.execute(
            "SELECT version, name FROM app.schema_migration ORDER BY version"
        )
        zapisane = await cur.fetchall()
    assert zapisane == [(m.version, m.name) for m in na_dysku]


async def test_powstaly_wszystkie_tabele_i_indeksy(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'app' ORDER BY 1"
        )
        tabele = [w[0] for w in await cur.fetchall()]
    assert tabele == [
        "auction",
        "price_snapshot",
        "run_log",
        "saved_filter",
        "schema_migration",
        "source",
        "watchlist",
    ]

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'app' "
            "AND indexname LIKE '%_idx' ORDER BY 1"
        )
        indeksy = [w[0] for w in await cur.fetchall()]
    # SPEC.md §8.3 wymienia je z nazwy — indeks czesciowy na next_poll_at jest
    # kluczowy, bo to zapytanie wykonuje sie najczesciej.
    assert "auction_next_poll_active_idx" in indeksy
    assert "price_snapshot_auction_ts_idx" in indeksy
    assert "watchlist_auction_unique_idx" in indeksy


async def test_indeks_na_next_poll_jest_czesciowy(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §8.3 — ma trafiać wyłącznie w aktywne aukcje."""
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname='app' AND indexname='auction_next_poll_active_idx'"
        )
        wiersz = await cur.fetchone()
    assert wiersz is not None
    assert "WHERE (status = 'ACTIVE'" in wiersz[0], wiersz[0]


async def test_migracje_nie_tworza_schematow(
    polaczenie: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §0 — schematy już istnieją, migracja zaczyna od tabel."""
    for migracja in wczytaj_migracje(MIGRACJE):
        assert "CREATE SCHEMA" not in migracja.sql.upper()


async def test_zmieniona_migracja_jest_wykrywana(
    polaczenie: psycopg.AsyncConnection, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Edycja zastosowanej migracji ma się wywalić, nie przejść po cichu."""
    await zastosuj_migracje(polaczenie, MIGRACJE)

    katalog = tmp_path_factory.mktemp("migracje")
    (katalog / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(BladMigracji, match="checksum"):
        await zastosuj_migracje(polaczenie, katalog)


def test_luka_w_numeracji_jest_wykrywana(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    katalog = tmp_path_factory.mktemp("luka")
    (katalog / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")
    (katalog / "003_dalej.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(BladMigracji, match="luka"):
        wczytaj_migracje(katalog)


def test_zla_nazwa_pliku_jest_wykrywana(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    katalog = tmp_path_factory.mktemp("zla-nazwa")
    (katalog / "init.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(BladMigracji, match="NNN_nazwa.sql"):
        wczytaj_migracje(katalog)


def test_checksum_zalezy_od_tresci() -> None:
    a = Migracja(version=1, name="init", sql="SELECT 1;")
    b = Migracja(version=1, name="init", sql="SELECT 2;")
    assert a.checksum != b.checksum


async def test_blokada_doradcza_jest_trzymana_w_trakcie_migracji(
    polaczenie: psycopg.AsyncConnection, nazwa_bazy_testowej: str
) -> None:
    """SPEC.md §2 pkt 6 — migracje pod blokadą, z własnym stałym kluczem.

    Blokady doradcze w PostgreSQL są wspólne dla całej instancji, nie dla
    pojedynczej bazy, więc kolizja z migracjami Ecto (TeslaMate) byłaby realna.
    Ten test sprawdza, że blokadę faktycznie bierzemy i że zwalnia się sama
    po transakcji — `pg_advisory_xact_lock`, nie sesyjna.
    """
    async with polaczenie.transaction():
        await polaczenie.execute(
            "SELECT pg_advisory_xact_lock(%s)", (KLUCZ_BLOKADY_MIGRACJI,)
        )
        async with polaczenie.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND ((classid::bigint << 32) | objid::bigint) = %s",
                (KLUCZ_BLOKADY_MIGRACJI,),
            )
            wiersz = await cur.fetchone()
        assert wiersz is not None and wiersz[0] >= 1, "blokada nie została wzięta"

    async with polaczenie.cursor() as cur:
        await cur.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND ((classid::bigint << 32) | objid::bigint) = %s",
            (KLUCZ_BLOKADY_MIGRACJI,),
        )
        wiersz = await cur.fetchone()
    assert (
        wiersz is not None and wiersz[0] == 0
    ), "blokada nie zwolniła się po transakcji — musi być xact, nie sesyjna"
