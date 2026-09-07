"""Wspólne fixtures testów.

SPEC.md §13: testy repozytoriów chodzą **wyłącznie** na lokalnym PostgreSQL 17
i tworzą sobie własną bazę tymczasową. Nigdy na instancji
`db21ed7f-postgres-latest` — tam mieszka TeslaMate, którego danych nie da się
odtworzyć z żadnego innego źródła (§2).
"""

from __future__ import annotations

import os
import pathlib
import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
import pytest_asyncio

MIGRACJE = pathlib.Path(__file__).resolve().parent.parent / "app" / "migrations"

# Host bazy do testow. Celowo NIE czytamy tu opcji add-onu ani niczego, co
# moglo by wskazac na instancje produkcyjna — domyslnie zawsze localhost.
HOST = os.environ.get("POLEASINGOWE_TEST_HOST", "localhost")
PORT = int(os.environ.get("POLEASINGOWE_TEST_PORT", "5432"))
USER = os.environ.get("POLEASINGOWE_TEST_USER", "poleasingowe_app")
HASLO = os.environ.get("POLEASINGOWE_TEST_PASSWORD", "poleasingowe_test")

# Nazwy hostow, na ktorych testy nie moga dzialac pod zadnym pozorem.
ZAKAZANE_HOSTY = ("db21ed7f-postgres-latest",)


def _dsn(baza: str) -> str:
    return f"postgresql://{USER}:{HASLO}@{HOST}:{PORT}/{baza}"


def _postgres_dostepny() -> bool:
    try:
        with psycopg.connect(_dsn("postgres"), connect_timeout=3):
            return True
    except psycopg.Error:
        return False


wymaga_postgresa = pytest.mark.skipif(
    not _postgres_dostepny(),
    reason=(
        "brak lokalnego PostgreSQL — uruchom "
        "`brew services start postgresql@17 && ./scripts/setup-lokalny-postgres.sh`"
    ),
)


@pytest.fixture(scope="session", autouse=True)
def nigdy_nie_produkcja() -> None:
    """Twarda bariera: testy nie mają prawa dotknąć instancji z TeslaMate."""
    if any(zly in HOST for zly in ZAKAZANE_HOSTY):
        pytest.exit(
            f"STOP: testy wskazują na {HOST}, czyli instancję współdzieloną "
            "z TeslaMate (SPEC.md §2, §13). Przerywam.",
            returncode=1,
        )


@pytest.fixture(scope="session")
def nazwa_bazy_testowej() -> Iterator[str]:
    """Tworzy bazę tymczasową na czas sesji i kasuje ją na końcu."""
    nazwa = f"poleasingowe_test_{uuid.uuid4().hex[:12]}"
    _utworz_baze(nazwa)
    try:
        yield nazwa
    finally:
        _skasuj_baze(nazwa)


def _utworz_baze(nazwa: str) -> None:
    with psycopg.connect(_dsn("postgres"), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{nazwa}"')
    with psycopg.connect(_dsn(nazwa), autocommit=True) as conn:
        # Schematy powstaja POZA migracja, tak jak na produkcji (SPEC.md §0).
        conn.execute("CREATE SCHEMA app")
        conn.execute("CREATE SCHEMA reporting")
        # Odwzorowanie uprawnien z §0: PUBLIC odebrane na bazie i na
        # schemacie public, grafana_ro ma WYLACZNIE CONNECT. Dzieki temu
        # test "Grafana nie widzi tabel bazowych" cos naprawde sprawdza.
        conn.execute(f'REVOKE ALL ON DATABASE "{nazwa}" FROM PUBLIC')
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        conn.execute(f'GRANT CONNECT ON DATABASE "{nazwa}" TO grafana_ro')


def _skasuj_baze(nazwa: str) -> None:
    with psycopg.connect(_dsn("postgres"), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (nazwa,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{nazwa}"')


@pytest_asyncio.fixture
async def baza_od_zera() -> AsyncIterator[psycopg.AsyncConnection]:
    """Osobna, dziewicza baza na jeden test — bez zastosowanych migracji.

    Baza sesyjna do tego nie wystarczy: `pusta_baza` commituje TRUNCATE,
    a razem z nim commituje migracje zastosowane wczesniej na tym samym
    polaczeniu. Test „migracje stosuja sie od zera i sa idempotentne" stawal
    sie wtedy zalezny od kolejnosci plikow w katalogu — i faktycznie pekl,
    gdy doszedl plik sortujacy sie przed nim alfabetycznie.
    """
    nazwa = f"poleasingowe_migracje_{uuid.uuid4().hex[:12]}"
    _utworz_baze(nazwa)
    try:
        async with await psycopg.AsyncConnection.connect(_dsn(nazwa)) as conn:
            yield conn
    finally:
        _skasuj_baze(nazwa)


@pytest_asyncio.fixture
async def polaczenie(
    nazwa_bazy_testowej: str,
) -> AsyncIterator[psycopg.AsyncConnection]:
    """Świeże połączenie do bazy testowej, z rollbackiem po teście."""
    async with await psycopg.AsyncConnection.connect(_dsn(nazwa_bazy_testowej)) as conn:
        yield conn
        await conn.rollback()


@pytest_asyncio.fixture
async def polaczenie_grafany(
    nazwa_bazy_testowej: str,
) -> AsyncIterator[psycopg.AsyncConnection]:
    """Połączenie jako `grafana_ro` — do sprawdzania kontraktu z §9."""
    dsn = f"postgresql://grafana_ro:{HASLO}@{HOST}:{PORT}/{nazwa_bazy_testowej}"
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        yield conn


@pytest_asyncio.fixture
async def pusta_baza(
    polaczenie: psycopg.AsyncConnection,
) -> AsyncIterator[psycopg.AsyncConnection]:
    """Baza z zastosowanymi migracjami i pustymi tabelami."""
    from app.infrastructure.persistence.migrations import zastosuj_migracje

    await zastosuj_migracje(polaczenie, MIGRACJE)
    async with polaczenie.transaction():
        await polaczenie.execute(
            "TRUNCATE app.price_snapshot, app.watchlist, app.run_log, "
            "app.auction, app.saved_filter, app.source RESTART IDENTITY CASCADE"
        )
    yield polaczenie
