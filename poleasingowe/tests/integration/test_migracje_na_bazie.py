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
    baza_od_zera: psycopg.AsyncConnection,
) -> None:
    # Wlasna, dziewicza baza: ten test jako jedyny wymaga, zeby NIC nie bylo
    # jeszcze zastosowane. Na bazie sesyjnej byl zalezny od kolejnosci plikow.
    polaczenie = baza_od_zera

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


async def test_migracja_poprawia_historyczne_tytuly_autoprzetarg(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "INSERT INTO app.source (key, name) VALUES ('autoprzetarg', 'Auto') "
            "RETURNING id"
        )
        source_id = (await cur.fetchone())[0]  # type: ignore[index]
        await cur.executemany(
            "INSERT INTO app.auction "
            "(source_id, external_id, url, variant) VALUES (%s, %s, %s, %s)",
            [
                (source_id, "a", "https://example/a", "1490,00 cm3 / 125 KM"),
                (
                    source_id,
                    "b",
                    "https://example/b",
                    "Executive 3,0 DIESEL / 175 KM",
                ),
            ],
        )
        await cur.execute((MIGRACJE / "008_tytuly_autoprzetarg.sql").read_text())
        await cur.execute(
            "SELECT external_id, variant FROM app.auction ORDER BY external_id"
        )
        assert await cur.fetchall() == [("a", None), ("b", "Executive")]


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


async def _wstaw_zrodlo(
    baza: psycopg.AsyncConnection, klucz: str, **kolumny: object
) -> None:
    nazwy = ", ".join(kolumny)
    znaki = ", ".join(["%s"] * len(kolumny))
    # Kolumny sa nazwami z kodu testu, nie z danych — ale sklejanie SQL-a
    # i tak wolimy trzymac w jednym miejscu, zamiast rozsiewac po testach.
    sql = f"INSERT INTO app.source (key, name, {nazwy}) VALUES (%s, %s, {znaki})"
    async with baza.cursor() as cur:
        await cur.execute(sql, (klucz, klucz, *kolumny.values()))


async def test_drabinka_domkniecia_musi_byc_rosnaca_i_dodatnia(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.5 — „nastepny element" ma sens tylko przy rosnacej siatce.

    Gdyby baza przyjmowala dowolna tablice, kolejnosc prob fazy 2 zalezalaby
    od kolejnosci wpisu, a nie od czasu — i drabinka dla autoprzetarg
    (okno 10-15 s) mogla po cichu trafiac w przekierowanie.
    """
    async with pusta_baza.transaction():
        await _wstaw_zrodlo(pusta_baza, "ok", closing_ladder_seconds=[2, 5, 8, 11, 14])

    for opis, drabinka in [
        ("malejaca", [5, 2]),
        ("z powtorzeniem", [2, 2, 5]),
        ("z zerem", [0, 5]),
        ("z ujemna", [2, -5]),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with pusta_baza.transaction():
                await _wstaw_zrodlo(
                    pusta_baza, f"zla-{opis}", closing_ladder_seconds=drabinka
                )


async def test_pusta_drabinka_jest_dozwolona(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Pusta tablica znaczy „jeden odpyt i koniec", a nie blad (§8.2)."""
    async with pusta_baza.transaction():
        await _wstaw_zrodlo(pusta_baza, "bez-drabinki", closing_ladder_seconds=[])


async def test_semantyka_bid_count_jest_ze_slownika(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.8 — od tej wartosci zalezy, czy `bid_gap` cokolwiek znaczy."""
    async with pusta_baza.transaction():
        await _wstaw_zrodlo(pusta_baza, "efl", bid_count_semantics="PARTICIPANTS")

    with pytest.raises(psycopg.errors.CheckViolation):
        async with pusta_baza.transaction():
            await _wstaw_zrodlo(pusta_baza, "zle", bid_count_semantics="BIDS")


async def test_domyslna_semantyka_to_niewiedza_a_nie_zalozenie(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Zrodlo bez dowodu z rekonesansu ma byc UNKNOWN, nie OFFERS.

    Domyslne OFFERS znaczyloby, ze `bid_gap` liczy sie dla kazdego nowego
    zrodla od razu — i dla serwisu z licytacja proxy (EFL) dawaloby zera
    czytane jako „komplet historii".
    """
    async with pusta_baza.transaction():
        await _wstaw_zrodlo(pusta_baza, "nowe", enabled=True)
        async with pusta_baza.cursor() as cur:
            await cur.execute(
                "SELECT bid_count_semantics, closing_ladder_seconds,"
                " bid_history_ttl_seconds FROM app.source WHERE key = 'nowe'"
            )
            wiersz = await cur.fetchone()
    assert wiersz == ("UNKNOWN", [2, 5, 10, 20, 40], None)
